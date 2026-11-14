"""From a parsed need to a priced basket (docs/ARCHITECTURE.md section 8).

No model in this file. The model's part ends at kitchen/need.py; from here on
every quantity comes from shared/units.py and every euro from shared/money.py.

    search_products -> convert units -> check_delivery -> drop what cannot be
    ordered -> rank -> basket line with two alternatives -> policy preview

A product is orderable only if it has a price, is not sales-blocked, its cutoff
has not passed, something can be delivered by the date, and its size could be
worked out in the unit the chef asked in.
"""
from __future__ import annotations

import math

import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from kitchen.gateway_client import GatewayClient
from kitchen.need import ParsedNeed
from shared.money import basket_totals, line_totals
from kitchen import narrate
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
FILLER_WORDS = {"the", "and", "with", "lactose", "free", "laktoositon",
                "organic", "luomu", "fresh", "frozen", "uhf", "uht", "esl"}

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
    """Keep the terse facts, and add the sentence (kitchen/narrate.py)."""
    steps.append(AgentStep(step=step, detail=narrate.sentence(step, detail), facts=detail,
                           for_chef=narrate.is_for_chef(step),
                           crossed_gateway=crossed, at=datetime.now(timezone.utc)))


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


def find_candidates(need: ParsedNeed, gateway: GatewayClient, steps: list[AgentStep],
                    extra_ids: list[str] | None = None) -> tuple[list[Candidate], dict[str, int]]:
    """Every product the search returned, each marked orderable or with the reason it is not.

    `extra_ids` are looked up by id as well as searched for, so a reorder always
    considers the product the kitchen actually uses even if the text search for
    similar products would have missed it.
    """
    kind = _kind(need.unit)
    found = gateway.search_products(SearchRequest(
        text=need.product_text, claims=need.claims, brand=need.brand,
        pack_size=need.pack_size, pack_size_unit=need.pack_size_unit or "", limit=SHORTLIST,
    ))
    _step(steps, "search_products", f"'{need.product_text}' {need.claims or ''}: "
          f"{len(found.products)} of {found.total_matched} candidates", crossed=True)

    products = list(found.products)
    missing = [pid for pid in (extra_ids or [])
               if pid not in {p.product_id for p in products}]
    if missing:
        # A reorder must consider the product the kitchen actually uses, even
        # when a search for similar products would not have turned it up.
        byid = gateway.search_products(SearchRequest(product_ids=missing, limit=len(missing)))
        products = byid.products + products
        _step(steps, "search_products", f"by id: {', '.join(missing)}", crossed=True)

    candidates = [Candidate(s, convert_summary(s, need.quantity, need.unit)) for s in products]
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


def build_line(c: Candidate, need: ParsedNeed, ranked: list[Candidate],
               quantity_override: float | None = None) -> BasketLine:
    """One basket line for this candidate.

    `quantity_override` is for a split line: the quantity is what this product
    can actually deliver rather than the whole need, so the delivered amount and
    the totals are recomputed from it.
    """
    kind = _kind(need.unit)
    conv, s, price = c.conversion, c.summary, c.price
    quantity = conv.quantity_sales_units if quantity_override is None else quantity_override
    per_unit = conv.size_per_sales_unit
    if quantity_override is None:
        delivered, overshoot = conv.delivered_amount, conv.overshoot
    elif kind and per_unit:
        delivered = round(quantity * per_unit, 6)
        overshoot = round(delivered - need.quantity, 6)
    else:
        delivered, overshoot = quantity, round(quantity - need.quantity, 6)
    totals = line_totals(price.price_per_sales_unit, quantity, price.tax_rate_percent)
    # Valio: a sentinel amount means availability is unknown, not plentiful, so
    # the reason says so rather than promising the full amount.
    when = c.delivery.delivery_date
    if not c.delivery.availability_known:
        covers = f"deliverable {when}, though Aimo does not confirm stock"
    elif c.delivery.can_fulfil == "full":
        covers = f"full amount deliverable {when}"
    else:
        covers = f"only {c.delivery.fulfil_amount:g} {s.sales_unit} deliverable {when}"
    position = "Cheapest" if ranked and ranked[0] is c else "Chosen"
    return BasketLine(
        product_id=s.product_id, name=s.name,
        need_value=need.quantity, need_unit=need.unit.upper(),
        sales_unit=s.sales_unit, base_unit=s.base_unit,
        base_units_per_sales_unit=s.base_units_per_sales_unit,
        size_per_base_unit=conv.size_per_base_unit, size_per_sales_unit=conv.size_per_sales_unit,
        size_unit=conv.size_unit, size_source=conv.size_source,
        quantity_sales_units=quantity,
        delivered_amount=delivered, overshoot=overshoot,
        price_per_sales_unit=price.price_per_sales_unit, price_unit=price.price_unit or s.sales_unit,
        price_per_comparison_unit=price.comparison_price, comparison_unit=price.comparison_unit,
        **totals,
        delivery=c.delivery, origin=s.origin, claims=s.claims, labels=s.labels,
        delivery_reliability=s.delivery_reliability, carbon_kg_per_kg=s.carbon_kg_per_kg,
        reason=(f"{position} match per {_per(kind)} (EUR {_unit_price(c, kind):.2f}) of {len(ranked)} orderable; "
                f"{covers}"
                + ("; no plain match could be ordered, so this is the closest" if c.off_request else "")),
        flags=(list(conv.flags)
               + (["differs_from_request"] if c.off_request else [])
               + ([] if c.delivery.availability_known else ["availability_unknown"])),
        alternatives=_alternatives(c, ranked, kind),
    )


