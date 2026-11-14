"""The proactive side: turning a shortfall into a basket a chef can approve.

The agent is proactive about **drafting**, never about committing. A suggestion
is a draft basket with the arithmetic shown, and a person still confirms and
approves it, exactly as if they had typed the request themselves.

Only one trigger is built: **running low**. It is the one that needs the kitchen's
own data, so it is the one a wholesaler's catalogue cannot produce on its own.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

from kitchen import agent
from kitchen.gateway_client import GatewayClient
from kitchen.inventory import Inventory
from shared.schemas import AgentStep, Suggestion

# How far ahead to look. Short enough that the numbers mean something, long
# enough to catch what a delivery two days out will not cover.
HORIZON_DAYS = int(os.environ.get("REORDER_HORIZON_DAYS", "6"))


def window_until(inventory: Inventory, as_of: date | None = None) -> date:
    return (as_of or inventory.as_of) + timedelta(days=HORIZON_DAYS)


def shortfall_reason(row: dict[str, Any]) -> str:
    """The arithmetic in a sentence, because a number without it is not a reason."""
    unit = row["unit"]
    parts = [f"the menu needs {row['needed']:g} {unit}"]
    if row["buffer"]:
        parts.append(f"plus {row['buffer']:g} {unit} of cover")
    parts.append(f"{row['stock']:g} {unit} in stock")
    if row["on_order"]:
        parts.append(f"{row['on_order']:g} {unit} already on order")
    when = f" Runs out {row['runs_out_on']}." if row.get("runs_out_on") else ""
    dishes = f" Used in {', '.join(row['dishes'][:2])}." if row.get("dishes") else ""
    return (f"Short {row['shortfall']:g} {unit}: " + ", ".join(parts) + "." + when + dishes)


def find(inventory: Inventory, as_of: date | None = None) -> list[Suggestion]:
    """What to flag, cheapest possible: the ledger only, no calls to Aimo."""
    until = window_until(inventory, as_of)
    return [
        Suggestion(
            product_id=row["product_id"], unit=row["unit"], shortfall=row["shortfall"],
            needed=row["needed"], buffer=row["buffer"], stock=row["stock"],
            on_order=row["on_order"], runs_out_on=row["runs_out_on"],
            dishes=row["dishes"], reason=shortfall_reason(row),
        )
        for row in inventory.shortfalls(until=until, as_of=as_of)
    ]


def draft(suggestion: Suggestion, inventory: Inventory, gateway: GatewayClient,
          baskets: dict[str, Any], delivery_date: date | None = None,
          as_of: date | None = None) -> tuple[Any, str, list[AgentStep]]:
    """Build a draft basket for one suggestion, through the normal agent path.

    The delivery date is the day before it runs out where that is still in the
    future, so the basket is for the order that is actually needed rather than
    for the soonest possible delivery.
    """
    today = as_of or inventory.as_of
    if delivery_date is None:
        runs_out = suggestion.runs_out_on
        wanted = (runs_out - timedelta(days=1)) if runs_out else window_until(inventory, today)
        delivery_date = max(wanted, today + timedelta(days=1))

    state, reply, steps = agent.add_reorder(
        None, suggestion.product_id, suggestion.shortfall, suggestion.unit,
        delivery_date, gateway, reason=suggestion.reason, source="suggestion:low_stock",
    )
    baskets[state.basket.basket_id] = state
    suggestion.basket_id = state.basket.basket_id
    if state.basket.lines:
        suggestion.name = state.basket.lines[0].name
    return state, reply, steps
