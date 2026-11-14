"""From a parsed need to a priced basket (NIGHT_SHIFT brief 1, step 5).

No model in this file. The model's part ends at kitchen/need.py; from here on
every quantity comes from shared/units.py and every euro from shared/money.py.

    search_products -> convert units -> check_delivery -> drop what cannot be
    ordered -> rank -> basket line with two alternatives -> policy preview

A product is orderable only if it has a price, is not sales-blocked, its cutoff
has not passed, something can be delivered by the date, and its size could be
worked out in the unit the chef asked in.
"""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from kitchen.gateway_client import GatewayClient
from kitchen.need import ParsedNeed
from shared.money import basket_totals, line_totals
from shared.schemas import (
    AgentStep,
    Alternative,
    Basket,
    BasketLine,
    CheckDeliveryItem,
    DeliveryAnswer,
    LineEdit,
    PolicyDecision,
    PriceAnswer,
    ProductSummary,
    SearchRequest,
)
from shared.units import NEED_TO_KILOS, NEED_TO_LITRES, Conversion, convert_summary

SHORTLIST = 40
CHEF_LIMIT_EUR = float(os.environ.get("CHEF_LIMIT_EUR", "500"))

# Words that make a product a different thing from the plain one. A chef asking
# for "milk" does not mean sour milk or coffee milk, though Aimo files them in
# the same category. A product carrying one of these that the request did not
# mention ranks after every plain product, whatever its price.
QUALIFIERS = {
    "sour", "coffee", "cafe", "cafè", "café", "latte", "portion", "chocolate", "cocoa",
    "vanilla", "strawberry", "banana", "flavoured", "flavored", "kefir", "acidofilus",
}


@dataclass
class Candidate:
    summary: ProductSummary
    conversion: Conversion
    price: PriceAnswer | None = None
    delivery: DeliveryAnswer | None = None
    rejected: str = ""                 # why it cannot go in the basket, "" when it can
    off_request: bool = False          # carries a qualifier the chef did not ask for

    @property
    def net(self) -> float:
        return line_totals(self.price.price_per_sales_unit, self.conversion.quantity_sales_units,
                           self.price.tax_rate_percent)["line_net"]


@dataclass
class BasketState:
    """What the kitchen service remembers about one basket between calls."""

    basket: Basket
    needs: dict[str, ParsedNeed] = field(default_factory=dict)            # line product_id -> its need
    candidates: dict[str, Candidate] = field(default_factory=dict)        # every orderable product seen


def _step(steps: list[AgentStep], step: str, detail: str, crossed: bool = False) -> None:
    steps.append(AgentStep(step=step, detail=detail, crossed_gateway=crossed, at=datetime.now(timezone.utc)))


def _kind(unit: str) -> str:
    """"L", "KG", or "" when the need is already in sales units."""
    unit = (unit or "").upper()
    return "L" if unit in NEED_TO_LITRES else "KG" if unit in NEED_TO_KILOS else ""


def _derived_size(price: PriceAnswer | None, kind: str) -> float | None:
    """Size of one sales unit from Aimo's own comparison price, when it is in our unit."""
    if not price or not price.price_per_sales_unit or not price.comparison_price:
        return None
    if not kind or (price.comparison_unit or "").upper() != kind:
        return None
    return price.price_per_sales_unit / price.comparison_price


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-zà-ÿ]+", (text or "").lower()))


def _reject(c: Candidate, kind: str) -> str:
    if not c.conversion.ok:
        return c.conversion.reason or "no_quantity"
    if not c.price or c.price.unavailable_reason or c.price.price_per_sales_unit is None:
        return (c.price.unavailable_reason if c.price else None) or "no_price"
    if kind and c.price.comparison_unit and c.price.comparison_unit.upper() != kind:
        return f"priced_per_{c.price.comparison_unit.upper()}_not_{kind}"
    if not c.delivery or c.delivery.sales_block:
        return "sales_block" if c.delivery else "no_delivery_answer"
    if c.delivery.past_closing_time:
        return "past_cutoff"
    if c.delivery.can_fulfil == "none":
        return "not_deliverable"
    return ""


def _unit_price(c: Candidate, kind: str) -> float:
    """What ranking compares: Aimo's price per litre or kilogram, else the price per sales unit."""
    if kind and c.price.comparison_price and (c.price.comparison_unit or "").upper() == kind:
        return c.price.comparison_price
    if kind and c.conversion.size_per_sales_unit:
        return c.price.price_per_sales_unit / c.conversion.size_per_sales_unit
    return c.price.price_per_sales_unit