def reprice(state: BasketState, gateway: GatewayClient,
            steps: list[AgentStep] | None = None) -> list[str]:
    """Bring every line back in line with what Aimo says right now.

    Used when something moved under us: stock fell between confirming and
    approving, a cut-off passed, a price changed. Rather than leaving the chef at
    a dead end, each line is brought down to what can actually be delivered and
    the changes are described in plain words.

    Returns one note per change, for the chat. An unchanged basket returns none.
    """
    steps = steps if steps is not None else []
    basket, notes = state.basket, []
    for line in list(basket.lines):
        answer = gateway.check_delivery(basket.requested_date, [CheckDeliveryItem(
            product_id=line.product_id, quantity_sales_units=line.quantity_sales_units)]).results[0]
        _step(steps, "check_delivery", f"{line.product_id} re-checked", crossed=True)
        delivery, price = answer.delivery, answer.price

        if delivery.sales_block:
            basket.lines.remove(line)
            notes.append(f"{line.name} is now blocked for sale, so it is out of the basket.")
            continue
        if delivery.past_closing_time:
            basket.lines.remove(line)
            notes.append(f"The order cut-off for {line.name} has passed, so it cannot arrive "
                          f"by {basket.requested_date} and is out of the basket.")
            continue
        if delivery.can_fulfil == "none":
            basket.lines.remove(line)
            notes.append(f"{line.name} can no longer be delivered by {basket.requested_date}, "
                          f"so it is out of the basket.")
            continue

        line.delivery = delivery
        if price.unavailable_reason is None and price.price_per_sales_unit is not None:
            if abs(price.price_per_sales_unit - (line.price_per_sales_unit or 0)) > 0.0049:
                was = line.price_per_sales_unit
                line.price_per_sales_unit = price.price_per_sales_unit
                line.price_per_comparison_unit = price.comparison_price
                notes.append(f"{line.name} is now EUR {price.price_per_sales_unit:.2f} per "
                             f"{line.sales_unit}, was EUR {was:.2f}.")

        deliverable = math.floor(delivery.fulfil_amount)
        if delivery.can_fulfil == "partial" and deliverable < line.quantity_sales_units:
            was = line.quantity_sales_units
            if deliverable <= 0:
                basket.lines.remove(line)
                notes.append(f"None of {line.name} is left for {basket.requested_date}, "
                              f"so it is out of the basket.")
                continue
            line.quantity_sales_units = float(deliverable)
            line.flags = [f for f in line.flags if f != "partial_split"] + ["partial_split"]
            notes.append(f"Only {deliverable:g} {line.sales_unit} of {line.name} is left, "
                          f"not {was:g}, so the line is reduced.")

        _retotal(line)

    _refresh(basket)        # totals, policy preview, and back to an unsigned draft
    if notes:
        _step(steps, "reprice", "; ".join(notes)[:200])
    return notes


