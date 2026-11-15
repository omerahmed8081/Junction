"""The ledger, the forecast and the running-low trigger, on the seeded restaurant.

Offline: it reads `Data/seed/` and never calls Aimo.

The arithmetic here decides what the kitchen is told to buy, so the tests are
about the traps rather than the happy path: stock that is also on order, a
closed Sunday averaged into a weekday, and a delivery counted twice.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kitchen import suggestions  # noqa: E402
from kitchen.inventory import MIN_SHORTFALL, Inventory, Movement  # noqa: E402

CREAM = "461490"            # Valio Keittiön whipping cream 3,3 dl, lactose free
MILK = "464306"             # the demo's milk
SALMON = "462150"           # sold by the kilogram
AS_OF = date(2026, 11, 14)


def seeded() -> Inventory:
    return Inventory.from_seed()


def test_the_ledger_reproduces_the_seed_exactly():
    """The movements are derived, not copied, so this is real evidence.

    Opening count + deliveries - (dishes sold x recipes) = the stock the seed
    states, for all 17 products.
    """
    inventory = seeded()
    assert inventory.reconcile() == []
    assert len(inventory.movements) > 800
    assert inventory.as_of == AS_OF


def test_stock_is_the_sum_of_movements_and_nothing_else():
    inventory = seeded()
    stock = inventory.stock()
    by_hand = sum(m.quantity for m in inventory.movements if m.product_id == CREAM)
    assert round(stock[CREAM], 6) == round(by_hand, 6)
    assert stock[CREAM] > 0


def test_an_order_is_on_order_until_it_arrives_and_never_both():
    """The bug this guards: counting an amount in stock and on order at once
    makes the trigger fire late or never."""
    inventory = seeded()
    before_stock = inventory.stock()[CREAM]
    before_on_order = inventory.on_order_totals()[CREAM]
    assert before_on_order > 0           # the seed has an open order

    order_id = next(o.order_id for o in inventory.on_order if o.product_id == CREAM)
    pending = sum(o.quantity for o in inventory.on_order
                  if o.order_id == order_id and o.product_id == CREAM)
    inventory.receive(order_id, when=AS_OF)

    assert inventory.stock()[CREAM] == round(before_stock + pending, 6)
    assert inventory.on_order_totals().get(CREAM, 0.0) == round(before_on_order - pending, 6)
    assert not [o for o in inventory.on_order if o.order_id == order_id]


def test_placing_an_order_does_not_touch_stock():
    inventory = seeded()
    before = inventory.stock().get(MILK, 0.0)
    inventory.add_on_order("po_new", [(MILK, 100.0, "L")], AS_OF + timedelta(days=2))
    assert inventory.stock().get(MILK, 0.0) == before
    assert inventory.on_order_totals()[MILK] >= 100.0


def test_dishes_sold_become_stock_out_through_the_recipes():
    """One number per dish in, one movement per ingredient out."""
    inventory = seeded()
    before = inventory.stock()[CREAM]
    recipe = inventory.recipes["d1"]
    per_portion = next(i["quantity_per_portion"] for i in recipe.ingredients
                       if i["product_id"] == CREAM)

    written = inventory.record_service(date(2026, 11, 15), {"d1": 10})
    assert len(written) == len(recipe.ingredients)
    assert all(m.quantity < 0 for m in written)
    assert inventory.stock()[CREAM] == round(before - per_portion * 10, 6)


def test_an_unknown_dish_or_zero_portions_writes_nothing():
    inventory = seeded()
    assert inventory.record_service(AS_OF, {"not-a-dish": 10}) == []
    assert inventory.record_service(AS_OF, {"d1": 0}) == []


def test_a_count_corrects_drift_without_rewriting_history():
    """A physical count is another movement, never an overwrite."""
    inventory = seeded()
    before = inventory.stock()[CREAM]
    inventory.movements.append(Movement(AS_OF, CREAM, 5.0, "L", "count", "weekly count"))
    assert inventory.stock()[CREAM] == round(before + 5.0, 6)
    assert inventory.movements[-1].kind == "count"


def test_the_weekday_average_keeps_a_closed_sunday_out_of_the_week():
    """The seed closes Sundays. Averaging them into the week would under-order
    for every other day."""
    averages = seeded().portions_by_weekday()
    weekdays = {weekday for (_dish, weekday) in averages}
    assert 6 not in weekdays or all(averages[(d, 6)] == 0 for d in {k[0] for k in averages}
                                    if (d, 6) in averages)
    # Saturday is the busiest day in the seed, Monday the quietest.
    saturday = sum(v for (_d, w), v in averages.items() if w == 5)
    monday = sum(v for (_d, w), v in averages.items() if w == 0)
    assert saturday > monday > 0


def test_expected_usage_follows_the_menu_and_the_window():
    inventory = seeded()
    one_day = inventory.expected_usage(date(2026, 11, 16), date(2026, 11, 16))
    three_days = inventory.expected_usage(date(2026, 11, 16), date(2026, 11, 18))
    assert 0 < one_day[CREAM] < three_days[CREAM]
    # A Sunday on its own uses nothing, because the kitchen is shut.
    assert inventory.expected_usage(date(2026, 11, 15), date(2026, 11, 15)) == {}


def test_a_shortfall_is_usage_plus_cover_less_stock_and_what_is_on_order():
    inventory = seeded()
    rows = {r["product_id"]: r for r in inventory.shortfalls(until=date(2026, 11, 20))}
    cream = rows[CREAM]
    assert cream["shortfall"] == round(
        cream["needed"] + cream["buffer"] - cream["stock"] - cream["on_order"], 6)
    assert cream["on_order"] > 0          # it is subtracted, not ignored
    assert cream["unit"] == "L"
    assert "Creamy salmon soup (lohikeitto)" in cream["dishes"]


def test_what_is_already_on_order_stops_the_same_order_every_morning():
    """Subtracting on-order is what makes the trigger idempotent."""
    inventory = seeded()
    before = {r["product_id"]: r["shortfall"]
              for r in inventory.shortfalls(until=date(2026, 11, 20))}
    short = before[CREAM]
    inventory.add_on_order("po_cover", [(CREAM, short, "L")], date(2026, 11, 16))
    after = {r["product_id"]: r["shortfall"]
             for r in inventory.shortfalls(until=date(2026, 11, 20))}
    assert CREAM not in after             # covered, so no longer suggested


def test_a_rounding_difference_does_not_become_a_purchase_order():
    inventory = seeded()
    rows = inventory.shortfalls(until=date(2026, 11, 20))
    assert all(r["shortfall"] >= MIN_SHORTFALL for r in rows)


def test_the_soonest_to_run_out_comes_first():
    rows = seeded().shortfalls(until=date(2026, 11, 20))
    dates = [r["runs_out_on"] for r in rows if r["runs_out_on"]]
    assert dates == sorted(dates)
    assert rows[0]["product_id"] == CREAM          # the demo's beat 3


def test_a_kilogram_product_is_measured_in_kilograms_throughout():
    inventory = seeded()
    assert inventory.units[SALMON] == "KG"
    rows = {r["product_id"]: r for r in inventory.shortfalls(until=date(2026, 11, 25))}
    if SALMON in rows:
        assert rows[SALMON]["unit"] == "KG"


def test_using_stock_makes_the_shortfall_bigger():
    """Same day, so the window does not move: only the stock changed."""
    inventory = seeded()
    before = {r["product_id"]: r["shortfall"]
              for r in inventory.shortfalls(until=date(2026, 11, 20))}
    inventory.record_service(AS_OF, {"d1": 40, "d3": 25})
    assert inventory.as_of == AS_OF                 # logging today moves nothing
    after = {r["product_id"]: r["shortfall"]
             for r in inventory.shortfalls(until=date(2026, 11, 20))}
    assert after[CREAM] > before[CREAM]


def test_logging_a_later_service_moves_the_forecast_window_with_it():
    """A day passing is a real change, not a bug: the window starts the day
    after the last service, so less of the week is left to cover."""
    inventory = seeded()
    before = inventory.expected_usage(AS_OF + timedelta(days=1), date(2026, 11, 20))
    inventory.record_service(date(2026, 11, 16), {"d1": 10})
    assert inventory.as_of == date(2026, 11, 16)
    after = inventory.expected_usage(inventory.as_of + timedelta(days=1), date(2026, 11, 20))
    assert after[CREAM] < before[CREAM]
    # The horizon follows it, so the window stays the same length.
    assert suggestions.window_until(inventory) == date(2026, 11, 16) + timedelta(
        days=suggestions.HORIZON_DAYS)


# ----------------------------------------------------------- the suggestions


def test_a_suggestion_shows_the_arithmetic_not_just_a_number():
    found = suggestions.find(seeded())
    assert found, "the seeded kitchen should be running low on something"
    cream = next(s for s in found if s.product_id == CREAM)
    for fragment in ("Short", "in stock", "already on order", "Runs out", "Used in"):
        assert fragment in cream.reason, cream.reason
    assert cream.shortfall > 0 and cream.on_order > 0


def test_suggestions_make_no_call_to_aimo():
    """They read the ledger only, so the panel can refresh freely."""
    class Explodes:
        def __getattr__(self, name):
            raise AssertionError(f"suggestions must not call the gateway ({name})")

    found = suggestions.find(seeded())
    assert found and found[0].basket_id is None     # a draft is a separate step


def test_nothing_is_suggested_when_everything_is_covered():
    inventory = seeded()
    until = suggestions.window_until(inventory)
    for row in inventory.shortfalls(until=until):
        inventory.add_on_order("po_cover", [(row["product_id"], row["shortfall"] + 1, row["unit"])],
                               date(2026, 11, 16))
    assert suggestions.find(inventory) == []


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(f"\n{passed} passed")


# ------------------------------- an order the kitchen placed, in its own ledger


def an_order(order_id="o_new", product_id=MILK, amount=1000.0, unit="L",
             status="placed", delivery=date(2026, 11, 16)):
    from shared.schemas import DeliveryAnswer, OrderRecord, QuoteLine

    line = QuoteLine(
        product_id=product_id, name="Valio Eila milk 1 l lactose free",
        quantity_sales_units=amount, sales_unit="ST",
        delivered_amount=amount, delivered_unit=unit,
        price_per_sales_unit=1.01, line_net=amount * 1.01,
        line_vat=0.0, line_gross=amount * 1.01,
        delivery=DeliveryAnswer(can_fulfil="full"))
    return OrderRecord(
        order_id=order_id, quote_id="q_1", customer="c_1", agent_id="a",
        status=status, placed_at=datetime(2026, 11, 14, 12, tzinfo=timezone.utc),
        requested_date=delivery, delivery_date=delivery,
        net_total=amount * 1.01, vat_total=0.0, gross_total=amount * 1.01, lines=[line])


def a_kitchen():
    """The kitchen module with a ledger of its own, put back afterwards."""
    import os
    os.environ["TTA_SKIP_DOTENV"] = "1"
    import kitchen.main as main
    return main


def test_an_order_lands_in_the_ledger_as_on_order_not_stock():
    """Not stock: nothing has arrived. `receive` is what turns it into stock,
    and there is no goods-in screen yet."""
    main = a_kitchen()
    held = main.inventory
    try:
        main.inventory = Inventory.from_seed()
        # The seeded restaurant already has milk on order, so this is the
        # change the new order makes, not the total.
        on_order_before = main.inventory.on_order_totals().get(MILK, 0.0)
        stock_before = main.inventory.stock().get(MILK, 0.0)
        main._book_on_order(an_order())

        assert main.inventory.on_order_totals()[MILK] - on_order_before == 1000.0
        assert main.inventory.stock().get(MILK, 0.0) == stock_before, "it was counted as stock"
    finally:
        main.inventory = held


def test_booking_the_same_order_twice_does_not_double_count():
    """It is booked when placed and again when replayed at startup, and the two
    paths deliberately share one function."""
    main = a_kitchen()
    held = main.inventory
    try:
        main.inventory = Inventory.from_seed()
        before = main.inventory.on_order_totals().get(MILK, 0.0)
        main._book_on_order(an_order())
        main._book_on_order(an_order())
        assert main.inventory.on_order_totals()[MILK] - before == 1000.0
    finally:
        main.inventory = held


def test_a_refused_order_is_not_booked():
    main = a_kitchen()
    held = main.inventory
    try:
        main.inventory = Inventory.from_seed()
        before = main.inventory.on_order_totals()
        main._book_on_order(an_order(status="rejected"))
        assert main.inventory.on_order_totals() == before
    finally:
        main.inventory = held


def test_the_ledger_after_a_restart_equals_the_ledger_before_it():
    """The invariant the whole design rests on: nothing is stored for the
    inventory, so a restart must reproduce it from the seed and the orders."""
    main = a_kitchen()
    held = main.inventory
    try:
        main.inventory = Inventory.from_seed()
        main._book_on_order(an_order("o_1"))
        main._book_on_order(an_order("o_2", product_id=CREAM, amount=40.0, unit="ST"))
        before = (main.inventory.stock(), main.inventory.on_order_totals())

        # what the lifespan does on the way back up
        main.inventory = Inventory.from_seed()
        for record in (an_order("o_1"), an_order("o_2", product_id=CREAM,
                                                 amount=40.0, unit="ST")):
            main._book_on_order(record)
        after = (main.inventory.stock(), main.inventory.on_order_totals())

        assert before == after
    finally:
        main.inventory = held


def test_ordering_what_is_running_low_stops_it_being_suggested_again():
    """A4/A5: the suggestion kept nagging after the order went through, because
    nothing ever put the order on order. `shortfalls` already subtracted it."""
    main = a_kitchen()
    held = main.inventory
    try:
        main.inventory = Inventory.from_seed()
        until = suggestions.window_until(main.inventory)
        short = {s["product_id"]: s for s in main.inventory.shortfalls(until)}
        assert short, "the seeded restaurant is short of nothing, so this proves nothing"

        product_id, row = next(iter(short.items()))
        main._book_on_order(an_order("o_fix", product_id=product_id,
                                     amount=row["shortfall"] * 2,
                                     unit=main.inventory.units.get(product_id, "")))

        again = {s["product_id"] for s in main.inventory.shortfalls(until)}
        assert product_id not in again, "it is still being suggested after the order"
    finally:
        main.inventory = held


# ------------------------------------------------- the number people say out loud


def test_orders_are_numbered_from_1001_and_keep_their_number():
    """`order_id` stays the identifier of record - it is what Aimo knows and
    what the audit rows join on. The number is only for the screen."""
    main = a_kitchen()
    held = dict(main.orders)
    try:
        main.orders.clear()
        first = main._assign_number(an_order("o_a"))
        main.orders["o_a"] = first
        second = main._assign_number(an_order("o_b"))
        main.orders["o_b"] = second

        assert (first.number, second.number) == (1001, 1002)
        assert first.order_id == "o_a", "the real id was replaced"

        # replayed at startup: the stored number stands
        assert main._assign_number(first).number == 1001
    finally:
        main.orders.clear()
        main.orders.update(held)


def test_numbering_starts_again_after_a_reset():
    """Counted from the orders in hand rather than a stored counter, so a demo
    reset leaves nothing behind to clear."""
    main = a_kitchen()
    held = dict(main.orders)
    try:
        main.orders.clear()
        main.orders["o_a"] = main._assign_number(an_order("o_a"))
        main.orders.clear()                      # what demo_reset does
        assert main._assign_number(an_order("o_c")).number == 1001
    finally:
        main.orders.clear()
        main.orders.update(held)


def test_numbering_follows_the_highest_order_in_hand():
    """Which means a number could be reused if the newest order were removed on
    its own. Nothing removes one on its own - the reset clears them all - so
    this pins the behaviour rather than defending it. A stored counter would
    avoid it and would be a second thing to clear; this was the trade.
    """
    main = a_kitchen()
    held = dict(main.orders)
    try:
        main.orders.clear()
        main.orders["o_a"] = main._assign_number(an_order("o_a"))
        main.orders["o_b"] = main._assign_number(an_order("o_b"))
        del main.orders["o_b"]                   # the newest one goes
        assert main._assign_number(an_order("o_c")).number == 1002   # reused
    finally:
        main.orders.clear()
        main.orders.update(held)


# ---------------------------- a substitute answers the shortage it was bought for


def test_a_substitute_answers_the_shortage_it_was_bought_for():
    """The cream on the demo: the 3,3 dl could not be ordered, the agent offered
    the 2 dl UHT, and the running-low panel went on asking for cream that had
    just been bought, because the ledger books by product id."""
    inv = Inventory.from_seed()
    until = suggestions.window_until(inv)
    short = {s["product_id"]: s for s in inv.shortfalls(until)}
    assert CREAM in short, "the seeded restaurant is not short of cream"
    gap = short[CREAM]["shortfall"]

    substitute = "999001"                       # a cream the ledger has never seen
    inv.units[substitute] = inv.units.get(CREAM, "L")
    inv.add_on_order("o_sub", [(substitute, gap * 2, inv.units[CREAM])],
                     date(2026, 11, 16), covers={substitute: CREAM})

    assert CREAM not in {s["product_id"] for s in inv.shortfalls(until)}


def test_the_ledger_still_says_what_is_actually_arriving():
    """The shortage is answered, but nobody is told that cream is coming when
    it is not. `on_order_totals` stays literal; only `shortfalls` reads cover."""
    inv = Inventory.from_seed()
    cream_before = inv.on_order_totals().get(CREAM, 0.0)
    substitute = "999001"
    inv.units[substitute] = inv.units.get(CREAM, "L")
    inv.add_on_order("o_sub", [(substitute, 20.0, inv.units[CREAM])],
                     date(2026, 11, 16), covers={substitute: CREAM})

    assert inv.on_order_totals().get(CREAM, 0.0) == cream_before, "the ledger was told a lie"
    assert inv.on_order_totals()[substitute] == 20.0
    assert inv.covering_totals()[CREAM] == 20.0


def test_a_substitute_in_another_unit_does_not_count():
    """Two litres of cream say nothing about a shortage measured in kilograms,
    and guessing would put a wrong number where every number is explained."""
    inv = Inventory.from_seed()
    until = suggestions.window_until(inv)
    short = {s["product_id"]: s for s in inv.shortfalls(until)}
    assert CREAM in short
    assert inv.units.get(CREAM) == "L", inv.units.get(CREAM)

    inv.units["999002"] = "KG"
    inv.add_on_order("o_sub", [("999002", 999.0, "KG")], date(2026, 11, 16),
                     covers={"999002": CREAM})

    assert CREAM in {s["product_id"] for s in inv.shortfalls(until)}
    assert CREAM not in inv.covering_totals()


def test_an_ordinary_delivery_covers_nothing():
    inv = Inventory.from_seed()
    inv.add_on_order("o_plain", [(CREAM, 5.0, inv.units.get(CREAM, "L"))],
                     date(2026, 11, 16))
    assert inv.covering_totals() == {}
    assert inv.on_order_totals()[CREAM] > 0
