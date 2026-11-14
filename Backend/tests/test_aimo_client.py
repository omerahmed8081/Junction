"""The client's own behaviour: rate limits, spacing, caching, and what a
sentinel availability means.

All offline: the HTTP client is replaced with a stub and sleeping is recorded
rather than done, so the suite stays fast and never touches Aimo.

The rules come from Valio's answers (docs/ARCHITECTURE.md section 18):
  * 429 blocks us for 30 seconds, so wait it out rather than failing.
  * A few hundred calls a minute is fine; the same burst in ten seconds is not.
  * availableAmount >= 999999 means availability is unknown, not unlimited.
  * An empty scheduleLines list means not available.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("JUNCTION_API_KEY", "test-key")

from gateway.aimo_client import (  # noqa: E402
    AimoClient,
    AimoError,
    AimoRateLimited,
    answer_delivery,
    is_availability_unknown,
    read_price,
)

WHEN = date(2026, 11, 17)
NOW = datetime(2026, 11, 14, 13, 45, tzinfo=timezone.utc)


class StubResponse:
    def __init__(self, status: int, payload=None, headers=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {"products": []}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


class StubHttp:
    """Returns the queued responses in order and records what was asked for."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def post(self, path, json=None):
        self.calls.append((path, json))
        return self._responses.pop(0) if self._responses else StubResponse(200)

    def close(self):
        pass


def build(responses):
    client = AimoClient.__new__(AimoClient)       # no network in the constructor
    client._client = StubHttp(responses)
    client._cache = {}
    client.slept: list[float] = []
    client._sleep = client.slept.append
    client._last_call_at = 0.0
    return client


def test_a_429_is_waited_out_and_retried():
    client = build([StubResponse(429), StubResponse(200, {"products": [{"productId": "1"}]})])
    out = client.prices(["1"], WHEN)
    assert "1" in out
    assert len(client._client.calls) == 2
    assert 30.0 in client.slept           # Valio's stated 30-second block


def test_retry_after_header_is_honoured():
    client = build([StubResponse(429, headers={"Retry-After": "5"}), StubResponse(200)])
    client.prices(["1"], WHEN)
    assert 5.0 in client.slept and 30.0 not in client.slept


def test_a_persistent_429_raises_rather_than_looping_forever():
    client = build([StubResponse(429), StubResponse(429), StubResponse(429)])
    try:
        client.prices(["1"], WHEN)
    except AimoRateLimited:
        pass
    else:
        raise AssertionError("a persistent 429 should raise AimoRateLimited")
    assert len(client._client.calls) == 3      # the first try plus two retries


def test_other_errors_are_not_retried():
    client = build([StubResponse(500, text="boom")])
    try:
        client.prices(["1"], WHEN)
    except AimoRateLimited:
        raise AssertionError("500 is not a rate limit")
    except AimoError:
        pass
    assert len(client._client.calls) == 1


def test_calls_are_spaced_so_a_shortlist_never_bursts():
    client = build([StubResponse(200), StubResponse(200)])
    client.prices(["1"], WHEN)
    client.availability(["1"], WHEN)
    assert any(0 < s <= 0.25 for s in client.slept), client.slept


def test_the_same_question_is_asked_once():
    client = build([StubResponse(200, {"products": [{"productId": "1"}]})])
    client.prices(["1"], WHEN)
    client.prices(["1"], WHEN)
    assert len(client._client.calls) == 1


def test_sentinels_mean_availability_unknown():
    for amount in (999_999.0, 666_666_666.0, 9_999_999_999.0):
        assert is_availability_unknown(amount)
    for amount in (None, 0.0, 51.0, 998_000.0):
        assert not is_availability_unknown(amount)


def test_unknown_availability_allows_the_quantity_but_confirms_nothing():
    raw = {
        "closingTime": "2026-11-15T17:00:00+0200",
        "salesBlock": False,
        "scheduleLines": [{"salesBlock": False, "availableAmount": 9_999_999_999.0,
                           "availableDate": "2026-11-16", "salesUnit": "ST"}],
    }
    a = answer_delivery(raw, 1000, WHEN, now=NOW)
    assert a["can_fulfil"] == "full" and a["fulfil_amount"] == 1000
    assert a["availability_known"] is False
    assert a["reason"] == "availability_unknown"


def test_a_real_count_below_the_need_is_partial_and_is_known():
    raw = {
        "closingTime": "2026-11-15T17:00:00+0200",
        "salesBlock": False,
        "scheduleLines": [{"salesBlock": False, "availableAmount": 51.0,
                           "availableDate": "2026-11-16", "salesUnit": "ST"}],
    }
    a = answer_delivery(raw, 1000, WHEN, now=NOW)
    assert a["can_fulfil"] == "partial" and a["fulfil_amount"] == 51
    assert a["availability_known"] is True


def test_an_empty_schedule_means_not_available():
    a = answer_delivery({"salesBlock": False, "scheduleLines": []}, 10, WHEN, now=NOW)
    assert a["can_fulfil"] == "none" and a["reason"] == "not_available"


def test_a_warehouse_count_is_never_echoed_back():
    """The gateway's promise: fulfil_amount is capped at what was asked for."""
    raw = {
        "closingTime": "2026-11-15T17:00:00+0200",
        "salesBlock": False,
        "scheduleLines": [{"salesBlock": False, "availableAmount": 5000.0,
                           "availableDate": "2026-11-16", "salesUnit": "ST"}],
    }
    a = answer_delivery(raw, 10, WHEN, now=NOW)
    assert a["fulfil_amount"] == 10 and a["can_fulfil"] == "full"


def test_net_price_not_found_is_still_a_usable_price():
    raw = {"catalogPrice": {"value": 31.31, "valueWithTax": 35.54,
                            "validTo": "9999-12-31T00:00:00+0200"},
           "comparisonPrice": {"value": 1.57}, "comparisonUnit": "L",
           "tax": {"taxRate": 13.5}, "netPriceFound": False, "error": False}
    p = read_price(raw)
    assert p["unavailable_reason"] is None
    assert p["price_per_sales_unit"] == 31.31
    assert p["customer_specific"] is False
    assert p["valid_to"] is None              # year 9999 means no end date


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(f"\n{passed} passed")
