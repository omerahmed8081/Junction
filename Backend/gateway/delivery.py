"""Price and delivery answers for a shortlist (NIGHT_SHIFT brief 1, step 4).

Pure: it turns Aimo's raw price and availability payloads into the contract's
CheckDeliveryResponse and does no I/O, so it is tested on the recorded hour 0
responses without the network.

THE RULE: the warehouse count never leaves the gateway. `can_fulfil` is full,
partial or none, and `fulfil_amount` is capped at what was asked for. All of that
is decided in aimo_client.answer_delivery(); nothing here reads availableAmount.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from gateway.aimo_client import answer_delivery, read_price
from shared.timeparse import parse_api_datetime
from shared.schemas import (
    CheckDeliveryItem,
    CheckDeliveryResponse,
    CheckDeliveryResult,
    DeliveryAnswer,
    PriceAnswer,
)

# The shortlist rule: this endpoint is for candidates, not for the catalogue.
MAX_ITEMS = 100


def _when(value: Any) -> datetime | None:
    """Aimo's timestamps, via the shared parser.

    It was fromisoformat() here, which silently returned None on Python 3.10
    because the API's offsets have no colon: a price validity date would have
    disappeared rather than raised.
    """
    return parse_api_datetime(value)


def _price(raw: dict[str, Any] | None, sales_unit: str) -> PriceAnswer:
    p = read_price(raw)
    if p.get("unavailable_reason"):
        return PriceAnswer(price_unit=sales_unit, unavailable_reason=p["unavailable_reason"])
    return PriceAnswer(
        price_per_sales_unit=p["price_per_sales_unit"],
        price_unit=sales_unit,
        price_with_tax_per_sales_unit=p.get("price_with_tax_per_sales_unit"),
        comparison_price=p.get("comparison_price"),
        comparison_unit=p.get("comparison_unit") or "",
        tax_rate_percent=p.get("tax_rate"),
        valid_to=_when(p.get("valid_to")),
        customer_specific=bool(p.get("customer_specific")),
        unavailable_reason=None,
    )


def _delivery(raw: dict[str, Any] | None, wanted: float, requested_date: date,
              sales_unit: str, now: datetime) -> DeliveryAnswer:
    a = answer_delivery(raw, wanted, requested_date, now=now)
    return DeliveryAnswer(
        can_fulfil=a["can_fulfil"],
        fulfil_amount=a.get("fulfil_amount", 0.0),
        sales_unit=a.get("sales_unit") or sales_unit,
        delivery_date=a.get("delivery_date"),
        closing_time=a.get("closing_time"),
        past_closing_time=bool(a.get("past_closing_time")),
        sales_block=bool(a.get("sales_block")),
        availability_known=bool(a.get("availability_known", True)),
    )


def build_response(
    items: list[CheckDeliveryItem],
    requested_date: date,
    raw_prices: dict[str, dict[str, Any]],
    raw_availability: dict[str, dict[str, Any]],
    sales_units: dict[str, str],
    now: datetime | None = None,
) -> CheckDeliveryResponse:
    """One result per item, in request order.

    `sales_units` maps every product id that exists in our catalogue to its sales
    unit. An id that is not in it gets `unknown_product` and is never sent to Aimo.
    """
    now = now or datetime.now(timezone.utc)
    results = []
    for item in items:
        pid = item.product_id
        if pid not in sales_units:
            results.append(CheckDeliveryResult(
                product_id=pid,
                price=PriceAnswer(unavailable_reason="unknown_product"),
                delivery=DeliveryAnswer(can_fulfil="none"),
            ))
            continue
        unit = sales_units[pid]
        results.append(CheckDeliveryResult(
            product_id=pid,
            price=_price(raw_prices.get(pid), unit),
            delivery=_delivery(raw_availability.get(pid), item.quantity_sales_units, requested_date, unit, now),
        ))
    return CheckDeliveryResponse(requested_date=requested_date, checked_at=now, results=results)
