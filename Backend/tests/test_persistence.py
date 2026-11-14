"""State that survives a restart (gateway.orders, used_quotes, kitchen.state).

Orders used to live in three places - the kitchen's file, the gateway's memory
and the audit log - and they disagreed after any reset. They are rows now, and
the risk moved with them: a model that does not survive the trip out to JSON
and back comes home missing a field, which is the sort of thing that shows up
as a wrong total rather than an error.

So these tests round-trip the real objects through the real serialisation. The
pool is a fake that keeps rows in a dict and answers the handful of statements
these stores actually run.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("TTA_SKIP_DOTENV", "1")

from gateway.orders import OrderStore  # noqa: E402
from gateway.quotes import QuoteRegistry  # noqa: E402
from kitchen import storage  # noqa: E402
from kitchen.need import ParsedNeed  # noqa: E402
from kitchen.ordering import Mandate  # noqa: E402
from shared.schemas import (  # noqa: E402
    Basket, MandateLimits, MandatePayload, OrderRecord,
)
from test_orders import CUSTOMER, NOW  # noqa: E402


class FakePool:
    """Enough Postgres for these stores: rows in a dict, and the statements
    they run. Anything unrecognised raises, so a query that changes without a
    test noticing does not silently pass."""

    def __init__(self, fail: bool = False):
        self.orders: dict[str, tuple] = {}
        self.quotes: dict[str, tuple] = {}
        self.state: dict[str, str] = {}
        self.fail = fail

    class _Result:
        def __init__(self, rows): self._rows = rows
        def fetchall(self): return self._rows
        def fetchone(self): return self._rows[0] if self._rows else None

    class _Conn:
        def __init__(self, pool): self.pool = pool
        def __enter__(self): return self
        def __exit__(self, *exc): return False

        def execute(self, sql, params=None):
            p = self.pool
            text = " ".join(sql.split())
            if text.startswith("INSERT INTO gateway.orders"):
                p.orders[params["order_id"]] = (params["document"],)
            elif text.startswith("SELECT document FROM gateway.orders"):
                return FakePool._Result(list(p.orders.values()))
            elif text.startswith("DELETE FROM gateway.orders"):
                customer = params[0] if isinstance(params, tuple) else params
                for key, (doc,) in list(p.orders.items()):
                    if json.loads(doc).get("customer") == customer:
                        p.orders.pop(key)
            elif text.startswith("INSERT INTO gateway.used_quotes"):
                quote_jti, order_id, key, customer = params
                p.quotes.setdefault(quote_jti, (quote_jti, order_id, key))
            elif text.startswith("SELECT quote_jti"):
                return FakePool._Result(list(p.quotes.values()))
            elif text.startswith("DELETE FROM gateway.used_quotes"):
                gone = set(params[0])
                p.quotes = {q: row for q, row in p.quotes.items() if row[1] not in gone}
            elif text.startswith("INSERT INTO kitchen.state"):
                state_id, document = params
                p.state[state_id] = document
            elif text.startswith("SELECT document FROM kitchen.state"):
                found = p.state.get(params[0])
                return FakePool._Result([(found,)] if found is not None else [])
            elif text.startswith("DELETE FROM kitchen.state"):
                p.state.pop(params[0], None)
            else:
                raise AssertionError(f"the fake pool does not know: {text[:70]}")
            return FakePool._Result([])

    def connection(self, timeout=None):
        if self.fail:
            raise RuntimeError("database is away")
        return self._Conn(self)


def an_order(order_id="o_1", customer=CUSTOMER, net=1010.0, placed_at=NOW) -> OrderRecord:
    return OrderRecord(
        order_id=order_id, quote_id="q_1", customer=customer, agent_id="agent:kitchen-demo",
        status="placed", placed_at=placed_at, requested_date=date(2026, 11, 17),
        delivery_date=date(2026, 11, 16), lines=[], net_total=net, vat_total=0.0,
        gross_total=net)


# ---- the gateway's orders ----------------------------------------------

def test_an_order_comes_back_whole_after_a_restart():
    """The figures the weekly limit is counted from have to survive the trip."""
    pool = FakePool()
    before = OrderStore()
    before.set_pool(pool)
    before.add(an_order(net=1010.0))

    after = OrderStore()                       # a fresh process
    after.set_pool(pool)
    back = after.get("o_1")
    assert back is not None
    assert back.net_total == 1010.0 and back.customer == CUSTOMER
    assert back.placed_at == NOW               # a datetime, not a string
    assert back.delivery_date == date(2026, 11, 16)


def test_the_weeks_spending_is_not_forgotten_by_a_restart():
    """It was: the limit started over and the guardrail was gone."""
    pool = FakePool()
    before = OrderStore()
    before.set_pool(pool)
    before.add(an_order(order_id="o_1", net=1200.0))
    before.add(an_order(order_id="o_2", net=800.0, placed_at=NOW - timedelta(days=1)))

    after = OrderStore()
    after.set_pool(pool)
    assert after.weekly_net(CUSTOMER, NOW) == 2000.0
    assert after.daily_net(CUSTOMER, NOW) == 1200.0


def test_the_demo_reset_clears_the_table_too():
    pool = FakePool()
    store = OrderStore()
    store.set_pool(pool)
    store.add(an_order())
    store.forget_customer(CUSTOMER)
    fresh = OrderStore()                       # a fresh process, nothing cached
    fresh.set_pool(pool)
    assert fresh.get("o_1") is None


def test_an_order_stands_even_if_the_write_fails():
    """The order was accepted; it is the record of it that did not land. Losing
    it from memory as well would mean the gateway forgot an order it placed."""
    store = OrderStore()
    store.pool = FakePool(fail=True)
    store.add(an_order())
    assert store.get("o_1") is not None


# ---- used quotes -------------------------------------------------------

def test_a_used_quote_is_still_used_after_a_restart():
    """This is what stops one quote being ordered twice. A restart emptying it
    made an already-paid-for quote orderable again."""
    pool = FakePool()
    before = QuoteRegistry()
    before.set_pool(pool)
    before.mark_used("q_abc", "idem-1", "o_1", CUSTOMER)

    after = QuoteRegistry()
    after.set_pool(pool)
    assert after.order_for_quote("q_abc") == "o_1"
    assert after.order_for_key("idem-1") == "o_1"
    assert after.order_for_quote("q_never") is None


def test_forgetting_a_customer_releases_their_quotes():
    pool = FakePool()
    registry = QuoteRegistry()
    registry.set_pool(pool)
    registry.mark_used("q_abc", "idem-1", "o_1", CUSTOMER)
    registry.forget_customer(["o_1"])
    fresh = QuoteRegistry()
    fresh.set_pool(pool)
    assert fresh.order_for_quote("q_abc") is None


# ---- the kitchen's state -----------------------------------------------

def a_mandate() -> Mandate:
    payload = MandatePayload(
        iss="http://kitchen", sub="agent:kitchen-demo", aud="http://gateway",
        customer=CUSTOMER, jti="m_1", iat=1, exp=2_000_000_000,
        limits=MandateLimits(max_net_per_order=2000.0, max_net_per_day=3000.0,
                             max_net_per_week=10000.0),
        allowed_categories=[], approval_required=False)
    return Mandate(jws="not.a.real.token", payload=payload)


class Stored:
    """Point storage at a fake pool and a throwaway file, and put it back."""

    def __init__(self, tmp_path, pool=None):
        self.pool = pool if pool is not None else FakePool()
        self.path = tmp_path

    def __enter__(self):
        self.before = storage._pool
        storage.set_pool(self.pool)
        return self

    def __exit__(self, *exc):
        storage.set_pool(self.before)
        return False


def test_the_kitchens_state_round_trips_through_one_row(tmp_path):
    from kitchen.agent import BasketState

    basket = Basket(basket_id="b_1", requested_date=date(2026, 11, 17))
    state = BasketState(basket)
    state.needs["464306"] = ParsedNeed(product_text="milk", quantity=1000, unit="L",
                                       requested_date=date(2026, 11, 17))
    services = [{"date": "2026-11-14", "portions": {"d1": 20}}]
    path = tmp_path / "kitchen.json"

    with Stored(path) as st:
        storage.save({"b_1": state}, {"o_1": an_order()}, a_mandate(),
                     path=path, services=services)
        assert st.pool.state, "nothing was written to the row"
        assert not path.exists(), "the file was written as well as the row"

        baskets, orders, mandate, back = storage.load(path=path)

    assert list(baskets) == ["b_1"]
    assert baskets["b_1"].needs["464306"].quantity == 1000
    assert baskets["b_1"].basket.requested_date == date(2026, 11, 17)
    assert orders["o_1"].net_total == 1010.0
    assert mandate is not None and mandate.payload.limits.max_net_per_day == 3000.0
    assert back == services


def test_a_database_that_is_away_still_keeps_the_basket(tmp_path):
    """Falling back to the file is the point: losing a basket mid-demo because
    Aiven blinked would be worse than a file nobody reads."""
    path = tmp_path / "kitchen.json"
    with Stored(path, pool=FakePool(fail=True)):
        storage.save({}, {"o_1": an_order()}, None, path=path, services=[])
        assert path.exists(), "nothing was written anywhere"
        orders = storage.load(path=path)[1]
    assert orders["o_1"].net_total == 1010.0


def test_state_left_in_a_file_is_found_and_moves_to_the_row(tmp_path):
    """A kitchen pointed at Postgres for the first time must not look empty."""
    path = tmp_path / "kitchen.json"
    with Stored(path, pool=FakePool(fail=True)):          # writes the file
        storage.save({}, {"o_1": an_order()}, None, path=path, services=[])

    with Stored(path) as st:                              # now with a database
        orders = storage.load(path=path)[1]
        assert orders["o_1"].order_id == "o_1"            # found in the file
        storage.save({}, orders, None, path=path, services=[])
        assert st.pool.state, "it did not move to the row"


def test_the_demo_reset_clears_both(tmp_path):
    path = tmp_path / "kitchen.json"
    with Stored(path) as st:
        storage.save({}, {"o_1": an_order()}, None, path=path, services=[])
        path.write_text("{}", encoding="utf-8")           # an old file beside it
        storage.clear(path=path)
        assert st.pool.state == {} and not path.exists()
        assert storage.load(path=path) == ({}, {}, None, [])


if __name__ == "__main__":
    import tempfile

    passed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        import inspect
        if "tmp_path" in inspect.signature(fn).parameters:
            with tempfile.TemporaryDirectory() as folder:
                from pathlib import Path as _P
                fn(_P(folder))
        else:
            fn()
        print("ok  ", name)
        passed += 1
    print(passed)
