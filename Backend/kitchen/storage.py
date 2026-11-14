"""Keeping baskets, orders and the mandate across a restart.

A JSON file, not a database table. The reason is honest: the kitchen's own
schema is the right home and is on the roadmap, but restarting the service
mid-demo and losing a basket on stage is the risk worth removing now, and this
removes it in a way nobody has to debug at two in the morning.

What is saved: each basket and the need behind each line, the orders placed, and
the mandate in force. What is not: the candidate products a basket was ranked
from, because they are a search result rather than state. A swap after a restart
searches again, which `agent.apply_edits` handles.

Writes go to a temporary file and are then moved into place, so a crash halfway
through leaves the previous file intact rather than half a file.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from kitchen.agent import BasketState
from kitchen.ordering import Mandate
from shared.schemas import Basket, MandatePayload, OrderRecord
from kitchen.need import ParsedNeed

STATE_FILE = Path(os.environ.get("KITCHEN_STATE_FILE")
                  or Path(__file__).resolve().parent.parent / ".state" / "kitchen.json")


def _dump(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def save(baskets: dict[str, BasketState], orders: dict[str, OrderRecord],
         mandate: Mandate | None, path: Path | None = None,
         services: list[dict] | None = None) -> None:
    """Write everything out. Called after any change; cheap at this size."""
    path = path or STATE_FILE
    payload = {
        "version": 1,
        "saved_at": datetime.now().astimezone().isoformat(),
        "baskets": {
            basket_id: {
                "basket": state.basket.model_dump(mode="json"),
                "needs": {pid: need.model_dump(mode="json") for pid, need in state.needs.items()},
            }
            for basket_id, state in baskets.items()
        },
        "orders": {oid: record.model_dump(mode="json") for oid, record in orders.items()},
        "mandate": ({"jws": mandate.jws, "payload": mandate.payload.model_dump(mode="json")}
                    if mandate else None),
        # Services are kept as "what was cooked", not as the movements they
        # produced, so replaying them rebuilds the ledger from the seed.
        "services": list(services or []),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=1, default=_dump), encoding="utf-8")
    temporary.replace(path)


def load(path: Path | None = None) -> tuple[dict[str, BasketState], dict[str, OrderRecord], Mandate | None, list[dict]]:
    """Read it back. A missing or unreadable file starts empty rather than crashing."""
    path = path or STATE_FILE
    if not path.exists():
        return {}, {}, None, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:        # noqa: BLE001
        print(f"[kitchen] ignoring unreadable state file {path}: {exc}")
        return {}, {}, None, []

    baskets: dict[str, BasketState] = {}
    for basket_id, stored in (payload.get("baskets") or {}).items():
        try:
            state = BasketState(Basket(**stored["basket"]))
            state.needs = {pid: ParsedNeed(**n) for pid, n in (stored.get("needs") or {}).items()}
            baskets[basket_id] = state
        except Exception as exc:        # noqa: BLE001
            # One unreadable basket must not cost us the others.
            print(f"[kitchen] skipping basket {basket_id}: {exc}")

    orders: dict[str, OrderRecord] = {}
    for order_id, stored in (payload.get("orders") or {}).items():
        try:
            orders[order_id] = OrderRecord(**stored)
        except Exception as exc:        # noqa: BLE001
            print(f"[kitchen] skipping order {order_id}: {exc}")

    mandate = None
    stored_mandate = payload.get("mandate")
    if stored_mandate:
        try:
            mandate = Mandate(jws=stored_mandate["jws"],
                              payload=MandatePayload(**stored_mandate["payload"]))
        except Exception as exc:        # noqa: BLE001
            print(f"[kitchen] ignoring stored mandate: {exc}")
    services = [s for s in (payload.get("services") or [])
                if isinstance(s, dict) and s.get("date") and isinstance(s.get("portions"), dict)]
    return baskets, orders, mandate, services


def clear(path: Path | None = None) -> None:
    """For the labelled demo reset."""
    path = path or STATE_FILE
    if path.exists():
        path.unlink()
