"""check_delivery on the recorded hour 0 responses: no network, no database."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gateway.delivery import build_response  # noqa: E402
from shared.schemas import CheckDeliveryItem  # noqa: E402

SAMPLES = ROOT / "Data" / "mock_samples"
PRICES = {p["productId"]: p for p in json.loads((SAMPLES / "price_milk.json").read_text())["products"]}
AVAILABILITY = {p["productId"]: p for p in json.loads((SAMPLES / "availability_milk.json").read_text())["products"]}
SALES_UNITS = {pid: (raw.get("scheduleLines") or [{}])[0].get("salesUnit", "ST") for pid, raw in AVAILABILITY.items()}
WHEN_RECORDED = datetime.fromisoformat("2026-11-14T03:40:09+02:00")
TUESDAY = date(2026, 11, 17)


def check(product_id: str, quantity: float, now: datetime = WHEN_RECORDED):
    items = [CheckDeliveryItem(product_id=product_id, quantity_sales_units=quantity)]
    return build_response(items, TUESDAY, PRICES, AVAILABILITY, SALES_UNITS, now=now).results[0]


def test_cheapest_milk_is_fully_deliverable():
    # The demo request: 464306 for 1,000 units on 2026-11-17.
    r = check("464306", 1000)
    assert r.delivery.can_fulfil == "full" and r.delivery.fulfil_amount == 1000
    assert r.delivery.delivery_date == date(2026, 11, 16)
    assert r.delivery.closing_time == datetime.fromisoformat("2026-11-15T17:00:00+02:00")
    assert not r.delivery.past_closing_time and not r.delivery.sales_block
    assert r.price.price_per_sales_unit == 1.01 and r.price.price_unit == "ST"
    assert r.price.comparison_price == 1.01 and r.price.comparison_unit == "L"
    assert r.price.tax_rate_percent == 13.5 and r.price.customer_specific is True
    assert r.price.unavailable_reason is None


def test_warehouse_count_never_leaves_the_gateway():
    # 464306 reports the sentinel 9999999999; 462775 really has 51.
    assert check("464306", 1000).delivery.fulfil_amount == 1000
    assert check("464306", 3).delivery.fulfil_amount == 3
    small = check("462775", 20)
    assert small.delivery.can_fulfil == "full" and small.delivery.fulfil_amount == 20   # not 51
    dumped = check("464306", 1000).model_dump_json() + small.model_dump_json()
    assert "9999999999" not in dumped and "availableAmount" not in dumped and '51' not in small.model_dump_json()


def test_partial_when_stock_is_short():
    r = check("462775", 1000)
    assert r.delivery.can_fulfil == "partial" and r.delivery.fulfil_amount == 51


def test_nothing_deliverable_when_there_are_no_schedule_lines():
    r = check("464440", 10)
    assert r.delivery.can_fulfil == "none" and r.delivery.fulfil_amount == 0
    assert r.price.price_per_sales_unit is not None      # a price exists; it just cannot be delivered


def test_passed_cutoff_is_flagged():
    # The 10 L Novobox: cutoff was 12 November, so it cannot be bought for the 17th.
    r = check("463038", 50)
    assert r.delivery.past_closing_time is True
    assert r.price.price_per_sales_unit == 31.31 and r.price.price_unit == "KI"


def test_cutoff_flips_once_the_closing_time_passes():
    before = check("464306", 10, now=datetime.fromisoformat("2026-11-15T16:59:00+02:00"))
    after = check("464306", 10, now=datetime.fromisoformat("2026-11-15T17:01:00+02:00"))
    assert not before.delivery.past_closing_time and after.delivery.past_closing_time


def test_catalogue_price_is_not_an_error():
    # netPriceFound false: no customer price, the catalogue price applies.
    r = check("463038", 50)
    assert r.price.customer_specific is False and r.price.unavailable_reason is None


def test_unknown_product_is_answered_not_sent():
    r = check("000000", 5)
    assert r.price.unavailable_reason == "unknown_product" and r.delivery.can_fulfil == "none"


def test_results_keep_request_order():
    items = [CheckDeliveryItem(product_id=p, quantity_sales_units=1) for p in ("463038", "464306", "462775")]
    out = build_response(items, TUESDAY, PRICES, AVAILABILITY, SALES_UNITS, now=WHEN_RECORDED)
    assert [r.product_id for r in out.results] == ["463038", "464306", "462775"]
    assert out.requested_date == TUESDAY and out.checked_at == WHEN_RECORDED


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(f"\n{passed} passed")


# ------------------------------------------------- the demo clock (AIMO_CUTOFF_NOW)


def _availability(closing: str, available: str = "2026-11-16") -> dict:
    return {"closingTime": closing, "salesBlock": False,
            "scheduleLines": [{"salesBlock": False, "availableAmount": 999999.0,
                               "availableDate": available, "salesUnit": "ST"}]}


def _price() -> dict:
    return {"prices": [{"price": 1.01, "salesUnit": "ST"}]}


def _one(now_env: str | None, monkeypatch) -> object:
    """One check_delivery answer for a product whose cut-off passed on 12 Nov."""
    from gateway import delivery
    from shared.schemas import CheckDeliveryItem

    if now_env is None:
        monkeypatch.delenv("AIMO_CUTOFF_NOW", raising=False)
    else:
        monkeypatch.setenv("AIMO_CUTOFF_NOW", now_env)
    answer = delivery.build_response(
        [CheckDeliveryItem(product_id="461490", quantity_sales_units=1)],
        date(2026, 11, 18),
        {"461490": _price()},
        {"461490": _availability("2026-11-12T15:00:00+02:00")},
        {"461490": "ST"})
    return answer.results[0].delivery


def test_without_the_demo_clock_a_passed_cutoff_is_passed(monkeypatch):
    assert _one(None, monkeypatch).past_closing_time is True


def test_the_demo_clock_judges_the_cutoff_at_the_stated_moment(monkeypatch):
    """This is the whole point: the cream's window closed on 12 Nov at 15:00
    and never reopens, so a demo has to say which moment it is replaying."""
    assert _one("2026-11-12T10:00:00+02:00", monkeypatch).past_closing_time is False
    assert _one("2026-11-12T16:00:00+02:00", monkeypatch).past_closing_time is True


def test_a_naive_timestamp_is_read_as_utc_and_rubbish_is_ignored(monkeypatch):
    assert _one("2026-11-12T10:00:00", monkeypatch).past_closing_time is False
    # Nonsense must not take the gateway down, and must not quietly pass either.
    assert _one("the day before yesterday", monkeypatch).past_closing_time is True


def test_an_explicit_now_still_wins_so_the_recorded_tests_hold(monkeypatch):
    from gateway import delivery
    from shared.schemas import CheckDeliveryItem

    monkeypatch.setenv("AIMO_CUTOFF_NOW", "2026-11-12T10:00:00+02:00")
    answer = delivery.build_response(
        [CheckDeliveryItem(product_id="461490", quantity_sales_units=1)],
        date(2026, 11, 18), {"461490": _price()},
        {"461490": _availability("2026-11-12T15:00:00+02:00")}, {"461490": "ST"},
        now=datetime(2026, 11, 14, 12, tzinfo=timezone.utc))
    assert answer.results[0].delivery.past_closing_time is True


def test_a_quote_is_never_born_expired_from_a_cutoff_already_past():
    """With the demo clock on, a line carries a closing time in the real past.
    Taking it as the quote's expiry would mint one the kitchen must refuse the
    moment it is handed back."""
    from datetime import timedelta

    from gateway.quotes import MAX_QUOTE_TTL, _expiry
    from shared.schemas import DeliveryAnswer

    class Line:
        def __init__(self, closing):
            self.delivery = DeliveryAnswer(can_fulfil="full", closing_time=closing)

    issued = datetime(2026, 11, 15, 3, 50, tzinfo=timezone.utc)
    passed = datetime(2026, 11, 12, 13, 0, tzinfo=timezone.utc)
    assert _expiry([Line(passed)], issued) == issued + MAX_QUOTE_TTL

    # A cut-off still ahead does bound the quote, as it always did.
    soon = issued + timedelta(minutes=30)
    assert _expiry([Line(soon)], issued) == soon
