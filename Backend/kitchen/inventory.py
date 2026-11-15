"""Stock, usage and the one proactive trigger: running low.

The ledger is **append-only movements**, never an overwritten quantity:

    count      a physical count, which resets any drift
    delivery   written when goods arrive, not when they are ordered
    sale       dishes sold, converted through the recipes
    waste

Stock is the sum. Ordering something creates an **on-order** record instead of a
movement, so an amount is never counted in both `stock` and `on_order`; the
delivery movement is written on receipt and closes the record. Without that
split the running-low trigger double counts and fires late or never.

Usage is forecast from **what the kitchen cooked**, not from what it ordered.
Training on past orders would teach us past over-ordering; the ledger gives
usage directly, which is the target that matters.

    expected portions for a dish on a weekday = its average over the last four
    of that weekday

The seed closes Sundays, so a weekday average keeps the zero where it belongs
instead of smearing it across the week.
"""
from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

SEED_DIR = Path(os.environ.get("KITCHEN_SEED_DIR")
                or Path(__file__).resolve().parent.parent / "Data" / "seed")

# How far ahead the trigger looks: to the next delivery, plus a day of cover so
# a kitchen is not ordering at the exact moment it runs out.
BUFFER_DAYS = float(os.environ.get("REORDER_BUFFER_DAYS", "1"))
# Averaged over this many of the same weekday.
WEEKS_OF_HISTORY = int(os.environ.get("FORECAST_WEEKS", "4"))
# Ignore a shortfall below this, in the product's own unit, so a rounding
# difference does not become a purchase order.
MIN_SHORTFALL = float(os.environ.get("MIN_SHORTFALL", "0.5"))


@dataclass
class Movement:
    """One line of the ledger. Append-only: nothing here is ever edited."""

    at: date
    product_id: str
    quantity: float               # positive in, negative out
    unit: str
    kind: str                     # count | delivery | sale | waste
    note: str = ""


@dataclass
class OnOrder:
    """Ordered, not yet received. Closed when the delivery movement is written."""

    product_id: str
    quantity: float
    unit: str
    delivery_date: date
    order_id: str
    # The product this was bought to stand in for, when the one the kitchen
    # wanted could not be ordered. Empty for an ordinary delivery. The ledger
    # stays literal either way: `product_id` is what is actually arriving.
    covers_product_id: str = ""


@dataclass
class Recipe:
    dish_id: str
    name: str
    ingredients: list[dict[str, Any]]


