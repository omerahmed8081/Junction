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

# Which kitchen's row this is. A second kitchen (the hospital) is a second
# service with its own id, so the two never read each other's state.
STATE_ID = os.environ.get("KITCHEN_STATE_ID") or os.environ.get("AGENT_ID", "default")

# Set once the database is open. With it, the state is one row in
# `kitchen.state`; without it, the file beside the code, which is what tests and
# a developer with no DATABASE_URL use.
#
# It used to be the file always, which meant orders lived in three places - here,
# the gateway's memory and the audit log - and they disagreed after any reset.
_pool: Any = None


def set_pool(pool: Any) -> None:
    global _pool
    _pool = pool


def using_database() -> bool:
    return _pool is not None


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
    document = json.dumps(payload, default=_dump)
    if _pool is not None:
        try:
            with _pool.connection(timeout=5.0) as conn:
                conn.execute(
                    "INSERT INTO kitchen.state (id, document, updated_at)"
                    " VALUES (%s, %s, now())"
                    " ON CONFLICT (id) DO UPDATE SET document = EXCLUDED.document,"
                    "                                updated_at = now()",
                    (STATE_ID, document))
            return
        except Exception as exc:        # noqa: BLE001
            # Fall through to the file rather than lose a basket mid-demo. The
            # next save goes to the database again if it comes back.
            print(f"[kitchen] state not written to the database, writing the file instead: "
                  f"{type(exc).__name__}: {exc}")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=1, default=_dump), encoding="utf-8")
    temporary.replace(path)


def load(path: Path | None = None) -> tuple[dict[str, BasketState], dict[str, OrderRecord], Mandate | None, list[dict]]:
    """Read it back. Nothing stored, or stored unreadably, starts empty.

    The database first, then the file: a kitchen that has just been pointed at
    Postgres still finds the state it had on disk, and saves it back to the
    row from then on.
    """
    path = path or STATE_FILE
    payload = None
    if _pool is not None:
        try:
            with _pool.connection(timeout=5.0) as conn:
                row = conn.execute("SELECT document FROM kitchen.state WHERE id = %s",
                                   (STATE_ID,)).fetchone()
            if row and row[0]:
                payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        except Exception as exc:        # noqa: BLE001
            print(f"[kitchen] could not read the state row: {type(exc).__name__}: {exc}")

    if payload is None:
        if not path.exists():
            return {}, {}, None, []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if _pool is not None:
                print(f"[kitchen] found state in {path.name}; it moves to the database "
                      "on the next save")
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
    """For the labelled demo reset. Clears both, so neither can come back."""
    path = path or STATE_FILE
    if _pool is not None:
        try:
            with _pool.connection(timeout=5.0) as conn:
                conn.execute("DELETE FROM kitchen.state WHERE id = %s", (STATE_ID,))
        except Exception as exc:        # noqa: BLE001
            print(f"[kitchen] could not clear the state row: {exc}")
    if path.exists():
        path.unlink()
