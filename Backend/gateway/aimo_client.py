"""Client for Aimo's mock price and availability API.

CONTRACT - agreed hour 0. Only this module talks to junction26.valioaimo.fi, and
only the gateway imports it. The agent never sees a raw warehouse count.

Hour 0 findings baked in (docs/HOUR0_FINDINGS.md):
  * Cloudflare rejects script-looking user agents with 403 "error code: 1010",
    so BROWSER_UA is not optional.
  * availableAmount uses several "unlimited" sentinels (999999, 9999999999,
    666666666), so anything at or above UNLIMITED_FROM is "unlimited", not a count.
  * netPriceFound false only means "no customer-specific price"; catalogPrice is
    still there and still usable.
  * comparisonPrice is already per litre or kilogram: rank on it, and use
    catalogPrice / comparisonPrice as a size cross-check.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any, Iterable

import httpx

BASE_URL = os.environ.get("AIMO_BASE_URL", "https://junction26.valioaimo.fi")
API_KEY = os.environ.get("JUNCTION_API_KEY", "")
CUSTOMER_ID = os.environ.get("AIMO_CUSTOMER_ID", "123")

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
UNLIMITED_FROM = 100_000.0
MAX_PRODUCTS_PER_CALL = 1000      # the API's own limit


class AimoError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    if not API_KEY:
        raise AimoError("JUNCTION_API_KEY is not set (see Backend/.env.example)")
    return {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": BROWSER_UA,
    }


class AimoClient:
    """Thin, cached wrapper. One instance per process."""

    def __init__(self, timeout: float = 20.0) -> None:
        self._client = httpx.Client(base_url=BASE_URL, headers=_headers(), timeout=timeout)
        self._cache: dict[tuple[str, str, str], dict[str, Any]] = {}

    def close(self) -> None:
        self._client.close()

    def ping(self) -> bool:
        return self._client.get("/api/ping").status_code == 200

    def _post(self, path: str, product_ids: list[str], requested_date: date) -> dict[str, Any]:
        key = (path, requested_date.isoformat(), ",".join(sorted(product_ids)))
        if key in self._cache:
            return self._cache[key]
        body = {
            "customer": CUSTOMER_ID,
            "requestedDate": requested_date.isoformat(),
            "products": product_ids,
        }
        response = self._client.post(path, json=body)
        if response.status_code != 200:
            raise AimoError(f"{path} returned {response.status_code}: {response.text[:200]}")
        payload = response.json()
        self._cache[key] = payload
        return payload

    def prices(self, product_ids: Iterable[str], requested_date: date) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(product_ids))[:MAX_PRODUCTS_PER_CALL]
        payload = self._post("/api/price", ids, requested_date)
        return {p["productId"]: p for p in payload.get("products", [])}

    def availability(self, product_ids: Iterable[str], requested_date: date) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(product_ids))[:MAX_PRODUCTS_PER_CALL]
        payload = self._post("/api/availability", ids, requested_date)
        return {p["productId"]: p for p in payload.get("products", [])}


# --------------------------------------------------------------- interpretation
# Pure functions, so they can be tested without the network.


def is_unlimited(amount: float | None) -> bool:
    return amount is not None and amount >= UNLIMITED_FROM


def read_price(raw: dict[str, Any] | None) -> dict[str, Any]:
    """The price fields we use, or a reason there is no usable price."""
    if not raw:
        return {"unavailable_reason": "no_price_returned"}
    if raw.get("error"):
        return {"unavailable_reason": "price_error"}
    catalog = raw.get("catalogPrice") or {}
    if catalog.get("value") is None:
        return {"unavailable_reason": "no_catalog_price"}
    comparison = raw.get("comparisonPrice") or {}
    with_tax = raw.get("priceWithTax") or {}
    tax = raw.get("tax") or {}
    return {
        "price_per_sales_unit": catalog["value"],
        "price_with_tax_per_sales_unit": with_tax.get("value") or catalog.get("valueWithTax"),
        "comparison_price": comparison.get("value"),
        "comparison_unit": raw.get("comparisonUnit") or "",
        "tax_rate": tax.get("taxRate"),
        "valid_to": catalog.get("validTo"),
        # netPriceFound false is not an error: it means the catalogue price applies.
        "customer_specific": bool(raw.get("netPriceFound")),
        "unavailable_reason": None,
    }


def derived_size_per_sales_unit(raw: dict[str, Any] | None) -> float | None:
    """catalogPrice / comparisonPrice = size of one sales unit, in comparison units.

    Rounded to cents upstream, so it is a cross-check and a last-resort fallback.
    """
    price = read_price(raw)
    catalog, comparison = price.get("price_per_sales_unit"), price.get("comparison_price")
    if not catalog or not comparison:
        return None
    return catalog / comparison


def answer_delivery(
    raw: dict[str, Any] | None,
    wanted_sales_units: float,
    requested_date: date,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Turn the raw availability into full / partial / none.

    The warehouse count never leaves this function: fulfil_amount is capped at
    what was asked for, which is what the brief's "without exposing confidential
    data" asks of us.
    """
    now = now or datetime.now(timezone.utc)
    if not raw:
        return {"can_fulfil": "none", "fulfil_amount": 0.0, "sales_block": False,
                "past_closing_time": False, "reason": "no_availability_returned"}

    closing_raw = raw.get("closingTime")
    closing = datetime.fromisoformat(closing_raw) if closing_raw else None
    past_closing = bool(closing and closing < now)

    if raw.get("salesBlock"):
        return {"can_fulfil": "none", "fulfil_amount": 0.0, "sales_block": True,
                "closing_time": closing, "past_closing_time": past_closing,
                "reason": "sales_block"}

    lines = [l for l in raw.get("scheduleLines") or [] if not l.get("salesBlock")]
    usable = [l for l in lines if l.get("availableDate") and l["availableDate"] <= requested_date.isoformat()]
    if not usable:
        # Either nothing is deliverable at all, or not by the date asked for.
        earliest = min((l["availableDate"] for l in lines if l.get("availableDate")), default=None)
        return {"can_fulfil": "none", "fulfil_amount": 0.0, "sales_block": False,
                "delivery_date": date.fromisoformat(earliest) if earliest else None,
                "closing_time": closing, "past_closing_time": past_closing,
                "reason": "not_deliverable_by_requested_date" if earliest else "no_schedule_lines"}

    best = min(usable, key=lambda l: l["availableDate"])
    amount = best.get("availableAmount")
    unit = best.get("salesUnit") or ""
    if is_unlimited(amount):
        fulfil = wanted_sales_units
    else:
        fulfil = min(float(amount or 0.0), wanted_sales_units)
    return {
        "can_fulfil": "full" if fulfil >= wanted_sales_units else ("partial" if fulfil > 0 else "none"),
        "fulfil_amount": fulfil,
        "sales_unit": unit,
        "delivery_date": date.fromisoformat(best["availableDate"]),
        "closing_time": closing,
        "past_closing_time": past_closing,
        "sales_block": False,
        "reason": "",
    }
