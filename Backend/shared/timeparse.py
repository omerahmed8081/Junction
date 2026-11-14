"""Parsing the timestamps Aimo's API actually sends.

Every timestamp in the mock's responses uses an offset with no colon:

    "closingTime": "2026-11-12T15:00:00+0200"
    "validTo":     "9999-12-31T00:00:00+0200"

`datetime.fromisoformat` only accepts that form from Python 3.11. On 3.10 it
raises ValueError, so a laptop or a deployment host on 3.10 crashes on every
delivery check while a 3.12 laptop passes its tests. Parse through here instead
of calling `fromisoformat` on an API string anywhere.

`validTo` of year 9999 is the API's way of saying "no end date", not a date to
show anyone, so `parse_api_datetime` returns None for it unless asked otherwise.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

# "+0200" or "-0500" at the end of the string, i.e. an offset without its colon.
_OFFSET_NO_COLON = re.compile(r"([+-])(\d{2})(\d{2})$")

OPEN_ENDED_YEAR = 9999


def normalise_offset(text: str) -> str:
    """Make an offset something every supported Python can read."""
    text = text.strip()
    if text.endswith(("Z", "z")):
        return text[:-1] + "+00:00"
    return _OFFSET_NO_COLON.sub(r"\1\2:\3", text)


def is_open_ended(value: Any) -> bool:
    """True for the year-9999 sentinel, whether it arrives as text or a date."""
    if isinstance(value, (datetime, date)):
        return value.year >= OPEN_ENDED_YEAR
    return isinstance(value, str) and value.strip().startswith(str(OPEN_ENDED_YEAR))


def parse_api_datetime(value: Any, keep_open_ended: bool = False) -> datetime | None:
    """A timezone-aware datetime, or None.

    None for an empty value, for something unparseable, and for the year-9999
    sentinel unless `keep_open_ended` is set. A value with no offset is read as
    UTC: Aimo always sends one, so that branch is defensive only, but it keeps
    comparisons with an aware "now" from raising TypeError.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    else:
        try:
            parsed = datetime.fromisoformat(normalise_offset(str(value)))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if not keep_open_ended and parsed.year >= OPEN_ENDED_YEAR:
        return None
    return parsed


def parse_api_date(value: Any) -> date | None:
    """A date, from "2026-11-16" or from a full timestamp."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        parsed = parse_api_datetime(text, keep_open_ended=True)
        return parsed.date() if parsed else None
