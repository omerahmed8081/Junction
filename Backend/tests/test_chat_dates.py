"""Whether a chat message names a delivery date (kitchen.need.mentioned_date).

A follow-up such as "add 10 L cream" must keep the basket's date. Only a message
that names a day moves the basket, and a message that only names a day
("deliver on Wednesday instead") moves it without adding anything.
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kitchen.need import mentioned_date  # noqa: E402

SATURDAY = date(2026, 11, 14)


def test_no_date_means_none_so_the_basket_keeps_its_date():
    for message in ("add 5 L milk", "1,000 L milk", "10 L whipping cream", "3.5 kg butter", "2,5 kg rice"):
        assert mentioned_date(message, SATURDAY) is None, message


def test_english_days_and_iso_dates():
    assert mentioned_date("5 kg rice by Tuesday", SATURDAY) == date(2026, 11, 17)
    assert mentioned_date("deliver on Friday instead", SATURDAY) == date(2026, 11, 20)
    assert mentioned_date("milk tomorrow", SATURDAY) == date(2026, 11, 15)
    assert mentioned_date("by 2026-11-20", SATURDAY) == date(2026, 11, 20)


def test_a_weekday_is_the_next_one_never_today():
    assert mentioned_date("on Saturday", SATURDAY) == date(2026, 11, 21)


def test_finnish_days_and_dates():
    assert mentioned_date("toimitus perjantaina", SATURDAY) == date(2026, 11, 20)
    assert mentioned_date("5 kg riisiä torstaiksi", SATURDAY) == date(2026, 11, 19)
    assert mentioned_date("huomenna", SATURDAY) == date(2026, 11, 15)
    assert mentioned_date("kermaa 18.11.", SATURDAY) == date(2026, 11, 18)
    assert mentioned_date("18.11.2026", SATURDAY) == date(2026, 11, 18)


def test_a_past_day_and_month_means_next_year():
    assert mentioned_date("12.5.", SATURDAY) == date(2027, 5, 12)


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(passed)