def _rank_key(c: Candidate, kind: str):
    s = c.summary
    return (
        c.off_request,                                        # plain products first
        c.delivery.can_fulfil != "full",                      # then the ones that cover the whole need
        _unit_price(c, kind),                                 # then price
        s.delivery_reliability is None, -(s.delivery_reliability or 0.0),   # unknown never beats known
        s.carbon_kg_per_kg is None, s.carbon_kg_per_kg or 0.0,
        s.product_id,
    )


def find_candidates(need: ParsedNeed, gateway: GatewayClient, steps: list[AgentStep]) -> tuple[list[Candidate], dict[str, int]]:
    """Every product the search returned, each marked orderable or with the reason it is not."""
    kind = _kind(need.unit)
    found = gateway.search_products(SearchRequest(
        text=need.product_text, claims=need.claims, brand=need.brand,
        pack_size=need.pack_size, pack_size_unit=need.pack_size_unit or "", limit=SHORTLIST,
    ))
    _step(steps, "search_products", f"'{need.product_text}' {need.claims or ''}: "
          f"{len(found.products)} of {found.total_matched} candidates", crossed=True)

    candidates = [Candidate(s, convert_summary(s, need.quantity, need.unit)) for s in found.products]
    asked = _words(need.product_text)
    for c in candidates:
        c.off_request = bool((_words(c.summary.name) & QUALIFIERS) - asked)

    # Ask about every product that has a quantity, plus one unit of each product whose
    # size we do not know: Aimo's comparison price may tell us the size.
    ask = [c for c in candidates if c.conversion.ok or c.conversion.reason == "size_unknown"]
    if ask:
        answer = gateway.check_delivery(need.requested_date, [
            CheckDeliveryItem(product_id=c.summary.product_id,
                              quantity_sales_units=c.conversion.quantity_sales_units if c.conversion.ok else 1)
            for c in ask
        ])
        by_id = {r.product_id: r for r in answer.results}
        retry = []
        for c in ask:
            r = by_id.get(c.summary.product_id)
            if not r:
                continue
            c.price, c.delivery = r.price, r.delivery
            derived = _derived_size(c.price, kind)
            if c.conversion.ok and derived:
                # Same quantity; this only adds the flag when the two sizes disagree.
                c.conversion = convert_summary(c.summary, need.quantity, need.unit, derived)
            elif not c.conversion.ok and derived:
                c.conversion = convert_summary(c.summary, need.quantity, need.unit, derived)
                if c.conversion.ok:
                    retry.append(c)
        _step(steps, "check_delivery", f"{len(ask)} products for {need.requested_date}", crossed=True)
        if retry:
            again = gateway.check_delivery(need.requested_date, [
                CheckDeliveryItem(product_id=c.summary.product_id,
                                  quantity_sales_units=c.conversion.quantity_sales_units) for c in retry
            ])
            for r in again.results:
                c = next(x for x in retry if x.summary.product_id == r.product_id)
                c.price, c.delivery = r.price, r.delivery
            _step(steps, "check_delivery", f"{len(retry)} more, size taken from the price", crossed=True)

    reasons: dict[str, int] = {}
    for c in candidates:
        c.rejected = _reject(c, kind)
        if c.rejected:
            reasons[c.rejected] = reasons.get(c.rejected, 0) + 1
    ok = sorted((c for c in candidates if not c.rejected), key=lambda c: _rank_key(c, kind))
    _step(steps, "rank", f"{len(ok)} orderable, {len(candidates) - len(ok)} dropped {reasons or ''}".strip())
    return ok, reasons


def _per(kind: str) -> str:
    return {"L": "litre", "KG": "kilogram"}.get(kind, "unit")


def _alternative(c: Candidate, winner: Candidate, kind: str, why: str) -> Alternative:
    delta = round(c.net - winner.net, 2)
    cost = "same total" if delta == 0 else f"EUR {abs(delta):,.2f} {'more' if delta > 0 else 'less'}"
    packs = f"{c.conversion.quantity_sales_units:g} {c.summary.sales_unit}"
    same = (c.conversion.quantity_sales_units == winner.conversion.quantity_sales_units
            and c.summary.sales_unit == winner.summary.sales_unit)
    detail = (f"EUR {_unit_price(c, kind):.2f} per {_per(kind)}" if same else
              f"{packs} instead of {winner.conversion.quantity_sales_units:g} {winner.summary.sales_unit}")
    return Alternative(
        product_id=c.summary.product_id, name=c.summary.name,
        quantity_sales_units=c.conversion.quantity_sales_units, sales_unit=c.summary.sales_unit,
        line_net=c.net,
        price_per_comparison_unit=c.price.comparison_price, comparison_unit=c.price.comparison_unit,
        delivery=c.delivery,
        reason=f"{why}: {detail}, {cost}",
        flags=list(c.conversion.flags),
    )


