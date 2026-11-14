"""The whole flow: a sentence becomes an order, offline.

Nothing is stubbed that matters. The real agent builds the basket from the
recorded hour 0 responses, Aimo's real quote service signs a real Ed25519 quote,
the kitchen's real signer signs a real mandate and approval, and Aimo's real
order service runs all ten of its checks. Only the HTTP hop and the database are
stood in for.

So if this file passes, the demo's first beat works.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from gateway import orders as gateway_orders  # noqa: E402
from gateway import quotes as gateway_quotes  # noqa: E402
from kitchen import agent, keys, ordering  # noqa: E402
from kitchen.gateway_client import GatewayRefused  # noqa: E402
from kitchen.need import parse_with_rules  # noqa: E402
from kitchen.ordering import KitchenSigner, OrderingError  # noqa: E402
from shared.schemas import (  # noqa: E402
    CheckDeliveryItem,
    CheckDeliveryResponse,
    LineEdit,
    PlaceOrderRequest,
)
from shared.signing import Signer, Verifier, generate_keypair  # noqa: E402
from test_agent import SATURDAY, RecordedGateway  # noqa: E402
from test_orders import AVAILABILITY, SUMMARIES, results_for  # noqa: E402

# test_orders keeps them keyed by product id, which is what build_quote wants.

GATEWAY_URL = "http://gateway.test"
KITCHEN_URL = "http://kitchen.test/restaurant-demo"

AIMO_KEYS = generate_keypair("aimo-test")
AIMO_SIGNER = Signer(AIMO_KEYS)

# The kitchen's key would be written to .keys/ in a real run; a temporary folder
# keeps the tests from creating one.
_TEMP = tempfile.TemporaryDirectory()
keys.KEY_DIR = Path(_TEMP.name)
keys.GATEWAY_URL = GATEWAY_URL
keys.KITCHEN_URL = KITCHEN_URL
keys.aimo_verifier = lambda timeout=5.0: Verifier([AIMO_KEYS.public_jwk])
ordering.keys = keys

NOW = datetime(2026, 11, 14, 14, 0, tzinfo=timezone.utc)


class StubGateway(RecordedGateway):
    """The gateway's own quote and order services, without the HTTP hop.

    Search, price and delivery come from the recorded responses; quoting,
    signing and the order checks are the real code.
    """

    def __init__(self, availability=None):
        self.registry = gateway_quotes.QuoteRegistry()
        self.store = gateway_orders.OrderStore()
        self.availability = AVAILABILITY if availability is None else availability
        self.placed: list[PlaceOrderRequest] = []

    def _results(self, requested_date, quantities, now=None):
        return results_for(quantities, requested_date, now or NOW, self.availability)

    def check_delivery(self, requested_date, items):
        """Overridden so a re-check sees this stub's stock, not the recorded one.

        RecordedGateway reads the module-level availability; here it has to
        follow `self.availability`, which a test changes to move stock.
        """
        quantities = {i.product_id: i.quantity_sales_units for i in items}
        results = self._results(requested_date, quantities)
        return CheckDeliveryResponse(
            requested_date=requested_date, checked_at=NOW,
            results=[results[i.product_id] for i in items if i.product_id in results])

    def create_quote(self, agent_id, requested_date, lines):
        quantities = {l.product_id: l.quantity_sales_units for l in lines}
        return gateway_quotes.build_quote(
            issuer=GATEWAY_URL, customer_id="123", agent_id=agent_id,
            requested_date=requested_date, summaries=SUMMARIES,
            results=self._results(requested_date, quantities),
            wanted=quantities, signer=AIMO_SIGNER, now=NOW,
        )

    def place_order(self, request: PlaceOrderRequest):
        self.placed.append(request)

        def recheck(requested_date, items):
            quantities = {i.product_id: i.quantity_sales_units for i in items}
            return self._results(requested_date, quantities)

        try:
            return gateway_orders.place_order(
                request=request, agent_id=ordering.AGENT_ID, gateway_url=GATEWAY_URL,
                own_verifier=Verifier([AIMO_KEYS.public_jwk]),
                kitchen_verifier=Verifier([keys.kitchen_keypair().public_jwk]),
                recheck_delivery=recheck, registry=self.registry, store=self.store,
                signer=AIMO_SIGNER, new_order_id=gateway_quotes.new_order_id, now=NOW,
            )
        except gateway_orders.OrderRefused as exc:
            # What the HTTP layer turns a 409 into.
            raise GatewayRefused(exc.rejection.reason, exc.rejection.detail,
                                 exc.rejection.security_event,
                                 exc.rejection.model_dump()) from exc


def drafted(message="1,000 L lactose-free milk by Tuesday", gateway=None):
    gateway = gateway or StubGateway()
    need = parse_with_rules(message, today=SATURDAY)
    state, _, _ = agent.add_need(None, need, gateway)
    return state, gateway, KitchenSigner()


def ordered(message="1,000 L lactose-free milk by Tuesday", role="manager", gateway=None):
    state, gateway, kitchen = drafted(message, gateway)
    ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    result = ordering.approve(state, role, gateway, kitchen, now=NOW)
    assert result.outcome == "ordered", result.reply
    return state.basket, result.order, result.reply, result.steps, gateway


# ----------------------------------------------------------------- the flow


def test_a_sentence_becomes_an_order():
    basket, record, reply, steps, _ = ordered()
    assert basket.status == "ordered" and basket.order_id == record.order_id
    assert record.net_total == 1010.00 and record.vat_total == 136.35
    assert record.delivery_date == date(2026, 11, 16)
    assert record.approver_role == "manager"
    assert "Ordered." in reply and record.order_id in reply


def test_confirm_moves_the_basket_to_quoted_and_holds_what_aimo_signed():
    state, gateway, kitchen = drafted()
    assert state.basket.status == "draft" and state.basket.quote_jws is None
    reply, steps = ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    assert state.basket.status == "quoted"
    assert state.basket.quote_verified is True
    assert state.basket.quote_id and state.basket.quote_jws
    assert state.basket.net_total == 1010.00
    assert state.basket.policy.binding is True          # no longer a preview
    assert "signed" in reply.lower()
    assert [s.step for s in steps] == ["create_quote", "verify_quote", "policy_binding"]
    assert [s.crossed_gateway for s in steps] == [True, False, False]


def test_the_confirmed_figures_come_from_the_verified_quote():
    """Not from our own arithmetic: Aimo's are what it will stand behind."""
    state, gateway, kitchen = drafted()
    ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    payload = Verifier([AIMO_KEYS.public_jwk]).verify(
        state.basket.quote_jws, issuer=GATEWAY_URL, audience=ordering.AGENT_ID, now=NOW)
    assert payload["net_total"] == state.basket.net_total
    assert payload["lines"][0]["line_net"] == state.basket.lines[0].line_net


