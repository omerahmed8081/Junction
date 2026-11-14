"""Why the kitchen's agent did what it did.

The other half of the audit trail. The gateway's log answers "what did Aimo
tell this agent"; this one answers "why did my agent buy that" - the sentence
somebody typed, what the model made of it, what the code decided and on what
grounds, and who approved it.

It is the kitchen's own record and stays in the kitchen's schema
(`kitchen.audit_log`, sql/audit.sql). Aimo never reads it. `crossed_gateway`
marks the few rows that did leave the building, which is what drives the "what
Aimo sees" toggle on the Activity tab: most of this never does.

Append-only, and it falls back to memory when the database is away rather than
failing the request - saying so, because a silent gap in an audit trail is
worse than a loud one.
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

FALLBACK_LIMIT = 2000
# How long to keep writing to memory before trying the database again.
RETRY_AFTER_SECONDS = 20.0
MAX_TEXT = 500


def quote_id_of(token: str | None) -> str:
    """The id inside a signed quote, for joining this log to the gateway's.

    Reads the payload without verifying, which is only ever safe for a log
    line. Deliberately here rather than imported from gateway/: the kitchen
    does not import the gateway's code, or the separation is only a diagram.
    """
    if not token:
        return ""
    from shared.signing import read_payload_unverified
    return str(read_payload_unverified(token).get("jti") or "")


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[:MAX_TEXT] + "..."
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value[:50]]
    return value


@dataclass
class KitchenRecord:
    at: datetime
    step: str
    basket_id: str = ""
    actor: str = "agent"            # agent | human | schedule
    approver_role: str = ""
    detail: str = ""
    reason: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    parser: str = ""
    quote_jti: str = ""
    order_id: str = ""
    crossed_gateway: bool = False
    record_id: int | None = None

    def as_row(self) -> dict[str, Any]:
        return {
            "at": self.at, "basket_id": self.basket_id, "actor": self.actor,
            "approver_role": self.approver_role, "step": self.step,
            "detail": self.detail, "reason": self.reason,
            "inputs": json.dumps(_clip(self.inputs), default=str),
            "result": json.dumps(_clip(self.result), default=str),
            "parser": self.parser, "quote_jti": self.quote_jti,
            "order_id": self.order_id, "crossed_gateway": self.crossed_gateway,
        }

    def as_json(self) -> dict[str, Any]:
        row = {k: v for k, v in self.__dict__.items() if k != "record_id"}
        row["at"] = self.at.isoformat()
        row["inputs"] = _clip(self.inputs)
        row["result"] = _clip(self.result)
        row["id"] = self.record_id
        return row


INSERT = """
INSERT INTO kitchen.audit_log
    (at, basket_id, actor, approver_role, step, detail, reason, inputs, result,
     parser, quote_jti, order_id, crossed_gateway)
VALUES
    (%(at)s, %(basket_id)s, %(actor)s, %(approver_role)s, %(step)s, %(detail)s,
     %(reason)s, %(inputs)s, %(result)s, %(parser)s, %(quote_jti)s, %(order_id)s,
     %(crossed_gateway)s)
RETURNING id
"""

SELECT = """
SELECT id, at, basket_id, actor, approver_role, step, detail, reason, inputs,
       result, parser, quote_jti, order_id, crossed_gateway
FROM kitchen.audit_log
WHERE (%(basket_id)s = '' OR basket_id = %(basket_id)s)
  AND (%(order_id)s = '' OR order_id = %(order_id)s)
  AND (%(crossed_only)s IS FALSE OR crossed_gateway IS TRUE)
