"""Changing a basket by saying so (kitchen/edits.py).

The risk here is not failing to understand - it is understanding wrongly and
silently changing the wrong line of somebody's order. So most of these pin what
is *not* treated as a change.
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kitchen import edits  # noqa: E402
from shared.schemas import Basket, BasketLine, DeliveryAnswer  # noqa: E402


def a_line(product_id="464306", name="Valio Eila 3 % milk drink 1 l lactose free",
           sales_unit="ST", quantity=40.0, size=1.0, need_unit="L") -> BasketLine:
    return BasketLine(
        product_id=product_id, name=name, need_value=quantity, need_unit=need_unit,
        sales_unit=sales_unit, base_unit="L", base_units_per_sales_unit=1.0,
        size_per_sales_unit=size, size_unit=need_unit, quantity_sales_units=quantity,
        delivered_amount=quantity, price_per_sales_unit=1.4,
        delivery=DeliveryAnswer(can_fulfil="full"))


def a_basket(*lines) -> Basket:
    return Basket(basket_id="b_1", requested_date=date(2026, 11, 17),
                  lines=list(lines) or [a_line()])


# ---- removing ----------------------------------------------------------

def test_dropping_a_line_by_name():
    basket = a_basket(a_line(name="milk"), a_line(product_id="475214", name="AGRINO rice"))
    for message in ["drop the rice", "remove the rice", "take the rice off",
                    "cancel the rice please", "get rid of the rice"]:
        change = edits.read(basket, message)
        assert change and change.action == "remove", message
        assert change.line.product_id == "475214", message


def test_removing_by_product_number():
    basket = a_basket(a_line(name="milk"), a_line(product_id="475214", name="AGRINO rice"))
    change = edits.read(basket, "remove 475214")
    assert change.action == "remove" and change.line.product_id == "475214"


# ---- changing an amount ------------------------------------------------

def test_make_it_twenty_with_one_line_in_the_basket():
    change = edits.read(a_basket(), "make it 20")
    assert change.action == "quantity" and change.quantity_sales_units == 20.0


def test_naming_the_line_and_the_amount():
    basket = a_basket(a_line(name="milk"), a_line(product_id="475214", name="AGRINO rice"))
    change = edits.read(basket, "change the rice to 3")
    assert change.line.product_id == "475214" and change.quantity_sales_units == 3.0


def test_only_ten_kilos_of_butter():
    basket = a_basket(a_line(product_id="462421", name="butter", sales_unit="ST",
                             quantity=20, size=0.5, need_unit="KG"))
    change = edits.read(basket, "only 10 kg of butter")
    # 10 kg in 0.5 kg packs is 20 packs.
    assert change.action == "quantity" and change.quantity_sales_units == 20.0


def test_a_spoken_amount_lands_on_whole_packs():
    """7 L of a 2 L pack is four packs, not three and a half."""
    basket = a_basket(a_line(sales_unit="ST", size=2.0, need_unit="L"))
    change = edits.read(basket, "make it 7 L")
    assert change.quantity_sales_units == 4.0
    assert "rounded_up_to_whole_packs" in change.flags


def test_an_amount_in_the_lines_own_unit_is_taken_as_it_is():
    basket = a_basket(a_line(sales_unit="ST", size=1.0))
    assert edits.read(basket, "make it 12 units").quantity_sales_units == 12.0


# ---- what must NOT be read as a change ---------------------------------

def test_a_new_request_is_not_a_change():
    basket = a_basket()
    for message in ["20 kg butter by friday", "I also need 10 L cream",
                    "add 5 kg rice", "1,000 L lactose-free milk by Tuesday"]:
        assert edits.read(basket, message) is None, message


def test_a_date_follow_up_is_not_a_change():
    for message in ["move it to monday", "I need these by friday not thursday"]:
        assert edits.read(a_basket(), message) is None, message


def test_nothing_is_changed_when_no_line_is_named():
    """Two lines and "make it 20" names neither: do nothing rather than guess."""
    basket = a_basket(a_line(name="milk"), a_line(product_id="475214", name="AGRINO rice"))
    assert edits.read(basket, "make it 20") is None


def test_two_lines_matching_equally_asks_which():
    basket = a_basket(a_line(product_id="1", name="Valio whipping cream"),
                      a_line(product_id="2", name="Valio cooking cream"))
    try:
        edits.read(basket, "drop the cream")
    except edits.Ambiguous as exc:
        assert len(exc.names) == 2
        return
    raise AssertionError("two cream lines should have been ambiguous")


def test_an_empty_basket_is_never_an_edit():
    assert edits.read(Basket(basket_id="b_2", requested_date=date(2026, 11, 17), lines=[]),
                      "make it 20") is None


def test_zero_and_nonsense_amounts_are_refused():
    for message in ["make it 0", "make it none", "change it to -5"]:
        change = edits.read(a_basket(), message)
        assert change is None or (change.quantity_sales_units or 0) > 0, message


def test_a_product_number_is_not_read_as_an_amount():
    basket = a_basket(a_line(name="milk"), a_line(product_id="475214", name="AGRINO rice"))
    change = edits.read(basket, "change 475214 to 3")
    assert change.line.product_id == "475214" and change.quantity_sales_units == 3.0


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(passed)