def test_aimo_signs_a_confirmation_the_kitchen_can_verify():
    _, record, _, _, _ = ordered()
    payload = Verifier([AIMO_KEYS.public_jwk]).verify(
        record.confirmation_jws, issuer=GATEWAY_URL, audience=ordering.AGENT_ID, now=NOW)
    assert payload["order_id"] == record.order_id and payload["approver_role"] == "manager"


def test_the_order_carried_a_mandate_and_an_approval():
    _, _, _, _, gateway = ordered()
    sent = gateway.placed[-1]
    assert sent.quote_jws and sent.mandate_jws and sent.approval_jws
    kitchen_verifier = Verifier([keys.kitchen_keypair().public_jwk])
    mandate = kitchen_verifier.verify(sent.mandate_jws, audience=GATEWAY_URL, now=NOW)
    approval = kitchen_verifier.verify(sent.approval_jws, audience=GATEWAY_URL, now=NOW)
    assert mandate["sub"] == ordering.AGENT_ID and mandate["approval_required"] is True
    assert approval["approver_role"] == "manager"
    quote = Verifier([AIMO_KEYS.public_jwk]).verify(sent.quote_jws, now=NOW)
    assert approval["quote_jti"] == quote["jti"]         # the approval names this quote


# ------------------------------------------------------------- the refusals


def test_a_draft_cannot_be_approved():
    state, gateway, kitchen = drafted()
    try:
        ordering.approve(state, "manager", gateway, kitchen, now=NOW)
    except OrderingError as exc:
        assert "confirm" in str(exc)
        return
    raise AssertionError("an unconfirmed basket was approved")


