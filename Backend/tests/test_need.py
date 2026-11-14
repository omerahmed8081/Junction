"""Reading a kitchen's message (kitchen/need.py).

Nobody shops one item at a time, so a message usually holds several needs. The
model returns them as a list; these tests pin what happens to that list,
without calling the model - which is where the surprises live.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kitchen.need import MAX_ITEMS_PER_MESSAGE, needs_from_reply, parse_with_rules  # noqa: E402

TUESDAY = "2026-11-17"
SATURDAY = date(2026, 11, 14)


def reply(*items) -> str:
    return json.dumps({"items": list(items)})


def an_item(**over) -> dict:
    item = {"product_text": "milk", "quantity": 40, "unit": "L", "claims": [],
            "pack_size": None, "pack_size_unit": "", "brand": None,
            "requested_date": TUESDAY}
    item.update(over)
    return item


def test_two_products_in_one_message():
    needs = needs_from_reply(reply(
        an_item(product_text="milk", quantity=40, unit="L"),
        an_item(product_text="rice", quantity=5, unit="KG")))
    assert [(n.product_text, n.quantity, n.unit) for n in needs] == [
        ("milk", 40.0, "L"), ("rice", 5.0, "KG")]


def test_the_order_asked_for_is_the_order_kept():
    needs = needs_from_reply(reply(
        an_item(product_text="butter"), an_item(product_text="cream"),
        an_item(product_text="milk")))
    assert [n.product_text for n in needs] == ["butter", "cream", "milk"]


def test_a_date_that_applies_to_the_whole_message_reaches_every_item():
    needs = needs_from_reply(reply(
        an_item(product_text="milk", requested_date=TUESDAY),
        an_item(product_text="rice", requested_date=TUESDAY)))
    assert all(str(n.requested_date) == TUESDAY for n in needs)


def test_a_single_item_still_works_as_a_bare_object():
    """An older prompt, or a terse reply, must not break the chat."""
    needs = needs_from_reply(json.dumps(an_item(product_text="butter", quantity=20, unit="KG")))
    assert len(needs) == 1 and needs[0].product_text == "butter"


def test_one_bad_entry_does_not_lose_the_good_ones():
    needs = needs_from_reply(reply(
        an_item(product_text="milk"),
        {"product_text": "", "quantity": 0, "unit": "L"},      # nothing usable
        "not even an object",
        an_item(product_text="rice", unit="KG", quantity=5)))
    assert [n.product_text for n in needs] == ["milk", "rice"]


def test_an_unknown_claim_is_dropped_rather_than_trusted():
    needs = needs_from_reply(reply(
        an_item(claims=["FREE_FROM_LACTOSE", "INVENTED_BY_THE_MODEL"])))
    assert needs[0].claims == ["FREE_FROM_LACTOSE"]


def test_a_lowercase_unit_is_accepted():
    assert needs_from_reply(reply(an_item(unit="kg", quantity=5)))[0].unit == "KG"


def test_a_very_long_list_is_capped():
    needs = needs_from_reply(reply(*[an_item(product_text=f"item{i}") for i in range(40)]))
    assert len(needs) == MAX_ITEMS_PER_MESSAGE


def test_nothing_usable_is_an_error_the_chat_can_show():
    for bad in ["", "no json here", "{}", json.dumps({"items": []}),
                reply({"product_text": "milk", "quantity": 0, "unit": "L"})]:
        try:
            needs_from_reply(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should not have parsed")


def test_the_rules_parser_still_reads_one_need():
    """The fallback finds one product, which is exactly why the model earns
    its place for messages that ask for several."""
    need = parse_with_rules("1,000 L lactose-free milk by Tuesday", SATURDAY)
    assert need.product_text == "milk" and need.quantity == 1000.0
    assert need.claims == ["FREE_FROM_LACTOSE"] and need.parser == "rules"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(passed)