@dataclass
class Inventory:
    """The kitchen's own view of what it has, uses and has coming."""

    recipes: dict[str, Recipe] = field(default_factory=dict)
    movements: list[Movement] = field(default_factory=list)
    on_order: list[OnOrder] = field(default_factory=list)
    units: dict[str, str] = field(default_factory=dict)      # product_id -> L or KG
    as_of: date = field(default_factory=lambda: datetime.now(timezone.utc).date())

    # ------------------------------------------------------------- loading

    @classmethod
    def from_seed(cls, folder: Path | None = None) -> "Inventory":
        """Build from the seeded restaurant: recipes, 28 days of sales, orders.

        The movements are derived rather than copied, so the stock this computes
        is the stock the ledger implies. The seed reconciles exactly, which is
        worth keeping true: `reconcile()` says so.
        """
        folder = folder or SEED_DIR
        inventory = cls()
        inventory.recipes = {
            r["dish_id"]: Recipe(r["dish_id"], r["name"], r["ingredients"])
            for r in _read(folder / "recipes.json", [])
        }
        for recipe in inventory.recipes.values():
            for line in recipe.ingredients:
                inventory.units.setdefault(line["product_id"], line["unit"])

        ledger = _read(folder / "ledger.json", {})
        opening = ledger.get("opening_count") or {}
        opening_date = _as_date(opening.get("date")) or date(2026, 1, 1)
        for row in opening.get("stock") or []:
            inventory.units.setdefault(row["product_id"], row.get("unit", ""))
            inventory.movements.append(Movement(
                opening_date, row["product_id"], float(row["quantity"]),
                row.get("unit", ""), "count", "opening count"))

        for order in _read(folder / "purchase_history.json", []):
            if order.get("status") != "delivered":
                # Still open, so it is on order, not in stock.
                for line in order.get("lines") or []:
                    inventory.on_order.append(OnOrder(
                        line["product_id"], float(line["delivered_amount"]),
                        line.get("unit", ""), _as_date(order.get("delivery_date")),
                        order.get("order_id", "")))
                continue
            when = _as_date(order.get("delivery_date"))
            for line in order.get("lines") or []:
                inventory.units.setdefault(line["product_id"], line.get("unit", ""))
                inventory.movements.append(Movement(
                    when, line["product_id"], float(line["delivered_amount"]),
                    line.get("unit", ""), "delivery", order.get("order_id", "")))

        for sale in _read(folder / "sales_history.json", []):
            inventory.record_service(_as_date(sale["date"]),
                                     {sale["dish_id"]: float(sale["portions"])},
                                     after=opening_date)

        # Anything the seed already lists as on order and we have not seen.
        known = {(o.product_id, o.order_id) for o in inventory.on_order}
        for row in ledger.get("on_order") or []:
            key = (row["product_id"], row.get("order_id", ""))
            if key not in known:
                inventory.on_order.append(OnOrder(
                    row["product_id"], float(row["quantity"]), row.get("unit", ""),
                    _as_date(row.get("delivery_date")), row.get("order_id", "")))

        inventory.as_of = _as_date((ledger.get("as_of") or "")[:10]) or inventory.as_of
        return inventory

    # ------------------------------------------------------------ movements

    def record_service(self, when: date, portions: dict[str, float],
                       after: date | None = None) -> list[Movement]:
        """Turn dishes sold into stock out, through the recipes.

        This is the whole point of asking a chef "what did you cook today?"
        rather than "what did you use?": one number per dish instead of one per
        ingredient.
        """
        written: list[Movement] = []
        for dish_id, count in portions.items():
            if after and when < after:
                continue
            recipe = self.recipes.get(dish_id)
            if recipe is None or not count:
                continue
            for line in recipe.ingredients:
                self.units.setdefault(line["product_id"], line["unit"])
                movement = Movement(
                    when, line["product_id"],
                    -round(float(line["quantity_per_portion"]) * count, 6),
                    line["unit"], "sale", f"{count:g} x {recipe.name}")
                self.movements.append(movement)
                written.append(movement)
        if written and when > self.as_of:
            self.as_of = when
        return written

    def receive(self, order_id: str, when: date | None = None) -> list[Movement]:
        """Goods arrived: write the delivery movements and close the on-order."""
        when = when or self.as_of
        written = []
        for pending in [o for o in self.on_order if o.order_id == order_id]:
            self.movements.append(Movement(when, pending.product_id, pending.quantity,
                                           pending.unit, "delivery", order_id))
            written.append(self.movements[-1])
            self.on_order.remove(pending)
        return written

    def add_on_order(self, order_id: str, lines: Iterable[tuple[str, float, str]],
                     delivery_date: date, covers: dict[str, str] | None = None) -> None:
        """An order was placed. Not stock until it arrives.

        `covers` maps a line's product to the product it was bought instead of,
        for the substitution the agent makes when the one the kitchen wanted
        cannot be ordered that day.
        """
        for product_id, quantity, unit in lines:
            self.on_order.append(OnOrder(product_id, quantity, unit, delivery_date,
                                         order_id, (covers or {}).get(product_id, "")))

    def covering_totals(self) -> dict[str, float]:
        """What is on order under one product to answer another's shortfall.

        Deliberately separate from `on_order_totals`, which stays literal: the
        Inventory page should show what is actually arriving, by the product
        that is actually arriving. This is the other question - which shortage
        it was bought to answer - and only `shortfalls` asks it.

        A substitute in a different unit is not counted. Two litres of cream
        answer a shortage measured in litres; they say nothing about one
        measured in kilograms, and guessing would put a wrong number on the
        screen in a place where every number is meant to be explained.
        """
        totals: dict[str, float] = defaultdict(float)
        for pending in self.on_order:
            covered = pending.covers_product_id
            if not covered:
                continue
            wanted_unit = self.units.get(covered, "")
            if wanted_unit and pending.unit and wanted_unit != pending.unit:
                continue
            totals[covered] += pending.quantity
        return {k: round(v, 6) for k, v in totals.items()}

    # ---------------------------------------------------------------- state

    def stock(self) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for movement in self.movements:
            totals[movement.product_id] += movement.quantity
        return {k: round(v, 6) for k, v in totals.items()}

    def on_order_totals(self) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for pending in self.on_order:
            totals[pending.product_id] += pending.quantity
        return {k: round(v, 6) for k, v in totals.items()}

    def reconcile(self, folder: Path | None = None) -> list[tuple[str, float, float]]:
        """Where our computed stock differs from the seed's own figure.

        Empty means the movements reproduce the seed exactly, which is the
        evidence that the ledger and the seed agree about the same restaurant.
        """
        ledger = _read((folder or SEED_DIR) / "ledger.json", {})
        stated = {row["product_id"]: float(row["quantity"]) for row in ledger.get("stock") or []}
        ours = self.stock()
        return [(p, round(stated[p], 3), round(ours.get(p, 0.0), 3))
                for p in sorted(stated) if abs(stated[p] - ours.get(p, 0.0)) > 0.051]

    # ------------------------------------------------------------- forecast

    def portions_by_weekday(self) -> dict[tuple[str, int], float]:
        """Average portions per dish per weekday, over the recent history."""
        seen: dict[tuple[str, int], list[float]] = defaultdict(list)
        for movement in self.movements:
            if movement.kind != "sale" or " x " not in movement.note:
                continue
        # Portions are not stored directly, so they are read back from the notes
        # once per dish and day rather than per ingredient.
        counted: set[tuple[date, str]] = set()
        for movement in self.movements:
            if movement.kind != "sale" or " x " not in movement.note:
                continue
            count_text, name = movement.note.split(" x ", 1)
            dish = next((d for d, r in self.recipes.items() if r.name == name), None)
            if dish is None or (movement.at, dish) in counted:
                continue
            counted.add((movement.at, dish))
            seen[(dish, movement.at.weekday())].append(float(count_text))
        return {key: round(sum(values[-WEEKS_OF_HISTORY:]) / len(values[-WEEKS_OF_HISTORY:]), 3)
                for key, values in seen.items() if values}

    def expected_usage(self, from_day: date, to_day: date) -> dict[str, float]:
        """How much of each product the menu will use between two dates."""
        averages = self.portions_by_weekday()
        usage: dict[str, float] = defaultdict(float)
        day = from_day
        while day <= to_day:
            for dish_id, recipe in self.recipes.items():
                portions = averages.get((dish_id, day.weekday()), 0.0)
                if not portions:
                    continue
                for line in recipe.ingredients:
                    usage[line["product_id"]] += float(line["quantity_per_portion"]) * portions
            day += timedelta(days=1)
        return {k: round(v, 6) for k, v in usage.items()}

    def shortfalls(self, until: date, as_of: date | None = None) -> list[dict[str, Any]]:
        """What the kitchen will run out of before `until`, and by how much.

            shortfall = expected usage + buffer - stock - on order

        Subtracting what is already on order is what stops the agent proposing
        the same order every morning.
        """
        as_of = as_of or self.as_of
        start = as_of + timedelta(days=1)
        usage = self.expected_usage(start, until)
        buffer_usage = self.expected_usage(until + timedelta(days=1),
                                           until + timedelta(days=int(BUFFER_DAYS)))
        stock, on_order = self.stock(), self.on_order_totals()
        # A substitute bought to cover a shortage answers that shortage, or the
        # kitchen is told to order cream it has already bought.
        covering = self.covering_totals()

        out = []
        for product_id, needed in usage.items():
            # Round the parts first and take the shortfall from those, so the
            # figures in the reason add up to the figure we act on. Rounding
            # last leaves a cent-sized difference, and "every number explained"
            # has to survive someone adding them up.
            need = round(needed, 3)
            buffer_needed = round(buffer_usage.get(product_id, 0.0), 3)
            have = round(stock.get(product_id, 0.0), 3)
            coming = round(on_order.get(product_id, 0.0)
                           + covering.get(product_id, 0.0), 3)
            short = round(need + buffer_needed - have - coming, 3)
            if short < MIN_SHORTFALL:
                continue
            out.append({
                "product_id": product_id,
                "unit": self.units.get(product_id, ""),
                "needed": need,
                "buffer": buffer_needed,
                "stock": have,
                "on_order": coming,
                "shortfall": short,
                "runs_out_on": self._runs_out_on(product_id, have + coming, start, until),
                "dishes": sorted({r.name for r in self.recipes.values()
                                  if any(i["product_id"] == product_id for i in r.ingredients)}),
            })
        # Soonest to run out first; that is the order a chef cares about.
        return sorted(out, key=lambda row: (row["runs_out_on"] or until, -row["shortfall"]))

    def _runs_out_on(self, product_id: str, available: float, start: date,
                     until: date) -> date | None:
        """The first day the menu needs more than there is."""
        day, left = start, available
        while day <= until + timedelta(days=int(BUFFER_DAYS)):
            used = self.expected_usage(day, day).get(product_id, 0.0)
            left -= used
            if left < 0:
                return day
            day += timedelta(days=1)
        return None


def _read(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:        # noqa: BLE001
        print(f"[kitchen] could not read {path}: {exc}")
        return default


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
