"""What Aimo saw: an append-only record of every call the gateway served.

This is the half of the audit trail that belongs to Valio. It answers "what did
we tell this agent, and when" without holding anything about why the kitchen
asked - the kitchen keeps its own log, and the two join on `quote_jti` and
`order_id`. Keeping them apart is what makes "kitchen data stays with the
kitchen" true in the code rather than only on a slide.

Append-only on purpose: rows are inserted, never updated or deleted. A record
that can be edited after the fact is not evidence.

Rows go to `gateway.audit_log` (sql/audit.sql). If the database is unreachable
the log falls back to memory so a demo never dies for want of a log line, and
says so in `degraded`, because a silent gap in an audit trail is worse than a
loud one.

Nothing secret is written: the API key is stored as a short hash, and a signed
token is recorded by its id and issuer, never its bytes.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from shared.signing import read_payload_unverified

# Kept in memory only when the database cannot be reached.
FALLBACK_LIMIT = 2000
# Aiven's free tier powers off when idle and takes about half a minute to wake,
# so one timeout must not send us to memory until the service is restarted.
RETRY_AFTER_SECONDS = 20.0
# How much of a free-text field we keep. Enough to see what was asked.
MAX_TEXT = 500


def key_fingerprint(api_key: str | None) -> str:
    """A key is identified in the log, never recorded. Twelve hex characters is
    plenty to tell two agents apart and useless for signing in as either."""
    if not api_key:
        return ""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def token_summary(token: str | None) -> dict[str, Any]:
    """A signed token's identity, not its bytes: id, issuer, audience, expiry."""
    if not token:
        return {}
    # read_payload_unverified answers {} for anything it cannot parse, so an
    # empty answer here means the token was unreadable - which is not the same
    # as there being no token, and an audit log has to tell those apart.
    payload = read_payload_unverified(token)
    if not payload:
        return {"unreadable": True}
    known = {k: payload.get(k) for k in ("jti", "iss", "aud", "sub", "exp", "customer")
             if payload.get(k) is not None}
    return known or {"unreadable": True}


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[:MAX_TEXT] + "..."
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value[:50]]
    return value


@dataclass
class AuditRecord:
    at: datetime
    agent_id: str
    customer_id: str
    key_fingerprint: str
    action: str
    outcome: str                    # "ok" | "refused" | "error"
    status: int
    duration_ms: int
    reason: str = ""                # why it was refused, in the gateway's own words
    detail: str = ""                # the sentence a human reads first
    request: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    quote_jti: str = ""
    order_id: str = ""
    security_event: bool = False
    record_id: int | None = None

    def as_row(self) -> dict[str, Any]:
        return {
            "at": self.at, "agent_id": self.agent_id, "customer_id": self.customer_id,
            "key_fingerprint": self.key_fingerprint, "action": self.action,
            "outcome": self.outcome, "status": self.status, "duration_ms": self.duration_ms,
            "reason": self.reason, "detail": self.detail,
            "request": json.dumps(_clip(self.request), default=str),
            "response": json.dumps(_clip(self.response), default=str),
            "quote_jti": self.quote_jti, "order_id": self.order_id,
            "security_event": self.security_event,
        }

    def as_json(self) -> dict[str, Any]:
        row = {k: v for k, v in self.__dict__.items() if k != "record_id"}
        row["at"] = self.at.isoformat()
        row["request"] = _clip(self.request)
        row["response"] = _clip(self.response)
        row["id"] = self.record_id
        return row


INSERT = """
INSERT INTO gateway.audit_log
    (at, agent_id, customer_id, key_fingerprint, action, outcome, status,
     duration_ms, reason, detail, request, response, quote_jti, order_id, security_event)
VALUES
    (%(at)s, %(agent_id)s, %(customer_id)s, %(key_fingerprint)s, %(action)s, %(outcome)s,
     %(status)s, %(duration_ms)s, %(reason)s, %(detail)s, %(request)s, %(response)s,
     %(quote_jti)s, %(order_id)s, %(security_event)s)
RETURNING id
"""

