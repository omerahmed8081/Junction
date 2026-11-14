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

from kitchen.need import (  # noqa: E402
    MAX_ITEMS_PER_MESSAGE, date_only_change, intent_from_reply,
    needs_from_reply, parse_with_rules,
)

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


# ---- a product named by number ----------------------------------------

def test_a_product_number_is_read_from_the_message():
    for message, expected in [
        ("40 units of 464306 by Tuesday", "464306"),
        ("#464306 x 40 by Monday", "464306"),
        ("5 ST id 475214 for friday", "475214"),
    ]:
        need = parse_with_rules(message, SATURDAY)
        assert need.product_id == expected, message
        # The number is the request; leftover words are noise.
        assert need.product_text == expected


def test_an_amount_is_never_mistaken_for_a_product_number():
    """100000 L is a hundred thousand litres, not product 100000."""
    need = parse_with_rules("100000 L milk by Tuesday", SATURDAY)
    assert need.product_id is None
    assert need.quantity == 100000.0 and need.product_text == "milk"


def test_a_named_product_does_not_need_a_unit_word():
    need = parse_with_rules("40 of 464306 by Monday", SATURDAY)
    assert need.product_id == "464306" and need.quantity == 40.0 and need.unit == "ST"


def test_a_number_with_no_quantity_asks_how_many():
    try:
        parse_with_rules("464306 by Monday", SATURDAY)
    except ValueError as exc:
        assert "464306" in str(exc) and "How many" in str(exc)
        return
    raise AssertionError("a product number with no quantity should ask how many")


def test_an_ordinary_request_has_no_product_number():
    assert parse_with_rules("1,000 L lactose-free milk by Tuesday", SATURDAY).product_id is None


def test_the_model_can_name_a_product_too():
    needs = needs_from_reply(reply(an_item(product_text="464306", product_id="464306")))
    assert needs[0].product_id == "464306"


# ---- following up on a basket already on screen ------------------------

class FakeBasket:
    """Just enough basket for the parser to know one is open."""

    def __init__(self):
        self.requested_date = date(2026, 11, 19)
        self.lines = [type("L", (), {"product_id": "464306", "name": "milk",
                                     "sales_unit": "ST", "quantity_sales_units": 40})()]


def test_a_follow_up_that_only_moves_the_day_is_recognised():
    for message, expected in [
        ("I need these by friday not thursday", date(2026, 11, 20)),
        ("move it to monday", date(2026, 11, 16)),
        ("deliver on monday instead", date(2026, 11, 16)),
        ("friday", date(2026, 11, 20)),
    ]:
        assert date_only_change(message, SATURDAY) == expected, message


def test_a_message_asking_for_something_is_never_a_date_change():
    for message in ["40 L milk by tuesday", "20 kg butter on friday",
                    "I also need butter"]:
        assert date_only_change(message, SATURDAY) is None, message


def test_a_vague_follow_up_is_not_guessed_at():
    """"a day later" names no day, and the date parser falls back to a default
    lead time. Moving the basket to that would be inventing an answer."""
    assert date_only_change("can that come a day later", SATURDAY) is None


def test_the_model_can_answer_with_a_date_change():
    got = intent_from_reply(json.dumps({"intent": "change_date",
                                        "requested_date": "2026-11-20"}),
                            basket=FakeBasket())
    assert got.intent == "change_date" and got.requested_date == date(2026, 11, 20)


def test_a_date_change_with_no_basket_open_is_refused():
    try:
        intent_from_reply(json.dumps({"intent": "change_date",
                                      "requested_date": "2026-11-20"}), basket=None)
    except ValueError:
        return
    raise AssertionError("a date change needs a basket to change")


def test_an_ordinary_reply_is_still_items():
    got = intent_from_reply(reply(an_item()), basket=FakeBasket())
    assert got.intent == "add" and len(got.items) == 1


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(passed)