def _alternatives(winner: Candidate, ranked: list[Candidate], kind: str) -> list[Alternative]:
    """The next best product, and the best one in a different pack size."""
    others = [c for c in ranked if c is not winner]
    out: list[Alternative] = []
    if others:
        out.append(_alternative(others[0], winner, kind, "Next best"))
    base = winner.conversion.size_per_base_unit
    for c in others[1:]:
        size = c.conversion.size_per_base_unit
        if base and size and abs(size - base) / base > 0.02:
            out.append(_alternative(c, winner, kind, "Different pack size"))
            break
    return out


def build_line(c: Candidate, need: ParsedNeed, ranked: list[Candidate]) -> BasketLine:
    kind = _kind(need.unit)
    conv, s, price = c.conversion, c.summary, c.price
    totals = line_totals(price.price_per_sales_unit, conv.quantity_sales_units, price.tax_rate_percent)
    covers = "full amount" if c.delivery.can_fulfil == "full" else f"only {c.delivery.fulfil_amount:g} {s.sales_unit}"
    position = "Cheapest" if ranked and ranked[0] is c else "Chosen"
    return BasketLine(
        product_id=s.product_id, name=s.name,
        need_value=need.quantity, need_unit=need.unit.upper(),
        sales_unit=s.sales_unit, base_unit=s.base_unit,
        base_units_per_sales_unit=s.base_units_per_sales_unit,
        size_per_base_unit=conv.size_per_base_unit, size_per_sales_unit=conv.size_per_sales_unit,
        size_unit=conv.size_unit, size_source=conv.size_source,
        quantity_sales_units=conv.quantity_sales_units,
        delivered_amount=conv.delivered_amount, overshoot=conv.overshoot,
        price_per_sales_unit=price.price_per_sales_unit, price_unit=price.price_unit or s.sales_unit,
        price_per_comparison_unit=price.comparison_price, comparison_unit=price.comparison_unit,
        **totals,
        delivery=c.delivery, origin=s.origin, claims=s.claims, labels=s.labels,
        delivery_reliability=s.delivery_reliability, carbon_kg_per_kg=s.carbon_kg_per_kg,
        reason=(f"{position} match per {_per(kind)} (EUR {_unit_price(c, kind):.2f}) of {len(ranked)} orderable; "
                f"{covers} deliverable {c.delivery.delivery_date}"
                + ("; no plain match could be ordered, so this is the closest" if c.off_request else "")),
        flags=list(conv.flags) + (["differs_from_request"] if c.off_request else []),
        alternatives=_alternatives(c, ranked, kind),
    )


def preview_policy(basket: Basket) -> PolicyDecision | None:
    """Who will have to approve, shown on the draft. Binding only once it is decided on a quote."""
    if not basket.lines:
        return None
    reasons = []
    if basket.net_total > CHEF_LIMIT_EUR:
        reasons.append(f"Over the EUR {CHEF_LIMIT_EUR:g} chef limit")
    if any(l.delivery and l.delivery.can_fulfil == "partial" for l in basket.lines):
        reasons.append("Partial delivery accepted")
    if any("differs_from_request" in l.flags for l in basket.lines):
        reasons.append("Product differs from what was asked for")
    if any("size_disagrees_with_price" in l.flags for l in basket.lines):
        reasons.append("Pack size disagrees with the price data")
    return PolicyDecision(level="manager" if reasons else "chef", reasons=reasons, binding=False)


def _refresh(basket: Basket) -> None:
    """Totals, policy preview, and back to an unsigned draft. Run after every change."""
    totals = basket_totals([{"line_net": l.line_net, "line_vat": l.line_vat} for l in basket.lines])
    basket.net_total, basket.vat_total, basket.gross_total = (
        totals["net_total"], totals["vat_total"], totals["gross_total"])
    basket.policy = preview_policy(basket)
    basket.status = "draft"
    basket.quote_id = basket.quote_jws = basket.quote_expires = None
    basket.quote_verified = False


def _explain_empty(need: ParsedNeed, reasons: dict[str, int]) -> str:
    words = {
        "past_cutoff": "past their order cutoff", "not_deliverable": "not deliverable by then",
        "size_unknown": "of unknown pack size", "sales_block": "blocked for sale", "no_price": "without a price",
    }
    if not reasons:
        return f"I found no products matching '{need.product_text}'."
    parts = [f"{n} {words.get(r, r.replace('_', ' '))}" for r, n in sorted(reasons.items(), key=lambda x: -x[1])]
    return (f"Nothing matching '{need.product_text}' can be ordered for {need.requested_date}: "
            + ", ".join(parts) + ". Try a later date or a different pack size.")


