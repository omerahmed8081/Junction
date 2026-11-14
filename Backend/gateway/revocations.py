"""Mandates the customer has called back.

A mandate is a signed statement that stands on its own: anyone holding the
token can present it, and it verifies until its own expiry. So "stop the agent"
cannot be something the kitchen only does to its own copy - a stolen or stale
mandate would keep working for the rest of the week. It has to be Aimo that
refuses, which means Aimo has to be told.

That is all this is: the ids of mandates that must no longer be accepted, kept
on the gateway's side, checked on every order.

Kept in Postgres rather than memory on purpose. A revocation is a safety
control, and one that quietly forgets on restart is worse than none, because
the screen still says the agent was stopped. When the database cannot be
reached the list stays in memory and `degraded` says so, which is a bad state
to be in but a visible one.

Revoking is final. A mandate is not un-revoked; the owner issues a new one,
which gets a new id.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

RETRY_AFTER_SECONDS = 20.0


@dataclass(frozen=True)
class Revocation:
    mandate_jti: str
    customer_id: str
    at: datetime
    reason: str = ""
    revoked_by: str = ""

    def as_json(self) -> dict[str, Any]:
        return {"mandate_jti": self.mandate_jti, "customer_id": self.customer_id,
                "at": self.at.isoformat(), "reason": self.reason,
                "revoked_by": self.revoked_by}


INSERT = """
INSERT INTO gateway.revoked_mandates (mandate_jti, customer_id, at, reason, revoked_by)
VALUES (%(mandate_jti)s, %(customer_id)s, %(at)s, %(reason)s, %(revoked_by)s)
ON CONFLICT (mandate_jti) DO NOTHING
"""

SELECT_ONE = "SELECT 1 FROM gateway.revoked_mandates WHERE mandate_jti = %(mandate_jti)s"

SELECT_FOR_CUSTOMER = """
SELECT mandate_jti, customer_id, at, reason, revoked_by
FROM gateway.revoked_mandates
WHERE customer_id = %(customer_id)s
ORDER BY at DESC
LIMIT %(limit)s
"""


class RevocationList:
    """Append-only. `pool` is a psycopg pool, or None to stay in memory."""

    def __init__(self, pool: Any = None) -> None:
        self._pool = pool
        self._memory: dict[str, Revocation] = {}
        self.degraded = False
        self.last_error = ""
        self._failed_at = 0.0

    def set_pool(self, pool: Any) -> None:
        self._pool = pool

    def _worth_retrying(self) -> bool:
        import time
        return not self.degraded or (time.monotonic() - self._failed_at) > RETRY_AFTER_SECONDS

    def _failed(self, exc: Exception) -> None:
        import time
        self.degraded = True
        self._failed_at = time.monotonic()
        self.last_error = f"{type(exc).__name__}: {exc}"[:200]
        print(f"[gateway] revocation list fell back to memory: {self.last_error}")

    def revoke(self, mandate_jti: str, customer_id: str, *, reason: str = "",
               revoked_by: str = "", at: datetime | None = None) -> Revocation:
        """Called back, from now on. Revoking twice is not an error."""
        entry = Revocation(mandate_jti=mandate_jti, customer_id=customer_id,
                           at=(at or datetime.now(timezone.utc)).replace(microsecond=0),
                           reason=reason, revoked_by=revoked_by)
        # Memory first, always: if the database write fails, the mandate is
        # still refused by this process rather than silently accepted.
        self._memory[mandate_jti] = entry
        if self._pool is not None:
            try:
                with self._pool.connection(timeout=3.0) as conn:
                    conn.execute(INSERT, {
                        "mandate_jti": entry.mandate_jti, "customer_id": entry.customer_id,
                        "at": entry.at, "reason": entry.reason, "revoked_by": entry.revoked_by})
                self.degraded = False
            except Exception as exc:                 # noqa: BLE001
                self._failed(exc)
        return entry

    def is_revoked(self, mandate_jti: str) -> bool:
        """Checked on every order, so it must never raise and never guess.

        The memory copy is checked first: it is the one written on the way in,
        and it cannot be wrong about something revoked in this process. Only a
        mandate it has not heard of is looked up in the database, where another
        instance - or this one before a restart - may have recorded it.
        """
        if not mandate_jti:
            return False
        if mandate_jti in self._memory:
            return True
        if self._pool is None or not self._worth_retrying():
            return False
        try:
            with self._pool.connection(timeout=3.0) as conn:
                found = conn.execute(SELECT_ONE, {"mandate_jti": mandate_jti}).fetchone()
            self.degraded = False
            return bool(found)
        except Exception as exc:                     # noqa: BLE001
            self._failed(exc)
            # Not revoked as far as we can tell. Saying "revoked" because the
            # database is down would refuse every order in the building.
            return False

    def for_customer(self, customer_id: str, limit: int = 50) -> list[dict[str, Any]]:
        if self._pool is not None and self._worth_retrying():
            try:
                with self._pool.connection(timeout=3.0) as conn:
                    from psycopg.rows import dict_row
                    # On a cursor, never the connection: the pool shares it.
                    cursor = conn.cursor(row_factory=dict_row)
                    rows = cursor.execute(SELECT_FOR_CUSTOMER, {
                        "customer_id": customer_id, "limit": limit}).fetchall()
                self.degraded = False
                return [{**r, "at": r["at"].isoformat() if hasattr(r["at"], "isoformat")
                         else str(r["at"])} for r in rows]
            except Exception as exc:                 # noqa: BLE001
                self._failed(exc)
        return [e.as_json() for e in
                sorted((e for e in self._memory.values() if e.customer_id == customer_id),
                       key=lambda e: e.at, reverse=True)[:limit]]

    def __len__(self) -> int:
        return len(self._memory)


# The list the gateway uses. main.py hands it the pool once the pool is open.
revoked = RevocationList()