def test_a_chef_cannot_approve_what_needs_a_manager():
    """EUR 1,010 is over the EUR 500 chef limit, so the policy says manager."""
    state, gateway, kitchen = drafted()
    ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    assert state.basket.policy.level == "manager"
    try:
        ordering.approve(state, "chef", gateway, kitchen, now=NOW)
    except OrderingError as exc:
        assert "needs manager approval" in str(exc)
        return
    raise AssertionError("a chef approved a manager's basket")


def test_a_chef_can_approve_a_small_basket():
    state, gateway, kitchen = drafted("100 L lactose-free milk by Tuesday")
    ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    assert state.basket.policy.level == "chef" and state.basket.net_total == 101.00
    result = ordering.approve(state, "chef", gateway, kitchen, now=NOW)
    assert result.outcome == "ordered"
    assert result.order.status == "placed" and result.order.approver_role == "chef"


def test_an_unknown_role_cannot_approve():
    state, gateway, kitchen = drafted()
    ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    for role in ("", "intern", "the chef's cousin"):
        try:
            ordering.approve(state, role, gateway, kitchen, now=NOW)
        except OrderingError:
            continue
        raise AssertionError(f"{role!r} approved an order")


def test_editing_a_quoted_basket_voids_the_quote():
    """Why the quote is signed at confirm and not at draft."""
    state, gateway, kitchen = drafted()
    ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    assert state.basket.status == "quoted"
    agent.apply_edits(state, [LineEdit(product_id="464306", quantity_sales_units=1200)], gateway)
    assert state.basket.status == "draft"
    assert state.basket.quote_jws is None and state.basket.quote_verified is False
    try:
        ordering.approve(state, "manager", gateway, kitchen, now=NOW)
    except OrderingError:
        pass
    else:
        raise AssertionError("a voided quote was approved")
    # Confirming again re-prices at the new quantity and can be approved.
    ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    assert state.basket.net_total == 1212.00
    result = ordering.approve(state, "manager", gateway, kitchen, now=NOW)
    assert result.outcome == "ordered" and result.order.net_total == 1212.00


def test_the_same_basket_cannot_be_ordered_twice():
    state, gateway, kitchen = drafted()
    ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    ordering.approve(state, "manager", gateway, kitchen, now=NOW)
    try:
        ordering.approve(state, "manager", gateway, kitchen, now=NOW)
    except OrderingError as exc:
        assert "already been ordered" in str(exc)
        return
    raise AssertionError("the same basket was ordered twice")


def test_an_expired_quote_sends_the_basket_back_to_draft():
    state, gateway, kitchen = drafted()
    ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    state.basket.quote_expires = datetime.now(timezone.utc) - timedelta(minutes=1)
    try:
        ordering.approve(state, "manager", gateway, kitchen, now=NOW)
    except OrderingError as exc:
        assert "expired" in str(exc)
        assert state.basket.status == "draft" and state.basket.quote_jws is None
        return
    raise AssertionError("an expired quote was approved")


def test_a_quote_the_kitchen_cannot_verify_blocks_the_basket():
    """A quote we cannot check is worse than none: it would put unchecked
    figures on screen."""
    state, gateway, kitchen = drafted()
    stranger = Verifier([generate_keypair("stranger").public_jwk])
    original, keys.aimo_verifier = keys.aimo_verifier, lambda timeout=5.0: stranger
    try:
        ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    except OrderingError as exc:
        assert "did not verify" in str(exc)
        assert state.basket.status == "blocked" and state.basket.quote_verified is False
    else:
        raise AssertionError("an unverifiable quote was accepted")
    finally:
        keys.aimo_verifier = original


def test_stock_that_moves_between_confirm_and_approve_is_refused():
    state, gateway, kitchen = drafted()
    ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    moved = dict(AVAILABILITY)
    moved["464306"] = {**AVAILABILITY["464306"],
                       "scheduleLines": [{**AVAILABILITY["464306"]["scheduleLines"][0],
                                          "availableAmount": 400.0}]}
    gateway.availability = moved
    result = ordering.approve(state, "manager", gateway, kitchen, now=NOW)

    # Not a dead end: the basket now matches what Aimo says, and says why.
    assert result.outcome == "adjusted" and result.order is None
    assert result.refusal.reason == "stock_changed"
    assert state.basket.status == "draft" and state.basket.quote_jws is None
    assert state.basket.lines[0].quantity_sales_units == 400.0
    assert state.basket.net_total == 404.00            # 400 x EUR 1.01
    assert "reduced" in result.reply and "Confirm again" in result.reply
    # ... and something that could cover the other 600 litres is suggested.
    assert "cover the shortfall" in result.reply.lower()
    suggested = [a for a in state.basket.lines[0].alternatives
                 if "covers_shortfall" in a.flags]
    assert suggested, [a.flags for a in state.basket.lines[0].alternatives]