def _retotal(line: BasketLine) -> None:
    """Recompute one line's delivered amount and money from its quantity."""
    factor = NEED_TO_LITRES.get(line.need_unit) or NEED_TO_KILOS.get(line.need_unit)
    if factor and line.size_per_sales_unit:
        line.delivered_amount = round(line.quantity_sales_units * line.size_per_sales_unit / factor, 6)
    else:
        line.delivered_amount = line.quantity_sales_units
    line.overshoot = round(line.delivered_amount - line.need_value, 6)
    totals = line_totals(line.price_per_sales_unit or 0.0, line.quantity_sales_units,
                         None if line.line_net == 0 else _tax_rate(line))
    line.line_net, line.line_vat, line.line_gross = (
        totals["line_net"], totals["line_vat"], totals["line_gross"])


def _tax_rate(line: BasketLine) -> float | None:
    """The VAT rate this line was priced at, recovered from its own figures.

    The basket line does not carry the rate, so it is taken back out of the VAT
    and net already on it rather than assumed to be 13.5%.
    """
    if not line.line_net:
        return None
    return round(line.line_vat / line.line_net * 100, 4)


def suggest_cover(state: BasketState, gateway: GatewayClient,
                  steps: list[AgentStep] | None = None) -> list[str]:
    """Offer products that could cover what the basket is now short of.

    Called after a line has been cut back or dropped. The agent proposes rather
    than substitutes: a replacement is attached to the line as an alternative,
    with the quantity that would close the gap and what it would cost, and the
    chef decides. Silently swapping in a different product is exactly what the
    guardrails are there to prevent.

    Returns one note per suggestion, for the chat.
    """
    steps = steps if steps is not None else []
    notes: list[str] = []
    in_basket = {l.product_id for l in state.basket.lines}

    # Group the lines by the need they came from, so a split need is one gap.
    by_need: dict[int, list[BasketLine]] = {}
    for line in state.basket.lines:
        need = state.needs.get(line.product_id)
        if need is not None:
            by_need.setdefault(id(need), []).append(line)

    for need in {id(n): n for n in state.needs.values()}.values():
        lines = by_need.get(id(need), [])
        covered = sum(l.delivered_amount for l in lines)
        shortfall = round(need.quantity - covered, 6)
        if shortfall <= 1e-9:
            continue

        ranked, _ = find_candidates(need, gateway, steps)
        state.candidates.update({c.summary.product_id: c for c in ranked})
        kind = _kind(need.unit)
        options = [c for c in ranked if c.summary.product_id not in in_basket
                   and c.delivery and c.delivery.can_fulfil != "none"]
        if not options:
            notes.append(f"Nothing else in the catalogue can cover the remaining "
                         f"{shortfall:g} {need.unit} for {state.basket.requested_date}.")
            continue

        for candidate in options[:2]:
            per_unit = candidate.conversion.size_per_sales_unit or 1.0
            wanted = math.ceil(shortfall / per_unit) if kind else shortfall
            deliverable = (wanted if candidate.delivery.can_fulfil == "full"
                           else math.floor(candidate.delivery.fulfil_amount))
            units = min(wanted, deliverable)
            if units <= 0:
                continue
            option = _cover_option(candidate, need, units, shortfall, kind)
            target = lines[-1] if lines else None
            if target is not None:
                target.alternatives = (
                    [a for a in target.alternatives if a.product_id != option.product_id]
                    + [option])
            notes.append(f"{option.name} could cover "
                         f"{'all' if units >= wanted else f'{units * per_unit:g} {need.unit}'} "
                         f"of the remaining {shortfall:g} {need.unit}: "
                         f"{units:g} {candidate.summary.sales_unit}, "
                         f"EUR {option.line_net:,.2f} net.")
    if notes:
        _step(steps, "suggest_cover", "; ".join(notes)[:200])
    return notes


