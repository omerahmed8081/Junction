"""Client for Aimo's mock price and availability API.

CONTRACT - agreed hour 0. Only this module talks to junction26.valioaimo.fi, and
only the gateway imports it. The agent never sees a raw warehouse count.

What the live API actually does, baked in (docs/ARCHITECTURE.md section 18):
  * Cloudflare rejects script-looking user agents with 403 "error code: 1010",
    so BROWSER_UA is not optional.
  * availableAmount >= 999999 does NOT mean unlimited stock. Valio: "we do not
    know the availability and our current logic allows ordering any quantity.
    These values typically appear on terminal products that are not stocked by
    us." So any quantity may be ordered, but nothing is confirmed: the answer
    carries availability_known = False and the agent lowers its confidence.
  * An empty scheduleLines list means not available at all (Valio).
  * 429 means we are blocked for 30 seconds. Valio: "a few hundred calls per
    minute is ok, over 10 seconds not ok", so calls are spaced and 429 is
    waited out rather than raised.
  * netPriceFound false only means "no customer-specific price"; catalogPrice is
    still there and still usable.
  * comparisonPrice is already per litre or kilogram: rank on it, and use
    catalogPrice / comparisonPrice as a size cross-check.
  * Every timestamp uses an offset with no colon ("+0200"), which Python 3.10
    cannot read, so they all go through shared.timeparse.
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timezone
from typing import Any, Iterable

import httpx

from shared.timeparse import parse_api_date, parse_api_datetime

BASE_URL = os.environ.get("AIMO_BASE_URL", "https://junction26.valioaimo.fi")
API_KEY = os.environ.get("JUNCTION_API_KEY", "")
CUSTOMER_ID = os.environ.get("AIMO_CUSTOMER_ID", "123")

# How long an answer from Aimo may be reused. Long enough that building one
# basket does not ask the same question five times, short enough that a price
# or a stock level that moves is seen. It used to be for ever, which meant the
# re-check at order time got the quote's own answer back and could never notice
# that stock had changed.
CACHE_SECONDS = float(os.environ.get("AIMO_CACHE_SECONDS", "60"))


def upstream_customer(customer_id: str | None) -> str:
    """The customer number to price for at Aimo.

    Aimo prices by contract, so the gateway's customer decides whose prices are
    fetched. `AIMO_CUSTOMER_MAP` ("456=300500,789=300612") says which Aimo
    customer each of ours is. One we have no mapping for is priced on the
    default contract rather than sent upstream as a number Aimo has never heard
    of: this prototype holds a single real contract, and an unknown customer
    must still be able to see a price.
    """
    wanted = (customer_id or "").strip()
    for pair in (os.environ.get("AIMO_CUSTOMER_MAP") or "").split(","):
        ours, _, theirs = pair.partition("=")
        if ours.strip() and ours.strip() == wanted and theirs.strip():
            return theirs.strip()
    return CUSTOMER_ID

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# Valio: "Availability ge 999999 means that we do not know the availability".
AVAILABILITY_UNKNOWN_FROM = 999_999.0
MAX_PRODUCTS_PER_CALL = 1000      # the API's own limit

# Valio: a few hundred calls a minute is fine, the same burst inside ten seconds
# is not. One call every 0.25 s is about 240 a minute, comfortably inside that.
MIN_SECONDS_BETWEEN_CALLS = 0.25
RATE_LIMIT_WAIT_SECONDS = 30.0    # Valio: a 429 blocks us for 30 seconds
MAX_RATE_LIMIT_RETRIES = 2


class AimoError(RuntimeError):
    pass


class AimoRateLimited(AimoError):
    """Still rate-limited after waiting out every allowed retry."""


def _retry_after_seconds(response: "httpx.Response") -> float:
    """Honour Retry-After when the API sends one, otherwise wait the stated 30 s."""
    header = response.headers.get("Retry-After")
    if header:
        try:
            return max(1.0, float(header))
        except ValueError:
            pass
    return RATE_LIMIT_WAIT_SECONDS


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

    def __init__(self, timeout: float = 20.0, sleep: Any = time.sleep) -> None:
        self._client = httpx.Client(base_url=BASE_URL, headers=_headers(), timeout=timeout)
        self._cache: dict[tuple[str, str, str, str], tuple[float, dict[str, Any]]] = {}
        self._sleep = sleep               # injectable, so tests do not really wait
        self._last_call_at = 0.0

    def close(self) -> None:
        self._client.close()

    def ping(self) -> bool:
        return self._client.get("/api/ping").status_code == 200

    # A class attribute, so a client built without __init__ (the tests do) has it.
    _clock = staticmethod(time.monotonic)

    def _post(self, path: str, product_ids: list[str], requested_date: date,
              customer: str | None = None, fresh: bool = False) -> dict[str, Any]:
        """One question to Aimo, reused for CACHE_SECONDS.

        `fresh=True` asks again whatever is cached. The order-time re-check uses
        it: the whole point of that check is to see what changed since the quote.
        """
        upstream = upstream_customer(customer)
        key = (path, requested_date.isoformat(), ",".join(sorted(product_ids)), upstream)
        cached = self._cache.get(key)
        if cached is not None and not fresh:
            stored_at, payload = cached
            if self._clock() - stored_at <= CACHE_SECONDS:
                return payload
        body = {
            "customer": upstream,
            "requestedDate": requested_date.isoformat(),
            "products": product_ids,
        }
        payload = self._post_with_backoff(path, body)
        self._cache[key] = (self._clock(), payload)
        return payload

    def _space_calls(self) -> None:
        """Keep a quarter second between calls, so a shortlist never bursts."""
        gap = time.monotonic() - self._last_call_at
        if self._last_call_at and gap < MIN_SECONDS_BETWEEN_CALLS:
            self._sleep(MIN_SECONDS_BETWEEN_CALLS - gap)
        self._last_call_at = time.monotonic()

    def _post_with_backoff(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """One call, waiting out a 429 rather than failing the request.

        A 429 blocks us for 30 seconds, so there is nothing cleverer to do than
        wait; Retry-After is honoured when the API sends one.
        """
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            self._space_calls()
            response = self._client.post(path, json=body)
            if response.status_code == 429:
                if attempt == MAX_RATE_LIMIT_RETRIES:
                    raise AimoRateLimited(
                        f"{path} still rate-limited after {attempt + 1} attempts"
                    )
                self._sleep(_retry_after_seconds(response))
                continue
            if response.status_code != 200:
                raise AimoError(f"{path} returned {response.status_code}: {response.text[:200]}")
            return response.json()
        raise AimoRateLimited(path)      # unreachable, kept for the type checker

    def prices(self, product_ids: Iterable[str], requested_date: date, *,
               customer: str | None = None, fresh: bool = False) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(product_ids))[:MAX_PRODUCTS_PER_CALL]
        payload = self._post("/api/price", ids, requested_date, customer, fresh)
        return {p["productId"]: p for p in payload.get("products", [])}

    def availability(self, product_ids: Iterable[str], requested_date: date, *,
                     customer: str | None = None, fresh: bool = False) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(product_ids))[:MAX_PRODUCTS_PER_CALL]
        payload = self._post("/api/availability", ids, requested_date, customer, fresh)
        return {p["productId"]: p for p in payload.get("products", [])}


# --------------------------------------------------------------- interpretation
# Pure functions, so they can be tested without the network.


def is_availability_unknown(amount: float | None) -> bool:
    """True for Aimo's "we do not know" sentinels: 999999, 666666666, 9999999999.

    Any quantity may be ordered, but nothing about stock is confirmed.
    """
    return amount is not None and amount >= AVAILABILITY_UNKNOWN_FROM


# Kept so older call sites keep working; the name was wrong, not the test.
is_unlimited = is_availability_unknown


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
        # Parsed here, so nothing downstream calls fromisoformat on an API
        # string. Year 9999 means "no end date" and comes back as None.
        "valid_to": parse_api_datetime(catalog.get("validTo")),
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
                "past_closing_time": False, "availability_known": True,
                "reason": "no_availability_returned"}

    closing = parse_api_datetime(raw.get("closingTime"))
    past_closing = bool(closing and closing < now)

    if raw.get("salesBlock"):
        return {"can_fulfil": "none", "fulfil_amount": 0.0, "sales_block": True,
                "closing_time": closing, "past_closing_time": past_closing,
                "availability_known": True, "reason": "sales_block"}

    lines = [l for l in raw.get("scheduleLines") or [] if not l.get("salesBlock")]
    usable = [l for l in lines if l.get("availableDate") and l["availableDate"] <= requested_date.isoformat()]
    if not usable:
        # Either nothing is deliverable at all, or not by the date asked for.
        earliest = min((l["availableDate"] for l in lines if l.get("availableDate")), default=None)
        # Valio: an empty scheduleLines list means not available.
        return {"can_fulfil": "none", "fulfil_amount": 0.0, "sales_block": False,
                "delivery_date": parse_api_date(earliest),
                "closing_time": closing, "past_closing_time": past_closing,
                "availability_known": True,
                "reason": "not_deliverable_by_requested_date" if earliest else "not_available"}

    best = min(usable, key=lambda l: l["availableDate"])
    amount = best.get("availableAmount")
    unit = best.get("salesUnit") or ""
    availability_known = not is_availability_unknown(amount)
    if availability_known:
        fulfil = min(float(amount or 0.0), wanted_sales_units)
    else:
        # Valio's logic allows ordering any quantity when availability is unknown.
        fulfil = wanted_sales_units
    return {
        "can_fulfil": "full" if fulfil >= wanted_sales_units else ("partial" if fulfil > 0 else "none"),
        "fulfil_amount": fulfil,
        "sales_unit": unit,
        "delivery_date": parse_api_date(best["availableDate"]),
        "closing_time": closing,
        "past_closing_time": past_closing,
        "sales_block": False,
        "availability_known": availability_known,
        "reason": "" if availability_known else "availability_unknown",
    }
