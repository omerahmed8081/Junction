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
    OrderRecord,
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


def make_mandate(*, per_order=2000.0, per_day=None, per_week=5000.0, categories=None,
                 approval_required=True, agent=AGENT, customer=CUSTOMER,
                 signer: Signer = KITCHEN_SIGNER, now: datetime = NOW):
    payload = MandatePayload(
        iss=KITCHEN, sub=agent, aud=GATEWAY, customer=customer,
        jti="m_test", iat=now_seconds(now), exp=now_seconds(now) + 7 * 24 * 3600,
        limits=MandateLimits(max_net_per_order=per_order, max_net_per_day=per_day,
                             max_net_per_week=per_week),
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
          registry=None, store=None, availability=None, agent=AGENT, revoked=None):
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
        new_order_id=new_order_id, revoked=revoked, now=now,
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


# ---- a mandate lives as long as its exp says ---------------------------

def test_a_mandate_signed_three_days_ago_still_orders():
    """Signed for seven days, it was refused after one: the verifier's age cap
    for use-at-once tokens was being applied to it too."""
    three_days_ago = NOW - timedelta(days=3)
    record = order(mandate=make_mandate(now=three_days_ago))
    assert record.status == "placed"


def test_a_mandate_past_its_own_expiry_is_still_refused():
    eight_days_ago = NOW - timedelta(days=8)          # exp is iat + 7 days
    reason, security = refused(mandate=make_mandate(now=eight_days_ago))
    assert reason == "mandate_invalid" and security


def test_an_approval_keeps_the_age_cap():
    """Lifting the cap for mandates must not lift it for anything else."""
    from shared.signing import VerificationError
    old_approval = KITCHEN_SIGNER.sign({
        "iss": KITCHEN, "aud": GATEWAY, "jti": "a_old", "quote_jti": "q",
        "approver_role": "manager", "approved_at": 0,
        "iat": now_seconds(NOW) - 2 * 24 * 3600, "exp": now_seconds(NOW) + 3600})
    try:
        KITCHEN_VERIFIER.verify(old_approval, audience=GATEWAY, now=NOW)
    except VerificationError as exc:
        assert "older than a day" in str(exc)
        return
    raise AssertionError("a two-day-old approval verified")


# ---- the daily budget --------------------------------------------------

def _an_order(*, customer=CUSTOMER, net=100.0, placed_at=NOW, order_id=None) -> OrderRecord:
    """A placed order, which is all daily_net and weekly_net look at."""
    return OrderRecord(
        order_id=order_id or f"o_{int(net)}_{placed_at.timestamp():.0f}",
        quote_id="q_test", customer=customer, agent_id=AGENT, status="placed",
        placed_at=placed_at, requested_date=WHEN, delivery_date=WHEN, lines=[],
        net_total=net, vat_total=0.0, gross_total=net)


def _refusal_for(*, net, already_today=0.0, per_day=None, per_week=5000.0,
                 already_week=0.0) -> str | None:
    """Spend some of the budget, then try an order and report the reason."""
    store = OrderStore()
    if already_today:
        store.add(_an_order(net=already_today, placed_at=NOW, order_id="o_today"))
    if already_week:
        # Earlier in the same ISO week, but not today.
        store.add(_an_order(net=already_week, placed_at=NOW - timedelta(days=2),
                            order_id="o_earlier"))
    try:
        order(mandate=make_mandate(per_order=10_000.0, per_day=per_day, per_week=per_week),
              quote=make_quote({"464306": net}), store=store)
    except OrderRefused as exc:
        return exc.rejection.reason
    return None


def test_a_days_spending_is_counted_in_the_kitchens_local_day():
    """A Helsinki kitchen's day starts at their midnight. Counting in UTC would
    roll over at 02:00 their time and move spending into the wrong day."""
    store = OrderStore()
    # 23:30 local on the 16th is 21:30 UTC on the 16th.
    store.add(_an_order(customer="123", net=100.0,
                        placed_at=datetime(2026, 11, 16, 21, 30, tzinfo=timezone.utc)))
    # 00:30 local on the 17th is 22:30 UTC on the 16th: a different day for the chef.
    store.add(_an_order(customer="123", net=50.0,
                        placed_at=datetime(2026, 11, 16, 22, 30, tzinfo=timezone.utc)))
    on_the_16th = datetime(2026, 11, 16, 20, 0, tzinfo=timezone.utc)
    on_the_17th = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)
    assert store.daily_net("123", on_the_16th) == 100.0
    assert store.daily_net("123", on_the_17th) == 50.0


def test_the_week_and_the_day_agree_on_what_time_it_is():
    """00:30 on Monday in Helsinki is still Sunday in UTC. The day was counted
    locally and the week in UTC, so that order landed in Monday's budget and in
    the week before's."""
    store = OrderStore()
    monday_0030_local = datetime(2026, 11, 15, 22, 30, tzinfo=timezone.utc)   # Mon 16th, 00:30
    store.add(_an_order(net=100.0, placed_at=monday_0030_local))
    later_that_monday = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
    the_sunday_before = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)
    assert store.daily_net(CUSTOMER, later_that_monday) == 100.0
    assert store.weekly_net(CUSTOMER, later_that_monday) == 100.0     # same week as its day
    assert store.weekly_net(CUSTOMER, the_sunday_before) == 0.0       # not last week's


def test_another_customers_spending_is_not_counted():
    store = OrderStore()
    store.add(_an_order(customer="456", net=900.0))
    assert store.daily_net("123", NOW) == 0.0


def test_over_the_daily_budget_is_refused_before_the_weekly_one():
    """The day is the tighter limit and the one a chef recognises."""
    reason = _refusal_for(net=600.0, already_today=1_500.0, per_day=2_000.0, per_week=5_000.0)
    assert reason == "over_daily_limit"


def test_inside_the_day_but_over_the_week_still_says_week():
    reason = _refusal_for(net=100.0, already_today=0.0, per_day=2_000.0,
                          per_week=5_000.0, already_week=4_950.0)
    assert reason == "over_weekly_limit"


def test_a_mandate_with_no_daily_limit_is_still_valid():
    """Mandates signed before the daily budget existed must keep working."""
    assert _refusal_for(net=4_000.0, already_today=0.0, per_day=None, per_week=5_000.0) is None


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(f"\n{passed} passed")