def _cover_option(c: Candidate, need: ParsedNeed, units: float, shortfall: float,
                  kind: str) -> Alternative:
    """One suggestion: this product, this many, to close the gap."""
    totals = line_totals(c.price.price_per_sales_unit, units, c.price.tax_rate_percent)
    per_unit = c.conversion.size_per_sales_unit or 1.0
    covers = round(units * per_unit, 6) if kind else units
    enough = covers >= shortfall - 1e-9
    return Alternative(
        product_id=c.summary.product_id, name=c.summary.name,
        quantity_sales_units=units, sales_unit=c.summary.sales_unit,
        line_net=totals["line_net"],
        price_per_comparison_unit=c.price.comparison_price,
        comparison_unit=c.price.comparison_unit,
        delivery=c.delivery,
        reason=(f"Covers {'the whole' if enough else 'part'} of the remaining "
                f"{shortfall:g} {need.unit} ({covers:g} {need.unit}) at "
                f"EUR {_unit_price(c, kind):.2f} per {_per(kind)}"),
        flags=list(c.conversion.flags) + ["covers_shortfall"],
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
    # Unknown availability is deliberately NOT an approval reason. Valio's own
    # logic allows ordering any quantity against a sentinel, and 10 of the 14
    # orderable milks carry one, so requiring a manager would send almost every
    # basket to approval. It stays on the line as the flag availability_unknown.
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


def cover_need(ranked: list[Candidate], need: ParsedNeed, steps: list[AgentStep]) -> list[BasketLine]:
    """Lines that cover the need, splitting across products when one cannot.

    Aimo answers full, partial or none. A partial answer used to be ordered at
    the full quantity anyway, which would have sent 1,000 litres to a product
    with 51 units of stock. Instead the line is reduced to what can actually be
    delivered and the remainder goes to the next ranked product, which is the
    "split across products" the exception table asks for.

    A quote refuses a partial line outright (the gateway will not sign for more
    than it can deliver), so reducing here is also what makes the line quotable.
    """
    kind = _kind(need.unit)
    winner = ranked[0]
    if _can_cover(winner):
        return [build_line(winner, need, ranked)]

    lines: list[BasketLine] = []
    remaining = need.quantity
    for candidate in ranked:
        if remaining <= 1e-9:
            break
        if candidate.delivery is None or candidate.delivery.can_fulfil == "none":
            continue
        # How much of the remaining need this product can actually deliver.
        per_unit = candidate.conversion.size_per_sales_unit or 1.0
        wanted_units = candidate.conversion.quantity_sales_units
        deliverable_units = (wanted_units if _can_cover(candidate)
                             else math.floor(candidate.delivery.fulfil_amount))
        if deliverable_units <= 0:
            continue
        units = min(wanted_units, deliverable_units,
                    math.ceil(remaining / per_unit) if kind else remaining)
        if units <= 0:
            continue
        line = build_line(candidate, need, ranked, quantity_override=units)
        line.flags.append("partial_split")
        lines.append(line)
        remaining = round(remaining - (units * per_unit if kind else units), 6)

    if not lines:
        return [build_line(winner, need, ranked)]
    if remaining > 1e-9:
        # Say so on the line rather than quietly delivering less than asked for.
        lines[-1].flags.append("short_of_need")
        lines[-1].reason += (f"; together these cover "
                             f"{need.quantity - remaining:g} of {need.quantity:g} {need.unit}, "
                             f"{remaining:g} short")
    _step(steps, "split_need",
          f"{need.quantity:g} {need.unit} split across {len(lines)} product(s)"
          + (f", {remaining:g} {need.unit} short" if remaining > 1e-9 else ""))
    return lines


def _can_cover(candidate: Candidate) -> bool:
    """True when this product alone can deliver everything that was asked for."""
    delivery = candidate.delivery
    if delivery is None:
        return False
    return (delivery.can_fulfil == "full"
            and delivery.fulfil_amount >= candidate.conversion.quantity_sales_units - 1e-9)


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

    lines = cover_need(ranked, need, steps)
    covered = {l.product_id for l in lines}
    state.basket.lines = [l for l in state.basket.lines if l.product_id not in covered] + lines
    state.basket.requested_date = need.requested_date
    for line in lines:
        state.needs[line.product_id] = need
    state.candidates.update({c.summary.product_id: c for c in ranked})
    _refresh(state.basket)
    _step(steps, "policy_preview", f"{state.basket.policy.level}: {state.basket.policy.reasons or 'within the chef limit'}")

    approval = ("A manager will need to approve it." if state.basket.policy.level == "manager"
                else "You can approve it yourself.")
    net = sum(l.line_net for l in lines)
    if len(lines) == 1:
        line = lines[0]
        reply = (f"Drafted {line.quantity_sales_units:g} {line.sales_unit} of {line.name}: "
                 f"EUR {line.line_net:,.2f} net, delivery {line.delivery.delivery_date}. {approval}")
    else:
        parts = "; ".join(f"{l.quantity_sales_units:g} {l.sales_unit} of {l.name}" for l in lines)
        reply = (f"No single product could cover {need.quantity:g} {need.unit}, so it is split "
                 f"across {len(lines)}: {parts}. EUR {net:,.2f} net in total. {approval}")
    shortfall = lines[-1].flags.count("short_of_need") if lines else 0
    if shortfall:
        reply += (f" Even together they fall short of {need.quantity:g} {need.unit}; "
                  f"the shortfall is on the last line.")
    return state, reply, steps


def add_reorder(state: BasketState | None, product_id: str, quantity: float, unit: str,
                requested_date: date, gateway: GatewayClient, reason: str = "",
                source: str = "suggestion:low_stock") -> tuple[BasketState, str, list[AgentStep]]:
    """More of a product the kitchen already uses, for the running-low trigger.

    A reorder is not a search: the kitchen knows which cream it wants, and
    picking a cheaper one because it ranked higher would change the dish. So the
    named product wins if it can be ordered at all, and only if it cannot does
    the agent fall back to the best alternative and say so on the line.

    Similar products are still looked up, so the chef has alternatives to swap
    to without another round trip.
    """
    steps: list[AgentStep] = []
    _step(steps, "reorder", f"{quantity:g} {unit} of {product_id} by {requested_date}"
          + (f" ({reason})" if reason else ""))
    state = state or BasketState(Basket(basket_id=f"b_{uuid.uuid4().hex[:10]}",
                                        requested_date=requested_date, source=source))
    state.basket.source = source

    named = gateway.search_products(SearchRequest(product_ids=[product_id], limit=1))
    _step(steps, "search_products", f"id {product_id}", crossed=True)
    if not named.products:
        _refresh(state.basket)
        return state, f"{product_id} is not in Aimo's catalogue any more.", steps
    summary = named.products[0]

    # The product's own name becomes the search for alternatives, with the claim
    # words stripped the same way a chat request would be.
    need = ParsedNeed(product_text=_reorder_text(summary.name), quantity=quantity,
                      unit=unit.upper(), claims=list(summary.claims),
                      requested_date=requested_date, parser="reorder")
    ranked, reasons = find_candidates(need, gateway, steps, extra_ids=[product_id])
    wanted = next((c for c in ranked if c.summary.product_id == product_id), None)

    if wanted is None:
        if not ranked:
            _refresh(state.basket)
            return state, _explain_empty(need, reasons), steps
        # The usual product cannot be ordered, so the closest is offered and
        # flagged: a substitution the chef did not ask for needs a human.
        lines = [build_line(ranked[0], need, ranked)]
        lines[0].flags.append("differs_from_request")
        lines[0].reason = (f"{summary.name} cannot be ordered for {requested_date}, "
                           f"so this is the closest that can; " + lines[0].reason)
    else:
        lines = cover_need([wanted] + [c for c in ranked if c is not wanted], need, steps)

    covered = {l.product_id for l in lines}
    state.basket.lines = [l for l in state.basket.lines if l.product_id not in covered] + lines
    for line in lines:
        state.needs[line.product_id] = need
    state.candidates.update({c.summary.product_id: c for c in ranked})
    _refresh(state.basket)
    _step(steps, "policy_preview", f"{state.basket.policy.level}: {state.basket.policy.reasons or 'within the chef limit'}")

    first = lines[0]
    lead = f"{reason} " if reason else ""
    approval = ("A manager will need to approve it." if state.basket.policy.level == "manager"
                else "You can approve it yourself.")
    return state, (f"{lead}Drafted {first.quantity_sales_units:g} {first.sales_unit} of "
                   f"{first.name}: EUR {first.line_net:,.2f} net, delivery "
                   f"{first.delivery.delivery_date}. {approval}"), steps


def _reorder_text(name: str) -> str:
    """A few words from a product name, to find products like it."""
    words = [w for w in re.split(r"[\s,/()]+", name.lower())
             if w and not w.isdigit() and w not in FILLER_WORDS and len(w) > 2]
    return " ".join(words[:3])


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
            if not target and need:
                # The candidates are not persisted, so after a restart the only
                # thing we hold is the need. Search again rather than refuse.
                ranked, _ = find_candidates(need, gateway, steps)
                state.candidates.update({c.summary.product_id: c for c in ranked})
                target = state.candidates.get(edit.swap_to_product_id)
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
