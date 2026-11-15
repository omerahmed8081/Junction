"""The agent on the recorded hour 0 data: no network, no database, no model.

The gateway is replaced by a stand-in that answers from tests/fixtures and
Data/mock_samples, through the gateway's own response builder, so what the agent
sees here is what the real gateway would have said at 03:40 on 14 November.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gateway.delivery import build_response  # noqa: E402
from kitchen import agent  # noqa: E402
from kitchen.need import parse_with_rules  # noqa: E402
from shared.schemas import LineEdit, ProductSummary, SearchResponse  # noqa: E402
from shared.units import convert_summary  # noqa: E402

SUMMARIES = [ProductSummary(**x) for x in json.loads((ROOT / "tests/fixtures/milk_summaries.json").read_text(encoding="utf-8"))]
SAMPLES = ROOT / "Data" / "mock_samples"
PRICES = {p["productId"]: p for p in json.loads((SAMPLES / "price_milk.json").read_text())["products"]}
AVAILABILITY = {p["productId"]: p for p in json.loads((SAMPLES / "availability_milk.json").read_text())["products"]}
WHEN_RECORDED = datetime.fromisoformat("2026-11-14T03:40:09+02:00")
SATURDAY = date(2026, 11, 14)
QUALIFIED = ("coffee", "cafè", "sour", "portion", "latte")


class RecordedGateway:
    """The 35 lactose-free milks, with the prices and stock recorded at hour 0."""

    def search_products(self, req):
        out = [
            s for s in SUMMARIES
            if all(word in s.name.lower() for word in (req.text or "").lower().split())
            and (not req.brand or req.brand.lower() in (s.vendor or "").lower())
            and (not req.pack_size or (s.size_per_sales_unit and abs(
                s.size_per_sales_unit / s.base_units_per_sales_unit - req.pack_size) <= 0.02 * req.pack_size))
        ]
        return SearchResponse(products=out[: req.limit], total_matched=len(out))

    def check_delivery(self, requested_date, items):
        units = {s.product_id: s.sales_unit for s in SUMMARIES}
        return build_response(items, requested_date, PRICES, AVAILABILITY, units, now=WHEN_RECORDED)


def basket_for(message: str):
    need = parse_with_rules(message, today=SATURDAY)
    return agent.add_need(None, need, RecordedGateway())


def test_rules_parse_the_brief_request():
    need = parse_with_rules("1,000 L lactose-free milk by Tuesday", today=SATURDAY)
    assert (need.quantity, need.unit, need.product_text) == (1000, "L", "milk")
    assert need.claims == ["FREE_FROM_LACTOSE"] and need.requested_date == date(2026, 11, 17)
    assert need.pack_size is None and need.brand is None


def test_rules_parse_pack_size_brand_and_other_units():
    need = parse_with_rules("1000 l Valio lactose free milk in 10 L packs by tuesday", today=SATURDAY)
    assert (need.pack_size, need.pack_size_unit, need.brand, need.quantity) == (10, "L", "Valio", 1000)
    need = parse_with_rules("I need 2,5 kg of butter for Friday please", today=SATURDAY)
    assert (need.quantity, need.unit, need.product_text) == (2.5, "KG", "butter")
    assert need.requested_date == date(2026, 11, 20) and need.claims == []


def test_hour_7_basket():
    # The demo request: "1,000 L lactose-free milk by Tuesday" -> 1,000 ST of 464306,
    # net 1,010.00, VAT 136.35, gross 1,146.35, delivery 2026-11-16.
    state, reply, _ = basket_for("1,000 L lactose-free milk by Tuesday")
    basket = state.basket
    line = basket.lines[0]
    assert (line.product_id, line.quantity_sales_units, line.sales_unit) == ("464306", 1000, "ST")
    assert (line.line_net, line.line_vat, line.line_gross) == (1010.00, 136.35, 1146.35)
    assert (basket.net_total, basket.vat_total, basket.gross_total) == (1010.00, 136.35, 1146.35)
    assert line.delivery.can_fulfil == "full" and line.delivery.delivery_date == date(2026, 11, 16)
    assert (line.delivered_amount, line.overshoot, line.size_source) == (1000, 0, "structured")
    assert line.price_per_comparison_unit == 1.01 and line.comparison_unit == "L"
    assert basket.status == "draft" and basket.quote_jws is None
    assert "464306" not in reply and "1,010.00" in reply


def test_policy_preview_sends_it_to_a_manager():
    policy = basket_for("1,000 L lactose-free milk by Tuesday")[0].basket.policy
    assert policy.level == "manager" and policy.binding is False
    assert policy.reasons == ["Over the EUR 500 chef limit"]
    small = basket_for("100 L lactose-free milk by Tuesday")[0].basket
    assert small.net_total == 101.00 and small.policy.level == "chef" and small.policy.reasons == []


def test_alternatives_are_the_next_best_and_another_pack_size():
    line = basket_for("1,000 L lactose-free milk by Tuesday")[0].basket.lines[0]
    first, second = line.alternatives
    assert first.product_id == "464401" and first.line_net == 1100.00
    assert "EUR 90.00 more" in first.reason
    assert second.sales_unit == "KI" and "instead of 1000 ST" in second.reason
    assert all(a.delivery and a.delivery.can_fulfil != "none" for a in line.alternatives)


def test_nothing_past_its_cutoff_is_offered():
    state, _, _ = basket_for("1,000 L lactose-free milk by Tuesday")
    offered = [state.basket.lines[0].product_id] + [a.product_id for a in state.basket.lines[0].alternatives]
    for pid in offered + list(state.candidates):
        answer = RecordedGateway().check_delivery(date(2026, 11, 17), [
            agent.CheckDeliveryItem(product_id=pid, quantity_sales_units=1)]).results[0]
        assert not answer.delivery.past_closing_time, pid
    assert "463038" not in state.candidates and "464179" not in state.candidates      # both Novoboxes


def test_plain_milk_ranks_before_sour_and_coffee_milk():
    state, _, _ = basket_for("1,000 L lactose-free milk by Tuesday")
    ranked = sorted(state.candidates.values(), key=lambda c: agent._rank_key(c, "L"))
    flags = [c.off_request for c in ranked]
    assert flags == sorted(flags) and any(flags)             # every plain product first
    line = state.basket.lines[0]
    for name in [line.name] + [a.name for a in line.alternatives]:
        assert not any(word in name.lower() for word in QUALIFIED), name
    # ...unless the chef asks for it
    need = parse_with_rules("50 L lactose-free sour milk by Tuesday", today=SATURDAY)
    sour, _, _ = agent.add_need(None, need, RecordedGateway())
    assert "sour" in sour.basket.lines[0].name.lower()


def test_closest_match_is_flagged_when_no_plain_product_can_be_ordered():
    # Coffee milk for a "milk" request is only ever a flagged last resort.
    need = parse_with_rules("1,000 L lactose-free milk by Tuesday", today=SATURDAY)

    class OnlyQualified(RecordedGateway):
        def search_products(self, req):
            found = super().search_products(req)
            keep = [s for s in found.products if any(w in s.name.lower() for w in QUALIFIED)]
            return SearchResponse(products=keep, total_matched=len(keep))

    state, _, _ = agent.add_need(None, need, OnlyQualified())
    line = state.basket.lines[0]
    assert "differs_from_request" in line.flags and "closest" in line.reason
    assert "Product differs from what was asked for" in state.basket.policy.reasons
    plain = basket_for("1,000 L lactose-free milk by Tuesday")[0].basket
    assert "differs_from_request" not in plain.lines[0].flags


def test_kilogram_product_is_never_ranked_against_litres():
    state, _, steps = basket_for("1,000 L lactose-free milk by Tuesday")
    assert "472492" not in state.candidates                   # sold by the kilogram
    # The machine reason stays in `facts`; `detail` is the sentence a chef reads.
    rank = next(s for s in steps if s.step == "rank")
    assert "size_in_KG_but_need_in_L" in rank.facts
    assert "sold by weight" in rank.detail and "size_in_KG" not in rank.detail


def test_no_orderable_product_gives_a_reason_not_an_empty_answer():
    state, reply, _ = basket_for("1,000 L lactose-free milk in 10 L packs by Tuesday")
    assert state.basket.lines == [] and state.basket.policy is None
    assert "cutoff" in reply and "2026-11-17" in reply


def test_partial_delivery_is_visible_and_needs_a_manager():
    state, _, _ = basket_for("1,000 L Arla lactose-free milk by Tuesday")
    line = state.basket.lines[0]
    assert line.delivery.can_fulfil == "partial" and line.delivery.fulfil_amount < 1000
    assert "Partial delivery accepted" in state.basket.policy.reasons and "only" in line.reason


def test_changing_the_quantity_updates_the_totals():
    state, _, _ = basket_for("1,000 L lactose-free milk by Tuesday")
    state.basket.quote_jws = "pretend-it-was-signed"
    reply, _ = agent.apply_edits(state, [LineEdit(product_id="464306", quantity_sales_units=1200)], RecordedGateway())
    line = state.basket.lines[0]
    assert (line.quantity_sales_units, line.line_net, line.line_vat, line.line_gross) == (1200, 1212.00, 163.62, 1375.62)
    assert (line.delivered_amount, line.overshoot) == (1200, 200)
    assert state.basket.gross_total == 1375.62 and "1,212.00" in reply
    assert state.basket.status == "draft" and state.basket.quote_jws is None       # an edit unsigns it


def test_swap_to_an_alternative_and_remove():
    state, _, _ = basket_for("1,000 L lactose-free milk by Tuesday")
    agent.apply_edits(state, [LineEdit(product_id="464306", swap_to_product_id="464401")], RecordedGateway())
    line = state.basket.lines[0]
    assert (line.product_id, line.line_net, state.basket.net_total) == ("464401", 1100.00, 1100.00)
    assert line.alternatives and line.alternatives[0].product_id == "464306"
    reply, _ = agent.apply_edits(state, [LineEdit(product_id="464306", swap_to_product_id="463038")], RecordedGateway())
    assert "not in the basket" in reply
    reply, _ = agent.apply_edits(state, [LineEdit(product_id="464401", swap_to_product_id="463038")], RecordedGateway())
    assert "not an orderable alternative" in reply and state.basket.lines[0].product_id == "464401"
    agent.apply_edits(state, [LineEdit(product_id="464401", remove=True)], RecordedGateway())
    assert state.basket.lines == [] and state.basket.net_total == 0 and state.basket.policy is None


def test_a_second_need_adds_a_line():
    state, _, _ = basket_for("1,000 L lactose-free milk by Tuesday")
    need = parse_with_rules("50 L lactose-free sour milk by Tuesday", today=SATURDAY)
    state, _, _ = agent.add_need(state, need, RecordedGateway())
    assert len(state.basket.lines) == 2
    assert state.basket.net_total == round(sum(l.line_net for l in state.basket.lines), 2)


def test_trace_marks_what_crossed_the_gateway():
    _, _, steps = basket_for("1,000 L lactose-free milk by Tuesday")
    crossed = {s.step: s.crossed_gateway for s in steps}
    assert crossed == {"parse_need": False, "search_products": True, "check_delivery": True,
                       "rank": False, "policy_preview": False}


def test_summary_conversion_matches_the_units_rule():
    by_id = {s.product_id: s for s in SUMMARIES}
    novobox = convert_summary(by_id["463038"], 1000, "L")
    assert (novobox.quantity_sales_units, novobox.size_source, novobox.flags) == (50, "name", ["size_from_product_name"])
    cases = convert_summary(by_id["463779"], 1000, "L")                # 15 x 2.5 dl = 3.75 L per case
    assert (cases.quantity_sales_units, cases.delivered_amount, cases.overshoot) == (267, 1001.25, 1.25)
    assert convert_summary(by_id["472492"], 1000, "L").reason == "size_in_KG_but_need_in_L"
    assert convert_summary(by_id["464306"], 30, "ST").size_source == "not_needed"

def test_a_partial_answer_is_split_across_products_not_over_ordered():
    """462775 has 51 units. Asking for 1,000 L of it must not order 1,000.

    Beat 2 of the demo. The line is reduced to what Aimo can deliver and the
    remainder goes to the next ranked product.
    """
    class OnlyArla(RecordedGateway):
        """Two Arla products, 51 and 384 units, so nothing can cover 1,000 alone."""

        def search_products(self, req):
            keep = [s for s in SUMMARIES if s.product_id in {"462775", "469363"}]
            return SearchResponse(products=keep, total_matched=len(keep))

    need = parse_with_rules("1,000 L lactose-free milk by Tuesday", today=SATURDAY)
    state, reply, steps = agent.add_need(None, need, OnlyArla())
    quantities = {l.product_id: l.quantity_sales_units for l in state.basket.lines}
    # 469363 ranks first on price (EUR 2.48 against 2.57) and has 384 units.
    assert quantities == {"469363": 384.0, "462775": 51.0}, quantities
    assert all("partial_split" in l.flags for l in state.basket.lines)
    # 435 litres of the 1,000 asked for, and the shortfall is stated, not hidden.
    assert sum(l.delivered_amount for l in state.basket.lines) == 435.0
    assert "short_of_need" in state.basket.lines[-1].flags
    assert "short" in state.basket.lines[-1].reason
    assert "split" in reply and "short" in reply
    assert any(s.step == "split_need" for s in steps)
    # Totals follow the reduced quantities, not the full need.
    assert state.basket.net_total == round(384 * 2.48 + 51 * 2.57, 2)
    assert state.basket.policy.level == "manager"


def test_a_product_that_can_cover_the_need_is_not_split():
    need = parse_with_rules("1,000 L lactose-free milk by Tuesday", today=SATURDAY)
    state, _, steps = agent.add_need(None, need, RecordedGateway())
    assert len(state.basket.lines) == 1
    assert not any("partial_split" in l.flags for l in state.basket.lines)
    assert not any(s.step == "split_need" for s in steps)


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(f"\n{passed} passed")


# --------------------------------- a day whose cut-off has gone is not a dead end


class LateGateway(RecordedGateway):
    """Nothing can be delivered until `open_from`, then normal.

    This is `not_deliverable`: Aimo has no schedule line on or before the day
    asked for, which a later day genuinely fixes.
    """

    def __init__(self, open_from: date):
        self.open_from = open_from
        self.asked: list[date] = []

    def check_delivery(self, requested_date, items):
        self.asked.append(requested_date)
        answer = super().check_delivery(requested_date, items)
        if requested_date >= self.open_from:
            return answer
        for row in answer.results:
            row.delivery.can_fulfil = "none"
        return answer


class CutoffGateway(RecordedGateway):
    """Every cut-off has passed, on every day. The shape of the live data:
    `closingTime < now` does not change when a later day is asked for."""

    def __init__(self):
        self.asked: list[date] = []

    def check_delivery(self, requested_date, items):
        self.asked.append(requested_date)
        answer = super().check_delivery(requested_date, items)
        for row in answer.results:
            row.delivery.can_fulfil = "none"
            row.delivery.past_closing_time = True
        return answer


def test_the_agent_moves_to_the_first_day_that_works():
    """It used to say "try a later date" and leave an empty basket, while
    holding the delivery answer that said which date would work."""
    need = parse_with_rules("10 L lactose-free milk by Tuesday", today=SATURDAY)
    wanted = need.requested_date
    gateway = LateGateway(open_from=wanted + timedelta(days=1))

    state, reply, steps = agent.add_need(None, need, gateway)

    assert state.basket.lines, f"still nothing ordered: {reply}"
    assert state.basket.requested_date == wanted + timedelta(days=1)
    assert any(s.step == "change_date" for s in steps), "the chef is not told it moved"
    assert gateway.asked[0] == wanted, "it did not try the day that was asked for first"


def test_it_gives_up_rather_than_wandering_forward_for_ever():
    need = parse_with_rules("10 L lactose-free milk by Tuesday", today=SATURDAY)
    gateway = LateGateway(open_from=need.requested_date + timedelta(days=60))

    state, reply, _ = agent.add_need(None, need, gateway)

    assert not state.basket.lines
    assert len(gateway.asked) <= agent.LOOK_AHEAD_DAYS + 1, gateway.asked
    assert state.basket.requested_date == need.requested_date, "the day moved anyway"


def test_a_basket_that_already_has_lines_is_never_moved_underneath_the_chef():
    """Those lines carry a price and a delivery answer for the day they were
    priced on. Moving the day under them would make those figures wrong."""
    state, _, _ = basket_for("1,000 L lactose-free milk by Tuesday")
    priced_on = state.basket.requested_date
    assert state.basket.lines

    need = parse_with_rules("10 L lactose-free milk by Tuesday", today=SATURDAY)
    gateway = LateGateway(open_from=need.requested_date + timedelta(days=1))
    agent.add_need(state, need, gateway)

    assert state.basket.requested_date == priced_on, "the day moved under priced lines"
    assert gateway.asked == [need.requested_date], "it looked further anyway"


def test_a_product_that_is_blocked_is_not_retried_on_another_day():
    """A later day fixes a cut-off. It does not fix a sales block or a missing
    price, so looking again would just cost the chef time."""
    need = parse_with_rules("10 L lactose-free milk by Tuesday", today=SATURDAY)

    class Blocked(RecordedGateway):
        def __init__(self): self.asked = []
        def check_delivery(self, requested_date, items):
            self.asked.append(requested_date)
            answer = super().check_delivery(requested_date, items)
            for row in answer.results:
                row.delivery.can_fulfil = "none"
                row.delivery.sales_block = True
            return answer

    gateway = Blocked()
    state, _, _ = agent.add_need(None, need, gateway)
    assert not state.basket.lines
    assert gateway.asked == [need.requested_date], "it looked at other days for a blocked product"


def test_a_passed_cutoff_is_not_chased_across_later_days():
    """`past_cutoff` is `closingTime < now`, a fact about when the order is
    placed rather than the day it is for. scripts/cutoff_check.py against the
    live gateway shows the same product reporting the same cut-off for every
    delivery day, so looking ahead would buy three more identical refusals.
    """
    need = parse_with_rules("10 L lactose-free milk by Tuesday", today=SATURDAY)
    gateway = CutoffGateway()

    state, reply, _ = agent.add_need(None, need, gateway)

    assert not state.basket.lines
    assert gateway.asked == [need.requested_date], f"it looked ahead anyway: {gateway.asked}"