SELECT = """
SELECT id, at, agent_id, customer_id, key_fingerprint, action, outcome, status,
       duration_ms, reason, detail, request, response, quote_jti, order_id, security_event
FROM gateway.audit_log
WHERE customer_id = %(customer)s
  AND (%(order_id)s = '' OR order_id = %(order_id)s)
  AND (%(quote_jti)s = '' OR quote_jti = %(quote_jti)s)
  AND (%(security_only)s IS FALSE OR security_event IS TRUE)
ORDER BY id DESC
LIMIT %(limit)s
"""


class AuditLog:
    """Append-only. `pool` is a psycopg pool, or None to stay in memory."""

    def __init__(self, pool: Any = None) -> None:
        self._pool = pool
        self._fallback: deque[AuditRecord] = deque(maxlen=FALLBACK_LIMIT)
        self.degraded = False
        self.last_error = ""
        self._failed_at = 0.0

    def forget_memory(self) -> int:
        """Drop the rows held in memory. For the labelled demo reset only.

        When the database is unreachable, rows go into `_fallback` and `recent`
        reads them from there. A reset truncates the table but the process keeps
        running, so without this the cleared log reappears - which is how the
        frontend ended up showing audit rows for orders that no longer existed.
        """
        held = len(self._fallback)
        self._fallback.clear()
        return held

    def _worth_retrying(self) -> bool:
        import time
        return not self.degraded or (time.monotonic() - self._failed_at) > RETRY_AFTER_SECONDS

    def set_pool(self, pool: Any) -> None:
        self._pool = pool

    # ---- writing ---------------------------------------------------------
    def record(self, **fields: Any) -> AuditRecord:
        fields.setdefault("at", datetime.now(timezone.utc))
        entry = AuditRecord(**fields)
        if self._pool is not None:
            try:
                with self._pool.connection(timeout=2.0) as conn:
                    row = conn.execute(INSERT, entry.as_row()).fetchone()
                    entry.record_id = row[0] if row else None
                self.degraded = False
                return entry
            except Exception as exc:                 # noqa: BLE001
                # Never fail the request because the log failed; say so instead.
                import time
                self._failed_at = time.monotonic()
                self.degraded = True
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                print(f"[gateway] audit log fell back to memory: {self.last_error}")
        self._fallback.append(entry)
        return entry

    # ---- reading ---------------------------------------------------------
    def recent(self, customer: str, limit: int = 100, order_id: str = "",
               quote_jti: str = "", security_only: bool = False) -> list[dict[str, Any]]:
        if self._pool is not None and self._worth_retrying():
            try:
                with self._pool.connection(timeout=5.0) as conn:
                    # A cursor, never the connection: the pool hands the same
                    # connection to everyone, so setting row_factory on it made
                    # the catalogue's own queries return dicts and blow up on
                    # r[1]. Scope it to this one read.
                    cursor = conn.cursor(row_factory=_dict_row())
                    rows = cursor.execute(SELECT, {
                        "customer": customer, "limit": limit, "order_id": order_id,
                        "quote_jti": quote_jti, "security_only": security_only,
                    }).fetchall()
                return [_row_as_json(r) for r in rows]
            except Exception as exc:                 # noqa: BLE001
                import time
                self._failed_at = time.monotonic()
                self.degraded = True
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
        kept = [e for e in reversed(self._fallback)
                if e.customer_id == customer
                and (not order_id or e.order_id == order_id)
                and (not quote_jti or e.quote_jti == quote_jti)
                and (not security_only or e.security_event)]
        return [e.as_json() for e in kept[:limit]]

    def __len__(self) -> int:
        return len(self._fallback)


def _dict_row():
    from psycopg.rows import dict_row
    return dict_row


def _row_as_json(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    at = out.get("at")
    out["at"] = at.isoformat() if hasattr(at, "isoformat") else str(at)
    for column in ("request", "response"):
        value = out.get(column)
        if isinstance(value, str):
            try:
                out[column] = json.loads(value)
            except Exception:                        # noqa: BLE001
                pass
    return out


# The log the gateway uses. main.py hands it the pool once the pool is open.
log = AuditLog()

SCHEMA_FILE = os.path.join(os.path.dirname(__file__), "..", "sql", "audit.sql")
