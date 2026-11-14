"""Synthetic seed data for the demo restaurant (docs/ARCHITECTURE.md section 11).

    cd Backend
    python scripts/generate_seed.py            # writes Data/seed/*.json

Deterministic (fixed random seed). Every product id is real and every quantity
goes through shared/units.convert, so orders are whole sales units and respect
allowedLotSize, exactly as the agent would compute them.

Story the files tell:
  * 28 days of lunch service, closed Sundays; mushroom soup grows ~7% a week.
  * Deliveries Monday (big weekly order, covers Mon-Fri) and Thursday (top-up).
  * The Monday 16 Nov order is already placed (status "open"), but its cream line
    is 3 KI against the usual 6. Cream runs out on Thursday, so the running-low
    trigger fires for cream only, and a usual-order check would flag the line.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.units import convert  # noqa: E402

CATALOGUE = ROOT / "Data" / "valio_aimo_junction26_product_data.json"
OUT = ROOT / "Data" / "seed"

CREAM = "461490"  # Valio Keittiön whipping cream 3,3 dl lactose free, KI of 10
P = {  # product id -> the size unit recipes use (matches the product's own size unit)
    CREAM: "L",
    "464306": "L",   # Valio Arki Eila semi skimmed milk 1 l lactose free
    "463254": "L",   # Millac Cooking 15 1 L lactose free
    "462421": "KG",  # Valio Eila butter 200 g lactose free
    "475214": "KG",  # SPL Portion potato peeled 10 kg
    "470572": "KG",  # SallaCarte Onion diced 1 kg
    "470139": "KG",  # SallaCarte Leek ring 1 kg
    "460437": "KG",  # Dill 100 g
    "462150": "KG",  # Salmon fillet C-cut, sold per kg (lot size 10)
    "463544": "KG",  # Lagerblad traditional meatball 30 g / 5 kg frozen
    "468016": "KG",  # SallaCarte Champignon slice 1,5 kg
    "462452": "KG",  # Saarioinen chicken fillet strips sous vide 2 kg
    "466511": "KG",  # Zini tagliatelle 3 kg frozen
    "463883": "KG",  # Kieku organic egg M 5,2 kg
    "460791": "KG",  # Myllyn Paras wheat flour 2 kg
    "460325": "KG",  # Dansukker caster sugar 1 kg
    "462171": "KG",  # Dronningholm strawberry jam 1 kg
}


def ing(pid: str, q: float) -> dict:
    return {"product_id": pid, "quantity_per_portion": q, "unit": P[pid]}


RECIPES = [
    {"dish_id": "d1", "name": "Creamy salmon soup (lohikeitto)", "ingredients": [
        ing("462150", 0.10), ing("475214", 0.12), ing("470572", 0.02), ing("470139", 0.02),
        ing(CREAM, 0.05), ing("464306", 0.08), ing("462421", 0.005), ing("460437", 0.002)]},
    {"dish_id": "d2", "name": "Meatballs in cream sauce with mashed potatoes", "ingredients": [
        ing("463544", 0.15), ing("475214", 0.20), ing("464306", 0.05), ing("462421", 0.010),
        ing("463254", 0.04), ing(CREAM, 0.02)]},
    {"dish_id": "d3", "name": "Creamy chicken tagliatelle", "ingredients": [
        ing("462452", 0.12), ing("466511", 0.15), ing("463254", 0.06), ing("468016", 0.04),
        ing("470572", 0.02)]},
    {"dish_id": "d4", "name": "Creamy mushroom soup", "ingredients": [
        ing("468016", 0.10), ing("470572", 0.03), ing(CREAM, 0.06), ing("464306", 0.10),
        ing("462421", 0.008)]},
    {"dish_id": "d5", "name": "Oven pancake with strawberry jam and whipped cream (pannukakku)",
     "ingredients": [
        ing("464306", 0.12), ing("463883", 0.05), ing("460791", 0.04), ing("460325", 0.01),
        ing("462421", 0.005), ing("462171", 0.03), ing(CREAM, 0.03)]},
]

START = date(2026, 10, 18)              # a Sunday
DAYS = [START + timedelta(days=i) for i in range(28)]   # .. Saturday 14 Nov
TODAY = date(2026, 11, 15)              # demo day, Sunday
WEEKDAY_FACTOR = {0: 0.8, 1: 0.9, 2: 1.0, 3: 1.05, 4: 1.2, 5: 1.3, 6: 0.0}
BASE_PORTIONS = {"d1": 28, "d2": 32, "d3": 26, "d4": 14, "d5": 22}
GROWING = "d4"
BUFFER = 0.15          # the chef's habit; the kitchen code owns the real safety buffer


def sales_history(rng: random.Random) -> list[dict]:
    rows = []
    for i, day in enumerate(DAYS):
        f = WEEKDAY_FACTOR[day.weekday()]
        for dish, b in BASE_PORTIONS.items():
            if dish == GROWING:
                b *= 1.07 ** (i // 7)
            n = 0 if f == 0 else max(0, round(b * f * rng.uniform(0.92, 1.08)))
            rows.append({"date": day.isoformat(), "dish_id": dish, "portions": n})
    return rows


def per_dish_qty(pid: str) -> dict[str, float]:
    return {r["dish_id"]: i["quantity_per_portion"]
            for r in RECIPES for i in r["ingredients"] if i["product_id"] == pid}


def main() -> None:
    catalogue = {p["id"]: p for p in json.loads(CATALOGUE.read_text("utf-8"))}
    for pid in P:
        assert not catalogue[pid]["synkkaData"].get("variableUnit"), pid

    rng = random.Random(26)
    sales = sales_history(rng)
    portions = {(s["date"], s["dish_id"]): s["portions"] for s in sales}

    def actual_use(pid: str, day: date) -> float:
        return sum(portions.get((day.isoformat(), d), 0) * q for d, q in per_dish_qty(pid).items())

    def forecast_use(pid: str, day: date, as_of: date) -> float:
        # What the chef expects: the same weekday over the last two weeks seen,
        # or the planned base in the first fortnight.
        seen = [as_of - timedelta(days=k) for k in range(1, 15) if (as_of - timedelta(days=k)) >= START]
        same = [d for d in seen if d.weekday() == day.weekday()]
        if len(same) < 2:
            f = WEEKDAY_FACTOR[day.weekday()]
            return sum(BASE_PORTIONS[d] * f * q for d, q in per_dish_qty(pid).items())
        return sum(actual_use(pid, d) for d in same) / len(same)

    def order_up_to(pid: str, stock: float, cover: list[date], as_of: date) -> dict | None:
        need = sum(forecast_use(pid, d, as_of) for d in cover) * (1 + BUFFER) - stock
        if need <= 0:
            return None
        c = convert(catalogue[pid], need, P[pid])
        assert c.ok, (pid, c.reason)
        return {"product_id": pid, "quantity_sales_units": c.quantity_sales_units,
                "sales_unit": c.sales_unit, "delivered_amount": round(c.delivered_amount, 3), "unit": P[pid]}

    # Opening count, morning of Monday 19 Oct: half of the first Mon-Wed.
    stock = {pid: round(0.5 * sum(forecast_use(pid, START + timedelta(days=k), START)
                                  for k in (1, 2, 3)), 2) for pid in P}
    opening = dict(stock)
    orders, n = [], 0

    def place(ordered_on: date, delivery: date, cover: list[date], cream_override=None,
              project_from: date | None = None) -> dict:
        nonlocal n
        n += 1
        lines = []
        for pid in P:
            # Placed in the evening, before today's usage is booked in this loop.
            projected = stock[pid] - sum(actual_use(pid, d) for d in DAYS if (project_from or ordered_on) <= d < delivery)
            line = order_up_to(pid, projected, cover, ordered_on)
            if pid == CREAM and cream_override is not None:
                line = cream_override(line)
            if line:
                lines.append(line)
        return {"order_id": f"po_{n:03d}", "ordered_on": ordered_on.isoformat(),
                "delivery_date": delivery.isoformat(), "status": "delivered", "lines": lines,
                "synthetic": True}

    # Simulate the 28 days: order the evening before delivery, deliver in the morning.
    for day in DAYS:
        wd = day.weekday()
        if wd == 6 and day + timedelta(days=1) <= DAYS[-1]:         # Sunday -> Monday weekly order
            mon = day + timedelta(days=1)
            orders.append(place(day, mon, [mon + timedelta(days=k) for k in range(5)]))
        if wd == 2:                                                  # Wednesday -> Thursday top-up
            thu = day + timedelta(days=1)
            orders.append(place(day, thu, [thu + timedelta(days=k) for k in range(3)]))
        for o in orders:
            if o["delivery_date"] == day.isoformat():
                for line in o["lines"]:
                    stock[line["product_id"]] += line["delivered_amount"]
        for pid in P:
            stock[pid] = stock[pid] - actual_use(pid, day)
            assert stock[pid] > -1e-6, f"{pid} ran out on {day}: the history would show a stockout"

    # Thursday 12 Nov: Aimo was short on cream, only part of the line arrived.
    # Monday 16 Nov, placed Friday 13: the usual weekly order, still open.
    mon16 = [TODAY + timedelta(days=1 + k) for k in range(5)]

    def build_open(cream_ki: int) -> dict:
        def fix(line):
            c = convert(catalogue[CREAM], cream_ki * 3.3, "L")
            return {"product_id": CREAM, "quantity_sales_units": c.quantity_sales_units,
                    "sales_unit": c.sales_unit, "delivered_amount": round(c.delivered_amount, 3), "unit": "L"}
        # Stock is already as of Saturday night, so project from today, not from Friday.
        o = place(date(2026, 11, 13), TODAY + timedelta(days=1), mon16, fix, project_from=TODAY)
        o["status"] = "open"
        return o

    # The open order's cream line was entered short. Pick the largest quantity that
    # still makes cream run out on Thursday (3 KI against a usual 6).
    def cream_forecast(day):
        xs = [actual_use(CREAM, d) for d in DAYS if d.weekday() == day.weekday()]
        return sum(xs) / len(xs)
    window_use = [cream_forecast(d) for d in mon16]
    open_ki = None
    for ki in range(6, 0, -1):
        left, runs = stock[CREAM] + ki * 3.3, None
        for d, u in zip(mon16, window_use):
            left -= u
            if left < 0:
                runs = d.strftime("%A")
                break
        if runs == "Thursday":
            open_ki = ki
            break
    assert open_ki, "no cream quantity makes cream run out on Thursday; adjust the recipes"
    open_order = build_open(open_ki)
    orders.append(open_order)

    ledger = {
        "as_of": "2026-11-14T22:00:00+02:00",
        "opening_count": {"date": "2026-10-19", "stock": [
            {"product_id": pid, "quantity": round(q, 3), "unit": P[pid]} for pid, q in opening.items()]},
        "stock": [{"product_id": pid, "quantity": round(q, 3), "unit": P[pid]} for pid, q in stock.items()],
        "on_order": [{"product_id": l["product_id"], "quantity": l["delivered_amount"], "unit": l["unit"],
                      "delivery_date": open_order["delivery_date"], "order_id": open_order["order_id"]}
                     for l in open_order["lines"]],
    }

    OUT.mkdir(parents=True, exist_ok=True)
    for name, obj in [("recipes.json", RECIPES), ("sales_history.json", sales),
                      ("ledger.json", ledger), ("purchase_history.json", orders)]:
        (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", "utf-8")

    # Self-check: only cream is short over Mon-Fri, and by how much.
    on_order = defaultdict(float)
    for l in ledger["on_order"]:
        on_order[l["product_id"]] += l["quantity"]
    for pid in P:
        use = sum(sum(actual_use(pid, d) for d in DAYS if d.weekday() == w.weekday()) / 4 for w in mon16)
        short = use * (1 + BUFFER) - stock[pid] - on_order[pid]
        if pid == CREAM:
            ki = math.ceil(short / 3.3)
            print(f"cream: {stock[pid]:.2f} L in stock + {on_order[pid]:.1f} L on order, "
                  f"Mon-Fri needs {use:.2f} L (+15% = {use * 1.15:.2f}) -> short {short:.2f} L "
                  f"-> {ki} KI = {ki * 3.3:.1f} L")
            assert short > 0
        else:
            assert short <= 0, f"{pid} would also trigger running low ({short:.2f})"
    print(f"{len(orders)} orders ({sum(len(o['lines']) for o in orders)} lines) written to {OUT}")


if __name__ == "__main__":
    main()
