"""The timestamps Aimo really sends, including the ones Python 3.10 refuses.

Run this on whatever Python you have; it is the one test that would have caught
the version difference between a 3.12 laptop and a 3.10 host.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.timeparse import (  # noqa: E402
    is_open_ended,
    normalise_offset,
    parse_api_date,
    parse_api_datetime,
)

HELSINKI_WINTER = timezone(timedelta(hours=2))
HELSINKI_SUMMER = timezone(timedelta(hours=3))


def test_offset_without_a_colon():
    """Every timestamp in the recorded responses looks like this."""
    assert parse_api_datetime("2026-11-15T17:00:00+0200") == datetime(
        2026, 11, 15, 17, 0, tzinfo=HELSINKI_WINTER
    )
    assert parse_api_datetime("2025-09-01T00:00:00+0300") == datetime(
        2025, 9, 1, 0, 0, tzinfo=HELSINKI_SUMMER
    )


def test_offset_with_a_colon_still_works():
    assert parse_api_datetime("2026-11-15T17:00:00+02:00") == datetime(
        2026, 11, 15, 17, 0, tzinfo=HELSINKI_WINTER
    )


def test_year_9999_means_no_end_date():
    # catalogPrice.validTo is 9999-12-31 for most products.
    assert parse_api_datetime("9999-12-31T00:00:00+0200") is None
    assert parse_api_datetime("9999-12-31T00:00:00+0200", keep_open_ended=True) is not None
    assert is_open_ended("9999-12-31T00:00:00+0200")
    assert not is_open_ended("2026-12-31T00:00:00+0200")


def test_a_real_validity_date_is_kept():
    assert parse_api_datetime("2026-12-31T00:00:00+0200") == datetime(
        2026, 12, 31, 0, 0, tzinfo=HELSINKI_WINTER
    )


def test_empty_and_unparseable():
    for junk in (None, "", "not a date", "2026-13-45T99:00:00+0200"):
        assert parse_api_datetime(junk) is None


def test_result_is_always_comparable_with_an_aware_now():
    """The bug this guards: comparing a naive datetime with an aware one raises."""
    now = datetime.now(timezone.utc)
    for text in ("2026-11-12T15:00:00+0200", "2026-11-15T17:00:00+02:00", "2026-11-15T17:00:00Z"):
        parsed = parse_api_datetime(text)
        assert parsed is not None and (parsed < now or parsed >= now)


def test_naive_input_is_read_as_utc_rather_than_crashing():
    parsed = parse_api_datetime("2026-11-15T17:00:00")
    assert parsed is not None and parsed.tzinfo is timezone.utc


def test_dates():
    assert parse_api_date("2026-11-16") == date(2026, 11, 16)
    assert parse_api_date("2026-11-16T00:00:00+0200") == date(2026, 11, 16)
    assert parse_api_date(date(2026, 11, 16)) == date(2026, 11, 16)
    assert parse_api_date(None) is None
    assert parse_api_date("rubbish") is None


def test_normalise_offset():
    assert normalise_offset("2026-11-15T17:00:00+0200") == "2026-11-15T17:00:00+02:00"
    assert normalise_offset("2026-11-15T17:00:00Z") == "2026-11-15T17:00:00+00:00"
    assert normalise_offset("2026-11-15T17:00:00+02:00") == "2026-11-15T17:00:00+02:00"
    # A bare date has no offset to move, and digits in it must not be mangled.
    assert normalise_offset("2026-11-16") == "2026-11-16"


def test_every_timestamp_in_the_recorded_responses_parses():
    """Belt and braces: walk the saved hour 0 payloads and parse everything."""
    import json
    import re

    samples = Path(__file__).resolve().parent.parent / "Data" / "mock_samples"
    files = sorted(samples.glob("*.json")) if samples.exists() else []
    if not files:
        return  # the saved responses are optional for whoever just cloned
    pattern = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:?\d{2}")
    seen = 0
    for path in files:
        for text in set(pattern.findall(path.read_text(encoding="utf-8"))):
            seen += 1
            assert parse_api_datetime(text, keep_open_ended=True) is not None, text
    assert seen > 0


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(f"\n{passed} passed")
