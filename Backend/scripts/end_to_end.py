"""One order, all the way through, without the network.

    python scripts/end_to_end.py

Chat, confirm, approve, then look at what the kitchen believes afterwards -
driven through `kitchen.main`'s own endpoint functions, with the real quote,
signing and order code behind a stub that skips only the HTTP hop. No Aiven, no
Valio, no browser.

It exists because the parts each pass their own tests while the wiring between
them breaks: "the inventory does not change when I order something" is invisible
to a test of the ledger and invisible to a test of ordering, and shows up only
when one calls the other.

Deliberately not a pytest file. Importing `tests/test_ordering.py` swaps the
signing keys module-wide, which is fine inside one process devoted to this and
quietly breaks two dozen other tests when pytest collects them together. So it
runs on its own, and `python -m pytest tests/` stays the thing that must be
green before a deploy.

Exit code 0 means the whole path works on this machine.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("TTA_SKIP_DOTENV", "1")

from kitchen.inventory import Inventory                         # noqa: E402
from kitchen.need import parse_with_rules                       # noqa: E402
from test_ordering import (                                     # noqa: E402
    AIMO_KEYS, AIMO_SIGNER, GATEWAY_URL, SUMMARIES, StubGateway, keys,
)

MILK = "464306"
SATURDAY = date(2026, 11, 14)


class LiveClockGateway(StubGateway):
    """StubGateway, signing at the real clock.

    The endpoints take no `now` - in service there is one clock - so a quote
    signed at the recorded moment would be hours expired before the kitchen
    verified it. Availability still comes from the recorded moment, so the
    stock picture is fixed and this does not change answer by time of day.
    """

    @staticmethod
    def _real_now() -> datetime:
        return datetime.now(timezone.utc)

    def create_quote(self, agent_id, requested_date, lines):
        from gateway import quotes as gateway_quotes

        quantities = {l.product_id: l.quantity_sales_units for l in lines}
        return gateway_quotes.build_quote(
            issuer=GATEWAY_URL, customer_id="123", agent_id=agent_id,
            requested_date=requested_date, summaries=SUMMARIES,
            results=self._results(requested_date, quantities),
            wanted=quantities, signer=AIMO_SIGNER, now=self._real_now())

    def place_order(self, request):
        from gateway import orders as gateway_orders
        from gateway import quotes as gateway_quotes
        from kitchen import ordering
        from kitchen.gateway_client import GatewayRefused
        from shared.signing import Verifier

        self.placed.append(request)

        def recheck(requested_date, items):
            quantities = {i.product_id: i.quantity_sales_units for i in items}
            return self._results(requested_date, quantities)

        try:
            return gateway_orders.place_order(
                request=request, agent_id=ordering.AGENT_ID, gateway_url=GATEWAY_URL,
                own_verifier=Verifier([AIMO_KEYS.public_jwk]),
                kitchen_verifier=Verifier([keys.kitchen_keypair().public_jwk]),
                recheck_delivery=recheck, registry=self.registry, store=self.store,
                signer=AIMO_SIGNER, new_order_id=gateway_quotes.new_order_id,
                now=self._real_now())
        except gateway_orders.OrderRefused as exc:
            raise GatewayRefused(exc.rejection.reason, exc.rejection.detail,
                                 exc.rejection.security_event,
                                 exc.rejection.model_dump(mode="json")) from exc


def fresh_kitchen():
    """kitchen.main with a stub gateway and a ledger from the seed."""
    import kitchen.main as main
    from kitchen import storage

    main.gateway = LiveClockGateway()
    main.inventory = Inventory.from_seed()
    main.baskets.clear()
    main.orders.clear()
    main.kitchen.mandate = None
    main.kitchen.stopped = False
    # Keep the state file isolated and writable on Windows as well as Linux.
    # The TemporaryDirectory object stays attached to the app module until the
    # process exits, then removes the file and directory automatically.
    temp_dir = tempfile.TemporaryDirectory(prefix="tta-end-to-end-")
    storage.STATE_FILE = Path(temp_dir.name) / "state.json"
    main._end_to_end_temp_dir = temp_dir
    return main


def a_draft(main, sentence: str):
    from kitchen import agent

    need = parse_with_rules(sentence, today=SATURDAY)
    state, reply, _ = agent.add_need(None, need, main.gateway)
    main.baskets[state.basket.basket_id] = state
    return state, reply


def order_it(main, state, role="owner"):
    """Confirm and approve through the endpoints, not around them."""
    from shared.schemas import ApproveRequest

    main.confirm_basket(state.basket.basket_id, x_kitchen_role=role)
    return main.approve_basket(state.basket.basket_id,
                               ApproveRequest(approver_role=role))


def main() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
        if not ok:
            failures.append(name)

    print("One order, end to end, offline.\n")
    kitchen = fresh_kitchen()

    print("Chat:")
    state, reply = a_draft(kitchen, "40 L lactose-free milk by Monday")
    check("the agent built a basket", bool(state.basket.lines), reply[:60])
    if not state.basket.lines:
        return 1
    wanted = sum(l.delivered_amount for l in state.basket.lines if l.product_id == MILK)
    print(f"        {len(state.basket.lines)} line(s), {wanted:g} of {MILK}, "
          f"net {state.basket.net_total}")

    before_stock = kitchen.inventory.stock().get(MILK, 0.0)
    before_order = kitchen.inventory.on_order_totals().get(MILK, 0.0)
    from kitchen import suggestions

    short_before = {s["product_id"] for s in
                    kitchen.inventory.shortfalls(suggestions.window_until(kitchen.inventory))}

    print("\nConfirm and approve:")
    result = order_it(kitchen, state)
    check("Aimo accepted the order", result.order is not None, result.reply[:60])
    if result.order is None:
        return 1
    check("it has a short number", result.order.number == 1001, f"#{result.order.number}")
    check("and keeps Aimo's own id", result.order.order_id.startswith("o_"),
          result.order.order_id)

    print("\nThe kitchen's own ledger:")
    after_order = kitchen.inventory.on_order_totals().get(MILK, 0.0)
    after_stock = kitchen.inventory.stock().get(MILK, 0.0)
    check("the goods are on order", abs((after_order - before_order) - wanted) < 1e-6,
          f"{before_order:g} -> {after_order:g}")
    check("and not counted as stock", abs(after_stock - before_stock) < 1e-6,
          f"{after_stock:g}")

    until = suggestions.window_until(kitchen.inventory)
    short_after = {s["product_id"] for s in kitchen.inventory.shortfalls(until)}
    if MILK in short_before:
        check("it stopped being suggested", MILK not in short_after)
    else:
        print("  --    the seeded restaurant was not short of this milk, nothing to check")

    print("\nWhat the Inventory page would show:")
    rows = {r.product_id: r for r in kitchen._stock_rows()}
    check("the endpoint reports it too", MILK in rows
          and abs(rows[MILK].on_order - after_order) < 1e-6,
          f"on order {rows[MILK].on_order:g}" if MILK in rows else "missing")

    print("\nAfter a restart:")
    running = (kitchen.inventory.stock(), kitchen.inventory.on_order_totals())
    kitchen.inventory = Inventory.from_seed()
    for record in kitchen.orders.values():
        kitchen._book_on_order(record)
    rebuilt = (kitchen.inventory.stock(), kitchen.inventory.on_order_totals())
    check("the ledger rebuilds to the same figures", running == rebuilt)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print("The whole path works on this machine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