ORDER BY id DESC
LIMIT %(limit)s
"""


class KitchenAudit:
    def __init__(self, pool: Any = None) -> None:
        self._pool = pool
        self._fallback: deque[KitchenRecord] = deque(maxlen=FALLBACK_LIMIT)
        self.degraded = False
        self.last_error = ""
        self._failed_at = 0.0
        self._recent: tuple[str, str, str] | None = None

    def forget_memory(self) -> int:
        """Drop the rows held in memory. For the labelled demo reset only.

        When the database is unreachable, rows go into `_fallback` and `recent`
        reads them from there. A reset truncates the table but the process keeps
        running, so without this the cleared log reappears - which is how the
        frontend ended up showing audit rows for orders that no longer existed.
        """
        held = len(self._fallback)
        self._fallback.clear()
        self._recent = None      # the de-duplicator, or the next identical
                                 # step after a reset would be swallowed
        return held

    def forget_everything(self) -> int:
        """Delete every row, in the database as well as in memory.

        For the labelled demo reset only. An audit log is append-only by
        design and nothing in production would call this - but the demo reset
        clears the orders and the baskets, and a log that outlives them is
        worse than no log at all: the Activity page showed a full history of
        accepted orders beside "No orders yet", which is exactly the
        disagreement between two views that this project exists to rule out.
        `scripts/clean_slate.py` already truncates this table; this is the
        same clean slate from the button.

        There is no tenant column because one service is one kitchen, so every
        row here belongs to the kitchen doing the resetting.
        """
        gone = self.forget_memory()
        if self._pool is None:
            return gone
        try:
            with self._pool.connection(timeout=5.0) as conn:
                deleted = conn.execute("DELETE FROM kitchen.audit_log").rowcount
            return gone + max(deleted, 0)
        except Exception as exc:        # noqa: BLE001
            print(f"[kitchen] the audit rows were not deleted: {type(exc).__name__}: {exc}")
            return gone

    def _worth_retrying(self) -> bool:
        """Aiven's free tier powers off when idle and takes about half a minute
        to wake, so one timeout must not send us to memory until a restart."""
        import time
        return not self.degraded or (time.monotonic() - self._failed_at) > RETRY_AFTER_SECONDS

    def set_pool(self, pool: Any) -> None:
        self._pool = pool

    def record(self, step: str, **fields: Any) -> KitchenRecord:
        fields.setdefault("at", datetime.now(timezone.utc))
        entry = KitchenRecord(step=step, **fields)
        # The same step can reach here twice when a caller records a list of
        # steps that a later caller records again. A log that repeats itself is
        # a log people stop reading.
        signature = (step, entry.detail, entry.basket_id)
        if signature == self._recent:
            return entry
        self._recent = signature
        if self._pool is not None:
            try:
                with self._pool.connection(timeout=2.0) as conn:
                    row = conn.execute(INSERT, entry.as_row()).fetchone()
                    entry.record_id = row[0] if row else None
                self.degraded = False
                return entry
            except Exception as exc:                 # noqa: BLE001
                import time
                self.degraded = True
                self._failed_at = time.monotonic()
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                print(f"[kitchen] audit log fell back to memory: {self.last_error}")
        self._fallback.append(entry)
        return entry

    def record_steps(self, steps: list[Any], basket_id: str, **common: Any) -> None:
        """The agent's own steps, as the agent already produces them."""
        for step in steps or ():
            self.record(
                step=getattr(step, "step", str(step)),
                basket_id=basket_id,
                detail=getattr(step, "detail", ""),
                crossed_gateway=bool(getattr(step, "crossed_gateway", False)),
                at=getattr(step, "at", None) or datetime.now(timezone.utc),
                **common)

    def recent(self, limit: int = 200, basket_id: str = "", order_id: str = "",
               crossed_only: bool = False) -> list[dict[str, Any]]:
        if self._pool is not None and self._worth_retrying():
            try:
                with self._pool.connection(timeout=5.0) as conn:
                    # Scoped to a cursor, never set on the connection: the pool
                    # reuses connections, so that would change how everyone
                    # else's rows come back.
                    from psycopg.rows import dict_row
                    cursor = conn.cursor(row_factory=dict_row)
                    rows = cursor.execute(SELECT, {
                        "basket_id": basket_id, "order_id": order_id,
                        "crossed_only": crossed_only, "limit": limit}).fetchall()
                return [_row_as_json(r) for r in rows]
            except Exception as exc:                 # noqa: BLE001
                import time
                self.degraded = True
                self._failed_at = time.monotonic()
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
        kept = [e for e in reversed(self._fallback)
                if (not basket_id or e.basket_id == basket_id)
                and (not order_id or e.order_id == order_id)
                and (not crossed_only or e.crossed_gateway)]
        return [e.as_json() for e in kept[:limit]]

    def __len__(self) -> int:
        return len(self._fallback)


def _row_as_json(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    at = out.get("at")
    out["at"] = at.isoformat() if hasattr(at, "isoformat") else str(at)
    for column in ("inputs", "result"):
        value = out.get(column)
        if isinstance(value, str):
            try:
                out[column] = json.loads(value)
            except Exception:                        # noqa: BLE001
                pass
    return out


log = KitchenAudit()