def add_need(state: BasketState | None, need: ParsedNeed, gateway: GatewayClient) -> tuple[BasketState, str, list[AgentStep]]:
    """Add one line for this need to the basket (a new basket when state is None)."""
    steps: list[AgentStep] = []
    _step(steps, "parse_need", f"{need.quantity:g} {need.unit} '{need.product_text}' {need.claims or ''} "
          f"by {need.requested_date} ({need.parser})".replace("  ", " "))
    state = state or BasketState(Basket(basket_id=f"b_{uuid.uuid4().hex[:10]}", requested_date=need.requested_date))

    ranked, reasons = find_candidates(need, gateway, steps)
    if not ranked:
        _refresh(state.basket)
        return state, _explain_empty(need, reasons), steps

    winner = ranked[0]
    line = build_line(winner, need, ranked)
    state.basket.lines = [l for l in state.basket.lines if l.product_id != line.product_id] + [line]
    state.basket.requested_date = need.requested_date
    state.needs[line.product_id] = need
    state.candidates.update({c.summary.product_id: c for c in ranked})
    _refresh(state.basket)
    _step(steps, "policy_preview", f"{state.basket.policy.level}: {state.basket.policy.reasons or 'within the chef limit'}")

    approval = ("A manager will need to approve it." if state.basket.policy.level == "manager"
                else "You can approve it yourself.")
    reply = (f"Drafted {line.quantity_sales_units:g} {line.sales_unit} of {line.name}: "
             f"EUR {line.line_net:,.2f} net, delivery {line.delivery.delivery_date}. {approval}")
    return state, reply, steps


def apply_edits(state: BasketState, edits: list[LineEdit], gateway: GatewayClient) -> tuple[str, list[AgentStep]]:
    """Quantity change, swap to an alternative, or remove. Always ends as an unsigned draft."""
    steps: list[AgentStep] = []
    basket, notes = state.basket, []
    for edit in edits:
        line = next((l for l in basket.lines if l.product_id == edit.product_id), None)
        if not line:
            notes.append(f"{edit.product_id} is not in the basket.")
            continue

        if edit.remove:
            basket.lines.remove(line)
            state.needs.pop(line.product_id, None)
            notes.append(f"Removed {line.name}.")

        elif edit.swap_to_product_id:
            target, need = state.candidates.get(edit.swap_to_product_id), state.needs.get(line.product_id)
            if not target or not need:
                notes.append(f"{edit.swap_to_product_id} is not an orderable alternative for this line.")
                continue
            kind = _kind(need.unit)
            ranked = sorted(state.candidates.values(), key=lambda c: _rank_key(c, kind))
            basket.lines[basket.lines.index(line)] = build_line(target, need, ranked)
            state.needs[target.summary.product_id] = state.needs.pop(line.product_id)
            notes.append(f"Swapped to {target.summary.name}.")

        elif edit.quantity_sales_units is not None:
            if edit.quantity_sales_units <= 0:
                notes.append("A quantity must be above 0; use remove to drop a line.")
                continue
            quantity = float(edit.quantity_sales_units)
            answer = gateway.check_delivery(basket.requested_date, [
                CheckDeliveryItem(product_id=line.product_id, quantity_sales_units=quantity)]).results[0]
            _step(steps, "check_delivery", f"{line.product_id} x {quantity:g} for {basket.requested_date}", crossed=True)
            price = answer.price.price_per_sales_unit or line.price_per_sales_unit
            line.quantity_sales_units = quantity
            line.delivery = answer.delivery
            line.price_per_sales_unit = price
            factor = NEED_TO_LITRES.get(line.need_unit) or NEED_TO_KILOS.get(line.need_unit)
            if factor and line.size_per_sales_unit:
                line.delivered_amount = round(quantity * line.size_per_sales_unit / factor, 6)
            else:
                line.delivered_amount = quantity
            line.overshoot = round(line.delivered_amount - line.need_value, 6)
            totals = line_totals(price, quantity, answer.price.tax_rate_percent)
            line.line_net, line.line_vat, line.line_gross = totals["line_net"], totals["line_vat"], totals["line_gross"]
            notes.append(f"{line.name}: {quantity:g} {line.sales_unit}, EUR {line.line_net:,.2f} net.")

    _refresh(basket)
    if basket.policy:
        _step(steps, "policy_preview", f"{basket.policy.level}: {basket.policy.reasons or 'within the chef limit'}")
    return " ".join(notes) or "Nothing changed.", steps
