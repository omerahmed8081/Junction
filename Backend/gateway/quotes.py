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

    Kept in `gateway.used_quotes` and mirrored in memory. This is what stops a
    quote being ordered twice, so a restart emptying it was not a lost
    convenience: a quote that had already been paid for became orderable again.

    Without a pool it is memory alone, which is how every test runs.
    """

    used: dict[str, str] = field(default_factory=dict)          # quote jti -> order id
    by_idempotency: dict[str, str] = field(default_factory=dict)  # key -> order id
    pool: Any = None

    def set_pool(self, pool: Any) -> None:
        self.pool = pool
        self.reload()

    def reload(self) -> int:
        if self.pool is None:
            return 0
        try:
            with self.pool.connection(timeout=5.0) as conn:
                rows = conn.execute(
                    "SELECT quote_jti, order_id, idempotency_key FROM gateway.used_quotes"
                ).fetchall()
        except Exception as exc:        # noqa: BLE001
            print(f"[gateway] could not read used quotes back: {type(exc).__name__}: {exc}")
            return 0
        for quote_jti, order_id, key in rows:
            self.used[quote_jti] = order_id
            if key:
                self.by_idempotency[key] = order_id
        return len(rows)

    def order_for_quote(self, quote_id: str) -> str | None:
        return self.used.get(quote_id)

    def order_for_key(self, idempotency_key: str) -> str | None:
        return self.by_idempotency.get(idempotency_key)

    def mark_used(self, quote_id: str, idempotency_key: str, order_id: str,
                  customer_id: str = "") -> None:
        self.used[quote_id] = order_id
        self.by_idempotency[idempotency_key] = order_id
        if self.pool is None:
            return
        try:
            with self.pool.connection(timeout=5.0) as conn:
                conn.execute(
                    "INSERT INTO gateway.used_quotes"
                    " (quote_jti, order_id, idempotency_key, customer_id)"
                    " VALUES (%s, %s, %s, %s) ON CONFLICT (quote_jti) DO NOTHING",
                    (quote_id, order_id, idempotency_key or "", customer_id or ""))
        except Exception as exc:        # noqa: BLE001
            print(f"[gateway] quote {quote_id} not marked used in the database: "
                  f"{type(exc).__name__}: {exc}")

    def forget_customer(self, order_ids: Iterable[str]) -> None:
        """For the labelled demo reset only."""
        gone = set(order_ids)
        self.used = {q: o for q, o in self.used.items() if o not in gone}
        self.by_idempotency = {k: o for k, o in self.by_idempotency.items() if o not in gone}
        if self.pool is not None and gone:
            try:
                with self.pool.connection(timeout=5.0) as conn:
                    conn.execute("DELETE FROM gateway.used_quotes WHERE order_id = ANY(%s)",
                                 (list(gone),))
            except Exception as exc:    # noqa: BLE001
                print(f"[gateway] demo reset did not clear used quotes: {exc}")


# ------------------------------------------------------------ building one


def _expiry(lines: list[QuoteLine], issued_at: datetime) -> datetime:
    """The earliest of: a price validity, a cut-off, and two hours from now."""
    candidates = [issued_at + MAX_QUOTE_TTL]
    for line in lines:
        closing = line.delivery.closing_time if line.delivery else None
        # A cut-off that had already passed when the quote was issued cannot
        # bound it: taking it would mint a quote that is expired at birth, and
        # the kitchen would refuse the very confirmation it just asked for.
        # Reachable whenever a cut-off is judged at a stated earlier instant
        # (gateway/delivery.py, cutoff_now), and nonsense in any case.
        if closing and closing > issued_at:
            candidates.append(closing)
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
