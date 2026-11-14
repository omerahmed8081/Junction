"""The gateway's audit log (gateway/audit.py).

These run against the in-memory fallback, so no database is needed. What they
pin is the part that matters whether the rows land in Postgres or not: one
customer never sees another's rows, and nothing secret is written down.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gateway.audit import AuditLog, key_fingerprint, token_summary, MAX_TEXT  # noqa: E402
from kitchen.audit import KitchenAudit, quote_id_of  # noqa: E402
from shared.signing import Signer, generate_keypair  # noqa: E402

NOW = datetime(2026, 11, 14, 14, 0, tzinfo=timezone.utc)


def a_log() -> AuditLog:
    return AuditLog(pool=None)          # memory only


def add(log: AuditLog, **over):
    fields = dict(agent_id="agent:kitchen-demo", customer_id="123",
                  key_fingerprint="abc123", action="create_quote", outcome="ok",
                  status=200, duration_ms=12, at=NOW)
    fields.update(over)
    return log.record(**fields)


def test_a_row_is_written_and_read_back():
    log = a_log()
    add(log, detail="quoted 1 line")
    rows = log.recent("123")
    assert len(rows) == 1
    assert rows[0]["action"] == "create_quote" and rows[0]["detail"] == "quoted 1 line"
    assert rows[0]["agent_id"] == "agent:kitchen-demo"


def test_one_customer_never_sees_another():
    log = a_log()
    add(log, customer_id="123", detail="the kitchen's order")
    add(log, customer_id="456", detail="somebody else's order")
    assert [r["detail"] for r in log.recent("123")] == ["the kitchen's order"]
    assert [r["detail"] for r in log.recent("456")] == ["somebody else's order"]
    assert log.recent("999") == []


def test_newest_first():
    log = a_log()
    add(log, at=NOW, detail="first")
    add(log, at=NOW + timedelta(minutes=1), detail="second")
    assert [r["detail"] for r in log.recent("123")] == ["second", "first"]


def test_rows_can_be_narrowed_to_one_order_or_quote():
    log = a_log()
    add(log, action="place_order", order_id="o_1", quote_jti="q_1")
    add(log, action="place_order", order_id="o_2", quote_jti="q_2")
    assert len(log.recent("123", order_id="o_1")) == 1
    assert log.recent("123", order_id="o_1")[0]["order_id"] == "o_1"
    assert log.recent("123", quote_jti="q_2")[0]["order_id"] == "o_2"


def test_only_the_rows_somebody_should_look_at():
    log = a_log()
    add(log, outcome="ok")
    add(log, outcome="refused", reason="mandate_invalid", security_event=True)
    flagged = log.recent("123", security_only=True)
    assert len(flagged) == 1 and flagged[0]["reason"] == "mandate_invalid"


def test_a_refusal_is_recorded_with_its_reason():
    """The most interesting row in the table is the one where somebody was
    turned away, so the reason has to survive."""
    log = a_log()
    add(log, outcome="refused", status=409, reason="over_order_limit",
        detail="1010.00 EUR is over the 500.00 EUR limit for one order")
    row = log.recent("123")[0]
    assert row["outcome"] == "refused" and row["reason"] == "over_order_limit"
    assert "500.00" in row["detail"]


def test_the_api_key_itself_is_never_written_down():
    assert key_fingerprint("demo-kitchen-key") != "demo-kitchen-key"
    assert len(key_fingerprint("demo-kitchen-key")) == 12
    # Same key, same fingerprint; different key, different fingerprint.
    assert key_fingerprint("k") == key_fingerprint("k")
    assert key_fingerprint("k") != key_fingerprint("k2")
    assert key_fingerprint(None) == "" and key_fingerprint("") == ""


def test_a_signed_token_is_recorded_by_its_identity_not_its_bytes():
    signer = Signer(generate_keypair("test-key"))
    token = signer.sign({"jti": "q_abc", "iss": "https://aimo", "aud": "agent:x",
                         "customer": "123", "exp": 1, "secret_field": "do not log me"})
    summary = token_summary(token)
    assert summary["jti"] == "q_abc" and summary["customer"] == "123"
    # Neither the token nor anything we did not ask for ends up in the log.
    assert token not in str(summary)
    assert "secret_field" not in summary and "do not log me" not in str(summary)


def test_an_unreadable_token_is_marked_rather_than_crashing():
    assert token_summary("not.a.token") == {"unreadable": True}
    assert token_summary(None) == {} and token_summary("") == {}


def test_a_very_long_field_is_clipped():
    log = a_log()
    add(log, request={"text": "x" * (MAX_TEXT + 500)})
    stored = log.recent("123")[0]["request"]["text"]
    assert len(stored) < MAX_TEXT + 10 and stored.endswith("...")


def test_the_log_never_breaks_the_request_when_the_database_is_gone():
    class Broken:
        def connection(self, timeout=None):
            raise RuntimeError("database is away")

    log = AuditLog(pool=Broken())
    add(log, detail="still recorded")           # must not raise
    assert log.degraded is True
    assert "database is away" in log.last_error
    # ...and the row is still readable, from memory.
    assert log.recent("123")[0]["detail"] == "still recorded"


# ---- the kitchen's half ------------------------------------------------

def test_the_kitchen_records_why_not_only_what():
    log = KitchenAudit(pool=None)
    log.record("parse_need", basket_id="b1", actor="human", parser="gemini",
               inputs={"message": "1,000 L lactose-free milk by Tuesday"},
               detail="gemini read 1000.0 L of 'milk'")
    row = log.recent()[0]
    assert row["step"] == "parse_need" and row["parser"] == "gemini"
    assert row["inputs"]["message"].startswith("1,000 L")


def test_what_aimo_sees_is_the_smaller_half():
    """The toggle on the Activity tab: most rows never left the building."""
    log = KitchenAudit(pool=None)
    log.record("parse_need", basket_id="b1")
    log.record("convert", basket_id="b1")
    log.record("confirm", basket_id="b1", crossed_gateway=True)
    assert len(log.recent()) == 3
    crossed = log.recent(crossed_only=True)
    assert len(crossed) == 1 and crossed[0]["step"] == "confirm"


def test_rows_can_be_narrowed_to_one_basket_or_order():
    log = KitchenAudit(pool=None)
    log.record("parse_need", basket_id="b1")
    log.record("approve", basket_id="b2", order_id="o_9")
    assert len(log.recent(basket_id="b1")) == 1
    assert log.recent(order_id="o_9")[0]["basket_id"] == "b2"


def test_the_agents_own_steps_are_recorded_as_they_are():
    class Step:
        def __init__(self, step, detail, crossed):
            self.step, self.detail, self.crossed_gateway, self.at = step, detail, crossed, NOW

    log = KitchenAudit(pool=None)
    log.record_steps([Step("search", "found 3 products", True),
                      Step("convert", "1,000 L -> 50 KI", False)], basket_id="b1")
    rows = log.recent()
    assert [r["step"] for r in rows] == ["convert", "search"]
    assert rows[1]["crossed_gateway"] is True and rows[0]["crossed_gateway"] is False
    assert all(r["basket_id"] == "b1" for r in rows)


def test_the_quote_id_joins_the_two_logs():
    signer = Signer(generate_keypair("test-key"))
    token = signer.sign({"jti": "q_abc", "iss": "https://aimo"})
    assert quote_id_of(token) == "q_abc"
    assert quote_id_of(None) == "" and quote_id_of("rubbish") == ""


def test_the_kitchen_log_also_survives_the_database_being_gone():
    class Broken:
        def connection(self, timeout=None):
            raise RuntimeError("database is away")

    log = KitchenAudit(pool=Broken())
    log.record("parse_need", basket_id="b1", detail="still recorded")
    assert log.degraded is True
    assert log.recent()[0]["detail"] == "still recorded"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(passed)
