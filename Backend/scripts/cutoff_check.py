"""Does a product's order cut-off move when you ask for a later delivery day?

    python scripts/cutoff_check.py --gateway http://YOUR_SERVER_IP/gateway
    python scripts/cutoff_check.py --product 461490 --days 7

Everything the kitchen can and cannot order turns on one comparison the gateway
makes: `closingTime < now`. What nobody here knows is whether Valio returns a
*different* closingTime when the requested delivery date changes.

  * If it moves, asking for a later day is a real answer, and the agent's
    look-ahead (kitchen/agent.py, _first_day_that_works) already does it.
  * If it is fixed per product, a later day changes nothing and the only honest
    way to demo a product whose window has closed is to judge the cut-off at a
    stated earlier instant.

This asks the question directly: the same product, one day at a time, printing
what Aimo says each time. It only reads.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv                                  # noqa: E402

load_dotenv(ROOT / ".env")

import httpx                                                    # noqa: E402

# The seeded restaurant's cream: the one the Running low panel recommends and
# the one that could not be ordered.
CREAM = "461490"
MILK = "464306"


def when(value) -> str:
    if not value:
        return "-"
    text = str(value)
    return text.replace("T", " ")[:16] if len(text) > 10 else text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", default=os.environ.get("GATEWAY_BASE_URL", "http://localhost:8001"))
    ap.add_argument("--key", default=os.environ.get("AGENT_API_KEY", "demo-kitchen-key"))
    ap.add_argument("--product", default=CREAM, help=f"product id (default {CREAM}, the cream)")
    ap.add_argument("--also", default=MILK, help=f"a second product to compare (default {MILK}, the milk)")
    ap.add_argument("--days", type=int, default=5, help="how many days ahead to try")
    ap.add_argument("--quantity", type=float, default=1.0)
    args = ap.parse_args()

    products = [p for p in (args.product, args.also) if p]
    # The gateway's own routes already begin with /gateway, and behind Caddy
    # the public address does too. Both spellings are natural to type, so one
    # is turned into the other rather than 404ing on
    # .../gateway/gateway/check_delivery.
    base = args.gateway.rstrip("/")
    if base.endswith("/gateway"):
        base = base[: -len("/gateway")]
    client = httpx.Client(base_url=base, timeout=60.0,
                          headers={"X-API-Key": args.key})
    now = datetime.now(timezone.utc)
    print(f"Asking {base}/gateway/check_delivery about {', '.join(products)}")
    print(f"Now is {when(now.isoformat())} UTC. A cut-off before that is past.\n")

    seen: dict[str, set[str]] = {p: set() for p in products}
    today = date.today()
    header = f"{'delivery day':<14} {'product':<9} {'can fulfil':<11} {'cut-off':<17} {'past?':<6} earliest"
    print(header)
    print("-" * len(header))

    for offset in range(1, args.days + 1):
        day = today + timedelta(days=offset)
        try:
            response = client.post("/gateway/check_delivery", json={
                "requested_date": day.isoformat(),
                "items": [{"product_id": p, "quantity_sales_units": args.quantity}
                          for p in products]})
            response.raise_for_status()
            results = response.json().get("results", [])
        except Exception as exc:                                # noqa: BLE001
            print(f"{day}  FAILED {type(exc).__name__}: {exc}")
            return 1

        for row in results:
            d = row.get("delivery") or {}
            pid = row.get("product_id", "?")
            seen.setdefault(pid, set()).add(str(d.get("closing_time")))
            print(f"{day.strftime('%a %d %b'):<14} {pid:<9} "
                  f"{str(d.get('can_fulfil')):<11} {when(d.get('closing_time')):<17} "
                  f"{('yes' if d.get('past_closing_time') else 'no'):<6} "
                  f"{when(d.get('delivery_date'))}")

    print()
    for pid, times in seen.items():
        times = {t for t in times if t and t != "None"}
        if not times:
            print(f"{pid}: Aimo returned no cut-off at all.")
        elif len(times) == 1:
            print(f"{pid}: ONE cut-off for every day tried ({when(times.pop())}).")
            print("    A later delivery day does not help this product.")
        else:
            print(f"{pid}: the cut-off MOVES with the delivery day "
                  f"({len(times)} different values).")
            print("    Asking for a later day is a real answer; the agent already does.")

    print("\nIf every cut-off is fixed and already past, the only honest way to show")
    print("this product is to judge cut-offs at a stated earlier instant, and say so.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
