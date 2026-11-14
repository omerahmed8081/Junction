"""Quotes: Aimo's signed statement of price and delivery for named lines.

Called a **signed price confirmation** in the UI and the pitch, because the brief
asks for ordering "based on fixed prices rather than a negotiated quote".

What a quote fixes, and what it does not:

  * **the price** is fixed until `exp`
  * **delivery is a snapshot.** The mock cannot reserve stock, so the quote says
    what Aimo could deliver when it was checked. `place_order` checks again and
    refuses with `stock_changed` if it moved.

`exp` is the earliest of the price validity, the order cut-off and two hours from
now, so a quote can never outlive the cut-off it depends on.

A quote is **single use**: the registry below remembers which have been ordered,
so replaying one with a fresh idempotency key is refused.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from shared.money import basket_totals, line_totals
from shared.schemas import (
    CheckDeliveryResult,
    CreateQuoteResponse,
    DeliveryAnswer,
    ProductSummary,
    QuoteLine,
    QuotePayload,
)
from shared.signing import Signer, now_seconds

MAX_QUOTE_TTL = timedelta(hours=2)


def new_quote_id() -> str:
    return f"q_{secrets.token_hex(5)}"


def new_order_id() -> str:
    return f"o_{secrets.token_hex(5)}"


# ------------------------------------------------------------- single use


@dataclass
class QuoteRegistry:
    """Which quotes have been ordered, and which orders answered which key.

    In memory for now, which is enough for the demo and matches the kitchen's
    baskets; the shape is deliberately a dict lookup so it can become a table
    with a unique index on the quote id.
    """

    used: dict[str, str] = field(default_factory=dict)          # quote jti -> order id
    by_idempotency: dict[str, str] = field(default_factory=dict)  # key -> order id

    def order_for_quote(self, quote_id: str) -> str | None:
        return self.used.get(quote_id)

    def order_for_key(self, idempotency_key: str) -> str | None:
        return self.by_idempotency.get(idempotency_key)

    def mark_used(self, quote_id: str, idempotency_key: str, order_id: str) -> None:
        self.used[quote_id] = order_id
        self.by_idempotency[idempotency_key] = order_id

    def forget_customer(self, order_ids: Iterable[str]) -> None:
        """For the labelled demo reset only."""
        gone = set(order_ids)
        self.used = {q: o for q, o in self.used.items() if o not in gone}
        self.by_idempotency = {k: o for k, o in self.by_idempotency.items() if o not in gone}


# ------------------------------------------------------------ building one


def _expiry(lines: list[QuoteLine], issued_at: datetime) -> datetime:
    """The earliest of: a price validity, a cut-off, and two hours from now."""
    candidates = [issued_at + MAX_QUOTE_TTL]
    for line in lines:
        if line.delivery and line.delivery.closing_time:
            candidates.append(line.delivery.closing_time)
    return min(candidates)


def quote_line(
    summary: ProductSummary,
    result: CheckDeliveryResult,
    quantity_sales_units: float,
) -> QuoteLine:
    """One line, with every figure computed here rather than taken on trust."""
    price = result.price
    totals = line_totals(price.price_per_sales_unit or 0.0, quantity_sales_units,
                         price.tax_rate_percent)
    size = summary.size_per_sales_unit
    return QuoteLine(
        product_id=summary.product_id,
        name=summary.name,
        gtin=summary.gtin,
        quantity_sales_units=quantity_sales_units,
        sales_unit=summary.sales_unit,
        delivered_amount=round(quantity_sales_units * size, 6) if size else quantity_sales_units,
        delivered_unit=summary.size_unit or summary.sales_unit,
        size_source=summary.size_source,
        price_per_sales_unit=price.price_per_sales_unit or 0.0,
        price_per_comparison_unit=price.comparison_price,
        comparison_unit=price.comparison_unit,
        line_net=totals["line_net"],
        line_vat=totals["line_vat"],
        line_gross=totals["line_gross"],
        tax_rate_percent=price.tax_rate_percent,
        delivery=result.delivery,
        origin=summary.origin,
        claims=list(summary.claims),
        labels=list(summary.labels),
        category=summary.category,
    )


def refusal_for(result: CheckDeliveryResult, wanted: float) -> dict[str, str] | None:
    """Why this line cannot be quoted at the quantity asked for, if it cannot.

    A quote states something Aimo will stand behind, so a line that cannot be
    delivered, is blocked, is past its cut-off or has no price is refused rather
    than quoted with a caveat.
    """
    price, delivery = result.price, result.delivery
    if price.unavailable_reason:
        return {"product_id": result.product_id, "reason": price.unavailable_reason}
    if delivery.sales_block:
        return {"product_id": result.product_id, "reason": "sales_block"}
    if delivery.past_closing_time:
        return {"product_id": result.product_id, "reason": "past_closing_time"}
    if delivery.can_fulfil == "none":
        return {"product_id": result.product_id, "reason": "not_deliverable"}
    if delivery.can_fulfil == "partial" and delivery.fulfil_amount < wanted:
        # Partial is a real answer, but not one to sign for the full quantity:
        # the kitchen reduces or splits the line and asks again.
        return {"product_id": result.product_id, "reason": "partial_only",
                "fulfil_amount": f"{delivery.fulfil_amount:g}"}
    return None


def build_quote(
    *,
    issuer: str,
    customer_id: str,
    agent_id: str,
    requested_date: date,
    summaries: dict[str, ProductSummary],
    results: dict[str, CheckDeliveryResult],
    wanted: dict[str, float],
    signer: Signer,
    now: datetime | None = None,
    quote_id: str | None = None,
) -> CreateQuoteResponse:
    """Sign one quote for the lines that can be stood behind."""
    issued_at = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    lines: list[QuoteLine] = []
    refused: list[dict[str, str]] = []

    for product_id, quantity in wanted.items():
        summary, result = summaries.get(product_id), results.get(product_id)
        if summary is None or result is None:
            refused.append({"product_id": product_id, "reason": "not_found"})
            continue
        problem = refusal_for(result, quantity)
        if problem:
            refused.append(problem)
            continue
        lines.append(quote_line(summary, result, quantity))

    if not lines:
        raise ValueError("no line could be quoted")

    totals = basket_totals([{"line_net": l.line_net, "line_vat": l.line_vat} for l in lines])
    expires_at = _expiry(lines, issued_at)
    payload = QuotePayload(
        iss=issuer,
        sub=f"customer:{customer_id}",
        aud=agent_id,
        jti=quote_id or new_quote_id(),
        iat=now_seconds(issued_at),
        exp=now_seconds(expires_at),
        requested_date=requested_date,
        lines=lines,
        net_total=totals["net_total"],
        vat_total=totals["vat_total"],
        gross_total=totals["gross_total"],
        price_checked_at=issued_at,
        delivery_checked_at=issued_at,
    )
    token = signer.sign(payload.model_dump(mode="json"))
    return CreateQuoteResponse(
        quote_id=payload.jti,
        quote_jws=token,
        expires_at=expires_at,
        net_total=payload.net_total,
        vat_total=payload.vat_total,
        gross_total=payload.gross_total,
        lines=lines,
        refused=refused,
    )
