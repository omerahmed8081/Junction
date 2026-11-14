"""Quotes and orders: every way an order can be refused.

Offline. The quote and the price figures come from the responses recorded at
hour 0, so the happy path is the demo's own basket: 1,000 units of 464306 at
EUR 1.01, EUR 1,010.00 net.

The point of this file is the refusals. "Nothing is ordered without a human's
OK" has to be something Aimo enforces, not something the kitchen promises about
itself, so each check gets a test that proves an order is refused without it.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gateway.delivery import build_response  # noqa: E402
from gateway.orders import OrderRefused, OrderStore, place_order  # noqa: E402
from gateway.quotes import QuoteRegistry, build_quote, new_order_id  # noqa: E402
from shared.schemas import (  # noqa: E402
    CheckDeliveryItem,
    MandateLimits,
    MandatePayload,
    PlaceOrderRequest,
    ProductSummary,
)
from shared.signing import Signer, Verifier, generate_keypair, now_seconds  # noqa: E402

SAMPLES = ROOT / "Data" / "mock_samples"
PRICES = {p["productId"]: p for p in json.loads((SAMPLES / "price_milk.json").read_text())["products"]}
AVAILABILITY = {p["productId"]: p for p in json.loads((SAMPLES / "availability_milk.json").read_text())["products"]}
SUMMARIES = {s.product_id: s for s in
             (ProductSummary(**x) for x in
              json.loads((ROOT / "tests/fixtures/milk_summaries.json").read_text(encoding="utf-8")))}

GATEWAY = "http://localhost:8001"
KITCHEN = "http://localhost:8002/restaurant-demo"
AGENT = "agent:kitchen-demo"
CUSTOMER = "123"
WHEN = date(2026, 11, 17)
NOW = datetime(2026, 11, 14, 14, 0, tzinfo=timezone.utc)

AIMO_KEYS = generate_keypair("aimo-2026-11")
KITCHEN_KEYS = generate_keypair("kitchen-2026-11")
STRANGER_KEYS = generate_keypair("stranger-1")

AIMO = Signer(AIMO_KEYS)
KITCHEN_SIGNER = Signer(KITCHEN_KEYS)
STRANGER = Signer(STRANGER_KEYS)
AIMO_VERIFIER = Verifier([AIMO_KEYS.public_jwk])
KITCHEN_VERIFIER = Verifier([KITCHEN_KEYS.public_jwk])


# ------------------------------------------------------------------ helpers


def results_for(quantities: dict[str, float], when: date = WHEN, now: datetime = NOW,
                availability=None):
    items = [CheckDeliveryItem(product_id=p, quantity_sales_units=q) for p, q in quantities.items()]
    units = {p: SUMMARIES[p].sales_unit for p in quantities}
    # `availability or AVAILABILITY` would turn an empty dict back into the full
    # one, which is exactly the case one test needs.
    raw = AVAILABILITY if availability is None else availability
    response = build_response(items, when, PRICES, raw, units, now=now)
    return {r.product_id: r for r in response.results}


def make_quote(quantities=None, *, when: date = WHEN, now: datetime = NOW,
               agent: str = AGENT, availability=None, signer: Signer = AIMO):
    quantities = quantities or {"464306": 1000.0}
    return build_quote(
        issuer=GATEWAY, customer_id=CUSTOMER, agent_id=agent, requested_date=when,
        summaries=SUMMARIES, results=results_for(quantities, when, now, availability),
        wanted=quantities, signer=signer, now=now,
    )


def make_mandate(*, per_order=2000.0, per_week=5000.0, categories=None,
                 approval_required=True, agent=AGENT, customer=CUSTOMER,
                 signer: Signer = KITCHEN_SIGNER, now: datetime = NOW):
    payload = MandatePayload(
        iss=KITCHEN, sub=agent, aud=GATEWAY, customer=customer,
        jti="m_test", iat=now_seconds(now), exp=now_seconds(now) + 7 * 24 * 3600,
        limits=MandateLimits(max_net_per_order=per_order, max_net_per_week=per_week),
        allowed_categories=categories if categories is not None else ["10400"],
        approval_required=approval_required,
    )
    return signer.sign(payload.model_dump(mode="json"))


def make_approval(quote_id: str, *, role="manager", signer: Signer = KITCHEN_SIGNER,
                  now: datetime = NOW):
    return signer.sign({
        "iss": KITCHEN, "aud": GATEWAY, "jti": "a_test", "quote_jti": quote_id,
        "approver_role": role, "approved_at": now_seconds(now),
        "iat": now_seconds(now), "exp": now_seconds(now) + 3600,
    })


def order(quote=None, *, mandate=None, approval="auto", key="idem-1", now: datetime = NOW,
          registry=None, store=None, availability=None, agent=AGENT):
    quote = quote or make_quote()
    if approval == "auto":
        approval = make_approval(quote.quote_id, now=now)
    request = PlaceOrderRequest(
        quote_jws=quote.quote_jws,
        mandate_jws=mandate if mandate is not None else make_mandate(now=now),
        approval_jws=approval,
        idempotency_key=key,
    )

    def recheck(requested_date, items):
        quantities = {i.product_id: i.quantity_sales_units for i in items}
        return results_for(quantities, requested_date, now, availability)

    return place_order(
        request=request, agent_id=agent, gateway_url=GATEWAY,
        own_verifier=AIMO_VERIFIER, kitchen_verifier=KITCHEN_VERIFIER,
        recheck_delivery=recheck, registry=registry if registry is not None else QuoteRegistry(),
        store=store if store is not None else OrderStore(), signer=AIMO,
        new_order_id=new_order_id, now=now,
    )


def refused(**kwargs) -> tuple[str, bool]:
    """Place an order expected to fail; return its reason and security flag."""
    try:
        order(**kwargs)
    except OrderRefused as exc:
        return exc.rejection.reason, exc.rejection.security_event
    raise AssertionError("the order was accepted and should not have been")


# ------------------------------------------------------------------- quotes


def test_a_quote_carries_the_demo_figures():
    quote = make_quote()
    assert quote.net_total == 1010.00 and quote.vat_total == 136.35
    assert quote.gross_total == 1146.35
    line = quote.lines[0]
    assert line.product_id == "464306" and line.quantity_sales_units == 1000
    assert line.price_per_sales_unit == 1.01 and line.price_per_comparison_unit == 1.01
    assert line.delivered_amount == 1000 and line.delivered_unit == "L"
    assert line.category == "10400" and "FREE_FROM_LACTOSE" in line.claims


def test_a_quote_cannot_outlive_the_cutoff():
    """exp is the earliest of the cut-off and two hours from now."""
    quote = make_quote()
    cutoff = datetime(2026, 11, 15, 17, 0, tzinfo=timezone(timedelta(hours=2)))
    assert quote.expires_at == min(cutoff, NOW + timedelta(hours=2))
    # Asked an hour before the cut-off, the cut-off is what binds.
    late = datetime(2026, 11, 15, 16, 0, tzinfo=timezone(timedelta(hours=2)))
    assert make_quote(now=late).expires_at == cutoff


def test_the_quote_verifies_and_says_what_it_promises():
    quote = make_quote()
    payload = AIMO_VERIFIER.verify(quote.quote_jws, issuer=GATEWAY, audience=AGENT, now=NOW)
    assert payload["sub"] == "customer:123" and payload["net_total"] == 1010.0
    assert payload["lines"][0]["delivery"]["delivery_date"] == "2026-11-16"
    # Valio: a sentinel amount means availability is unknown, and the quote says so.
    assert payload["lines"][0]["delivery"]["availability_known"] is False


def test_a_partial_line_is_refused_rather_than_quoted():
    """462775 has 51 units; a quote for 1,000 would promise what Aimo cannot."""
    quote = make_quote({"464306": 1000.0, "462775": 1000.0})
    assert [l.product_id for l in quote.lines] == ["464306"]
    assert quote.refused == [{"product_id": "462775", "reason": "partial_only",
                              "fulfil_amount": "51"}]


def test_a_past_cutoff_line_is_refused():
    quote = make_quote({"464306": 1000.0, "463038": 50.0})      # the 10 L Novobox
    assert [l.product_id for l in quote.lines] == ["464306"]
    assert quote.refused[0]["reason"] == "past_closing_time"


def test_nothing_quotable_is_an_error_not_an_empty_quote():
    try:
        make_quote({"463038": 50.0})
    except ValueError:
        return
    raise AssertionError("a quote with no line should not be signed")


# ------------------------------------------------------------- the happy path


def test_an_order_with_quote_mandate_and_approval_is_accepted():
    record = order()
    assert record.status == "placed" and record.net_total == 1010.00
    assert record.approver_role == "manager"
    assert record.delivery_date == date(2026, 11, 16)
    assert record.lines[0].product_id == "464306"


def test_aimo_signs_the_confirmation_too():
    record = order()
    payload = AIMO_VERIFIER.verify(record.confirmation_jws, issuer=GATEWAY,
                                   audience=AGENT, now=NOW)
    assert payload["order_id"] == record.order_id
    assert payload["quote_jti"] == record.quote_id
    assert payload["approver_role"] == "manager"


# --------------------------------------------------------------- the refusals


def test_a_tampered_quote_is_a_security_event():
    quote = make_quote()
    quote.quote_jws = quote.quote_jws[:-6] + "AAAAAA"
    assert refused(quote=quote) == ("quote_invalid", True)


def test_a_quote_signed_by_somebody_else_is_refused():
    assert refused(quote=make_quote(signer=STRANGER)) == ("quote_invalid", True)


def test_an_expired_quote_is_refused():
    much_later = NOW + timedelta(hours=6)
    assert refused(now=much_later)[0] == "quote_invalid"


def test_a_quote_for_another_agent_is_refused():
    assert refused(quote=make_quote(agent="agent:someone-else"))[0] == "quote_invalid"


def test_the_same_quote_cannot_be_ordered_twice():
    registry, store, quote = QuoteRegistry(), OrderStore(), make_quote()
    first = order(quote=quote, registry=registry, store=store, key="idem-1")
    reason, _ = refused(quote=quote, registry=registry, store=store, key="idem-2")
    assert reason == "quote_already_used"
    assert registry.order_for_quote(quote.quote_id) == first.order_id


def test_a_retry_with_the_same_key_returns_the_same_order():
    registry, store, quote = QuoteRegistry(), OrderStore(), make_quote()
    first = order(quote=quote, registry=registry, store=store, key="idem-1")
    again = order(quote=quote, registry=registry, store=store, key="idem-1")
    assert again.order_id == first.order_id
    assert len(store.orders) == 1


def test_no_mandate_no_order():
    """What the second client shows on stage."""
    assert refused(mandate=make_mandate(signer=STRANGER)) == ("mandate_invalid", True)


def test_a_mandate_for_another_agent_is_refused():
    assert refused(mandate=make_mandate(agent="agent:another")) == ("mandate_wrong_agent", True)


def test_a_mandate_for_another_customer_is_refused():
    assert refused(mandate=make_mandate(customer="999")) == ("mandate_wrong_customer", True)


def test_over_the_per_order_limit():
    reason, security = refused(mandate=make_mandate(per_order=500.0))
    assert reason == "over_order_limit" and security is False


def test_over_the_weekly_limit():
    """Two orders of EUR 1,010 against a EUR 1,500 week: the second is refused."""
    registry, store = QuoteRegistry(), OrderStore()
    mandate = make_mandate(per_week=1500.0)
    order(quote=make_quote(), mandate=mandate, registry=registry, store=store, key="k1")
    assert store.weekly_net(CUSTOMER, NOW) == 1010.00
    reason, _ = refused(quote=make_quote(), mandate=mandate, registry=registry,
                        store=store, key="k2")
    assert reason == "over_weekly_limit"


def test_a_category_outside_the_mandate_is_refused():
    assert refused(mandate=make_mandate(categories=["10401"]))[0] == "category_not_allowed"


def test_an_order_with_no_approval_is_refused():
    reason, security = refused(approval=None)
    assert reason == "approval_missing" and security is False


def test_an_approval_signed_by_somebody_else_is_a_security_event():
    quote = make_quote()
    bad = make_approval(quote.quote_id, signer=STRANGER)
    assert refused(quote=quote, approval=bad) == ("approval_invalid", True)


def test_an_approval_for_another_quote_cannot_pay_for_this_one():
    """The reason approvals name a quote at all."""
    quote, other = make_quote(), make_quote()
    assert refused(quote=quote, approval=make_approval(other.quote_id)) == (
        "approval_for_another_quote", True)


def test_a_mandate_that_needs_no_approval_lets_an_order_through():
    record = order(mandate=make_mandate(approval_required=False), approval=None)
    assert record.status == "placed" and record.approver_role is None


def test_stock_that_moved_between_quote_and_order_stops_it():
    """Delivery is a snapshot, so it is checked again at the moment of ordering."""
    moved = dict(AVAILABILITY)
    moved["464306"] = {**AVAILABILITY["464306"],
                       "scheduleLines": [{**AVAILABILITY["464306"]["scheduleLines"][0],
                                          "availableAmount": 400.0}]}
    reason, _ = refused(quote=make_quote(), availability=moved)
    assert reason == "stock_changed"


def test_a_cutoff_that_passes_while_the_quote_is_open_stops_it():
    gone = dict(AVAILABILITY)
    gone["464306"] = {**AVAILABILITY["464306"], "closingTime": "2026-11-14T13:00:00+0200"}
    reason, _ = refused(quote=make_quote(), availability=gone)
    assert reason == "past_closing_time"


def test_a_product_aimo_stops_answering_stops_the_order():
    reason, _ = refused(quote=make_quote(), availability={})
    assert reason == "stock_changed"


def test_an_unsigned_token_is_never_accepted():
    """The classic JWT forgery: alg "none" with a payload of one's choosing."""
    import base64

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    forged = (b64(json.dumps({"alg": "none", "kid": "aimo-2026-11"}).encode()) + "."
              + b64(json.dumps({"iss": GATEWAY, "aud": AGENT, "sub": "customer:123",
                                "jti": "q_forged", "exp": now_seconds(NOW) + 3600,
                                "net_total": 0.01}).encode()) + ".")
    try:
        AIMO_VERIFIER.verify(forged, issuer=GATEWAY, audience=AGENT, now=NOW)
    except Exception as exc:        # noqa: BLE001
        assert "not accepted" in str(exc), exc
        return
    raise AssertionError("an unsigned token was accepted")


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(f"\n{passed} passed")
