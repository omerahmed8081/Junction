"""The gateway's endpoints, called over HTTP with the database and Aimo faked.

Every other gateway test calls the functions underneath. That left the endpoint
code itself - the part that reads attributes off results and passes arguments
along - run by nothing until a real request arrived, and it is where a
misspelt attribute once returned 500 on every price check.

No lifespan, so no database and no network: `pool` and the Aimo client are
replaced by fakes that record what they were asked.
"""
from __future__ import annotations

import inspect
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

# Before the gateway is imported: it must not load the developer's .env here.
os.environ.setdefault("TTA_SKIP_DOTENV", "1")

from fastapi.testclient import TestClient  # noqa: E402

import gateway.main as main  # noqa: E402
from gateway import agents, audit  # noqa: E402
from test_orders import AVAILABILITY, PRICES, SUMMARIES  # noqa: E402

PRODUCT = "464306"
WHEN = "2026-11-17"


class FakeAimo:
    """Answers from the recorded samples and remembers how it was asked."""

    def __init__(self):
        self.asked: list[dict] = []

    def prices(self, ids, when, *, customer=None, fresh=False):
        self.asked.append({"what": "prices", "customer": customer, "fresh": fresh})
        return {i: PRICES[i] for i in ids if i in PRICES}

    def availability(self, ids, when, *, customer=None, fresh=False):
        self.asked.append({"what": "availability", "customer": customer, "fresh": fresh})
        return {i: AVAILABILITY[i] for i in ids if i in AVAILABILITY}


class FakePool:
    """Enough of a psycopg pool for the one inline query check_delivery runs."""

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *exc): return False

        def execute(self, sql, params=None):
            ids = (params or [[]])[0]
            rows = [(i, SUMMARIES[i].sales_unit) for i in ids if i in SUMMARIES]
            return type("R", (), {"fetchall": lambda self: rows,
                                  "fetchone": lambda self: (len(rows),)})()

    def connection(self, timeout=None):
        return self._Conn()


class Patched:
    """Swap the gateway's database, Aimo client and audit log for fakes."""

    def __enter__(self):
        self.before = (main.pool, main._aimo, main.catalog.summaries, audit.log._pool)
        self.aimo = FakeAimo()
        main.pool = FakePool()
        main._aimo = self.aimo
        main.catalog.summaries = lambda conn, ids: [SUMMARIES[i] for i in ids if i in SUMMARIES]
        audit.log.set_pool(None)                    # rows to memory, not a database
        self.client = TestClient(main.app)          # no lifespan: nothing real is opened
        return self

    def __exit__(self, *exc):
        main.pool, main._aimo, main.catalog.summaries, pool = self.before
        audit.log.set_pool(pool)
        return False


def key(which: str) -> dict:
    return {"X-API-Key": which}


CHECK = {"requested_date": WHEN,
         "items": [{"product_id": PRODUCT, "quantity_sales_units": 10}]}


def test_a_price_check_answers_and_is_logged():
    """The request that returned 500 for an evening: the audit line read
    attributes that live one level down on the result."""
    with Patched() as g:
        r = g.client.post("/gateway/check_delivery", json=CHECK,
                          headers=key(agents.DEMO_KITCHEN_KEY))
        assert r.status_code == 200, r.text
        result = r.json()["results"][0]
        assert result["product_id"] == PRODUCT
        assert "price" in result and "delivery" in result
        me = agents.lookup(agents.DEMO_KITCHEN_KEY)
        rows = audit.log.recent(me.customer_id)
        assert rows and rows[0]["action"] == "check_delivery" and rows[0]["outcome"] == "ok"


def test_no_key_is_turned_away_before_anything_is_asked_of_aimo():
    with Patched() as g:
        assert g.client.post("/gateway/check_delivery", json=CHECK).status_code == 401
        assert g.client.post("/gateway/check_delivery", json=CHECK,
                             headers=key("not-a-key")).status_code == 401
        assert g.aimo.asked == []


