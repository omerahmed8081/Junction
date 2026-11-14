"""The kitchen's standing rules (kitchen/rules.py).

A rule can only ever relax an approval the kitchen would ask for itself. The
tests that matter are the ones pinning what a rule may *not* do.
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kitchen import rules  # noqa: E402
from shared.schemas import Basket, BasketLine, DeliveryAnswer  # noqa: E402


def a_line(product_id="464306", name="Valio Eila milk 1 l lactose free", net=100.0,
           claims=None, category="", can_fulfil="full", flags=None) -> BasketLine:
    return BasketLine(
        product_id=product_id, name=name, need_value=10, need_unit="L",
        sales_unit="ST", base_unit="L", base_units_per_sales_unit=1.0,
        quantity_sales_units=10, line_net=net, price_per_sales_unit=net / 10,
        claims=claims or [], category=category, flags=flags or [],
        delivery=DeliveryAnswer(can_fulfil=can_fulfil))


def a_basket(*lines, net=None) -> Basket:
    lines = list(lines) or [a_line()]
    basket = Basket(basket_id="b_1", requested_date=date(2026, 11, 17), lines=lines)
    basket.net_total = net if net is not None else sum(l.line_net for l in lines)
    return basket


def milk_rule(**over) -> rules.Rule:
    fields = {"name": "milk", "match": "name", "value": "milk", "max_net_per_order": 200.0}
    fields.update(over)
    return rules.Rule(**fields)


# ---- what a rule does --------------------------------------------------

def test_a_rule_clears_a_basket_it_covers():
    assert rules.applies([milk_rule()], a_basket(a_line(net=150)), []) is not None


def test_a_ceiling_is_a_ceiling():
    assert rules.applies([milk_rule()], a_basket(a_line(net=250)), []) is None


def test_a_rule_with_no_ceiling_covers_any_amount():
    rule = milk_rule(max_net_per_order=None)
    assert rules.applies([rule], a_basket(a_line(net=9_000)), []) is not None


def test_matching_on_a_product_number():
    rule = rules.Rule(match="product", value="464306", max_net_per_order=500)
    assert rules.applies([rule], a_basket(a_line(product_id="464306")), []) is not None
    assert rules.applies([rule], a_basket(a_line(product_id="999999")), []) is None


def test_matching_on_a_claim():
    rule = rules.Rule(match="claim", value="FREE_FROM_LACTOSE", max_net_per_order=500)
    assert rules.applies([rule], a_basket(a_line(claims=["FREE_FROM_LACTOSE"])), []) is not None
    assert rules.applies([rule], a_basket(a_line(claims=["VEGAN"])), []) is None


def test_matching_anything_under_a_ceiling():
    rule = rules.Rule(match="any", max_net_per_order=50)
    assert rules.applies([rule], a_basket(a_line(net=40)), []) is not None
    assert rules.applies([rule], a_basket(a_line(net=60)), []) is None


# ---- what a rule must NOT do -------------------------------------------

def test_every_line_has_to_match_not_just_one():
    """A rule about milk does not wave through a basket that also holds rice:
    the chef never said anything about the rice."""
    basket = a_basket(a_line(name="milk", net=50),
                      a_line(product_id="475214", name="AGRINO rice", net=20))
    assert rules.applies([milk_rule()], basket, []) is None


def test_a_rule_never_clears_a_reason_about_the_order_being_wrong():
    for reason in ["Partial delivery accepted",
                   "Product differs from what was asked for",
                   "Pack size disagrees with the price data"]:
        rule = milk_rule(max_net_per_order=None)
        assert rules.applies([rule], a_basket(a_line(net=10)), [reason]) is None, reason


def test_a_rule_does_clear_a_reason_about_size():
    """Being over the chef's limit is exactly what a rule is for."""
    assert rules.applies([milk_rule()], a_basket(a_line(net=150)),
                         ["Over the EUR 500 chef limit"]) is not None


def test_an_inactive_rule_does_nothing():
    assert rules.applies([milk_rule(active=False)], a_basket(a_line(net=10)), []) is None


def test_an_empty_basket_is_never_cleared():
    basket = Basket(basket_id="b_2", requested_date=date(2026, 11, 17), lines=[])
    assert rules.applies([milk_rule(max_net_per_order=None)], basket, []) is None


def test_no_rules_means_no_change():
    assert rules.applies([], a_basket(), []) is None


# ---- how a rule reads --------------------------------------------------

def test_a_rule_says_itself_in_a_sentence():
    assert "up to EUR 200.00 net" in milk_rule().said()
    assert "without asking" in milk_rule().said()
    assert "any order" in rules.Rule(match="any", max_net_per_order=50).said()
    assert "lactose" in rules.Rule(match="claim", value="FREE_FROM_LACTOSE").said()


# ---- the store ---------------------------------------------------------

def test_rules_are_switched_off_rather_than_deleted():
    """A rule in force when an order went through has to stay readable."""
    store = rules.RuleStore(pool=None)
    kept = store.add(milk_rule())
    assert kept.rule_id is not None
    assert len(store.all()) == 1
    assert store.remove(kept.rule_id) is True
    assert store.all() == []                       # gone from the active list
    assert len(store.all(active_only=False)) == 1  # still on the record


def test_removing_a_rule_that_is_not_there():
    assert rules.RuleStore(pool=None).remove(999) is False


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(passed)