def test_over_the_mandate_limit_is_refused_by_aimo_not_by_us():
    state, gateway, kitchen = drafted()
    kitchen.issue_mandate(per_order=500.0)
    ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    result = ordering.approve(state, "manager", gateway, kitchen, now=NOW)
    assert result.outcome == "refused" and result.order is None
    assert result.refusal.reason == "over_order_limit"
    assert "would not accept" in result.reply


def test_a_mandate_is_issued_on_first_use_and_reused_after():
    kitchen = KitchenSigner()
    assert kitchen.mandate is None
    first = kitchen.mandate_in_force()
    assert kitchen.mandate_in_force().payload.jti == first.payload.jti
    assert first.payload.aud == GATEWAY_URL and first.payload.sub == ordering.AGENT_ID
    assert first.expires_at > datetime.now(timezone.utc)


def test_an_empty_basket_cannot_be_confirmed():
    state, gateway, kitchen = drafted()
    state.basket.lines = []
    try:
        ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    except OrderingError as exc:
        assert "nothing in the basket" in str(exc)
        return
    raise AssertionError("an empty basket was confirmed")

# ---------------------------------------------------------------- persistence


def test_baskets_orders_and_the_mandate_survive_a_restart():
    """Restarting the service mid-demo must not lose a basket from the screen."""
    from kitchen import storage

    state, gateway, kitchen = drafted()
    ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    record = ordering.approve(state, "manager", gateway, kitchen, now=NOW).order

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "kitchen.json"
        storage.save({state.basket.basket_id: state}, {record.order_id: record},
                     kitchen.mandate, path=path)
        baskets, orders, mandate, services = storage.load(path)

    restored = baskets[state.basket.basket_id]
    assert restored.basket.status == "ordered"
    assert restored.basket.order_id == record.order_id
    assert restored.basket.net_total == 1010.00
    assert restored.basket.quote_jws == state.basket.quote_jws
    assert restored.needs["464306"].quantity == 1000
    assert orders[record.order_id].net_total == 1010.00
    assert mandate.jws == kitchen.mandate.jws
    # The restored mandate still verifies, so it can be used for the next order.
    from shared.signing import Verifier
    Verifier([keys.kitchen_keypair().public_jwk]).verify(
        mandate.jws, audience=GATEWAY_URL, now=NOW)


def test_a_missing_or_corrupt_state_file_starts_empty_rather_than_crashing():
    from kitchen import storage

    with tempfile.TemporaryDirectory() as folder:
        missing = Path(folder) / "nothing.json"
        assert storage.load(missing) == ({}, {}, None, [])
        broken = Path(folder) / "broken.json"
        broken.write_text("{not json at all", encoding="utf-8")
        assert storage.load(broken) == ({}, {}, None, [])


def test_one_unreadable_basket_does_not_cost_us_the_others():
    from kitchen import storage

    state, gateway, kitchen = drafted()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "kitchen.json"
        storage.save({state.basket.basket_id: state}, {}, None, path=path)
        import json
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["baskets"]["b_broken"] = {"basket": {"nonsense": True}}
        path.write_text(json.dumps(payload), encoding="utf-8")
        baskets, _, _, _ = storage.load(path)
    assert list(baskets) == [state.basket.basket_id]


def test_a_swap_still_works_when_the_candidates_were_not_persisted():
    """After a restart only the need survives, so the agent searches again."""
    from kitchen import storage

    state, gateway, kitchen = drafted()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "kitchen.json"
        storage.save({state.basket.basket_id: state}, {}, None, path=path)
        baskets, _, _, _ = storage.load(path)
    restored = baskets[state.basket.basket_id]
    assert restored.candidates == {}            # not persisted, by design
    reply, _ = agent.apply_edits(
        restored, [LineEdit(product_id="464306", swap_to_product_id="464401")], gateway)
    assert "Swapped" in reply
    assert [l.product_id for l in restored.basket.lines] == ["464401"]


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(f"\n{passed} passed")