def test_each_caller_is_priced_as_their_own_customer():
    """Prices used to be fetched for one fixed customer, whoever asked."""
    with Patched() as g:
        for which in (agents.DEMO_KITCHEN_KEY, agents.DEMO_SECOND_CLIENT_KEY):
            g.aimo.asked.clear()
            r = g.client.post("/gateway/check_delivery", json=CHECK, headers=key(which))
            assert r.status_code == 200, r.text
            expected = agents.lookup(which).customer_id
            assert g.aimo.asked, "Aimo was never asked"
            assert all(a["customer"] == expected for a in g.aimo.asked), g.aimo.asked
        assert (agents.lookup(agents.DEMO_KITCHEN_KEY).customer_id
                != agents.lookup(agents.DEMO_SECOND_CLIENT_KEY).customer_id)


def test_a_quote_is_priced_for_the_caller_too():
    with Patched() as g:
        r = g.client.post("/gateway/create_quote", headers=key(agents.DEMO_KITCHEN_KEY),
                          json={"agent_id": agents.lookup(agents.DEMO_KITCHEN_KEY).agent_id,
                                "requested_date": WHEN,
                                "lines": [{"product_id": PRODUCT, "quantity_sales_units": 10}]})
        # 200 with a quote, or 409 once the sample's cut-off has passed in real
        # time. Either way the endpoint ran, and it asked as the right customer.
        assert r.status_code in (200, 409), r.text
        expected = agents.lookup(agents.DEMO_KITCHEN_KEY).customer_id
        assert g.aimo.asked and all(a["customer"] == expected for a in g.aimo.asked)


def test_the_order_time_check_does_not_trust_the_cache():
    """With answers reused for ever, the re-check got the quote's own answer
    back and `stock_changed` could never happen."""
    with Patched() as g:
        main._summaries_and_results([PRODUCT], {PRODUCT: 10.0}, date(2026, 11, 17),
                                    customer="123", fresh=True)
        assert g.aimo.asked and all(a["fresh"] is True for a in g.aimo.asked)
        g.aimo.asked.clear()
        main._summaries_and_results([PRODUCT], {PRODUCT: 10.0}, date(2026, 11, 17))
        assert all(a["fresh"] is False for a in g.aimo.asked)
    # ...and the order path is the one that asks for it.
    assert "fresh=True" in inspect.getsource(main.place_order)


def test_the_key_and_health_are_reachable_behind_a_shared_front_door():
    """One origin in front of both services only passes /gateway/* through, and
    the bare path there served the kitchen's key instead of Aimo's."""
    with Patched() as g:
        bare = g.client.get("/.well-known/jwks.json").json()
        prefixed = g.client.get("/gateway/.well-known/jwks.json")
        assert prefixed.status_code == 200 and prefixed.json() == bare
        assert g.client.get("/gateway/health").status_code == 200


def test_the_name_signed_into_a_quote_is_not_the_network_address():
    """They were one variable, so public quotes were issued by localhost."""
    from gateway import keys
    before = os.environ.get("GATEWAY_ISSUER")
    try:
        os.environ.pop("GATEWAY_ISSUER", None)
        assert keys.gateway_issuer() == keys.GATEWAY_URL        # unset: as it always was
        os.environ["GATEWAY_ISSUER"] = "https://aimo.example/gateway"
        assert keys.gateway_issuer() == "https://aimo.example/gateway"
        assert keys.GATEWAY_URL != keys.gateway_issuer()        # the address did not move
    finally:
        if before is None:
            os.environ.pop("GATEWAY_ISSUER", None)
        else:
            os.environ["GATEWAY_ISSUER"] = before


def test_health_and_the_public_key_need_no_key():
    with Patched() as g:
        assert g.client.get("/health").status_code == 200
        keys = g.client.get("/.well-known/jwks.json")
        assert keys.status_code == 200 and keys.json()["keys"]
        # A public key document must never carry the private half.
        assert all("d" not in k for k in keys.json()["keys"])


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(passed)
