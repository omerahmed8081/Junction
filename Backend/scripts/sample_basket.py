"""Print a real basket, offline, from the responses recorded at hour 0.

No database, no Gemini, no network: the gateway is stood in for by the recorded
price and availability payloads, so the JSON is exactly what the live services
produce for the same request.

    cd Backend
    python scripts/sample_basket.py                                  # the demo request
    python scripts/sample_basket.py "500 L lactose-free milk by Tuesday"
    python scripts/sample_basket.py --out ../Frontend/sample_basket.json

The frontend can build against the written file without running anything.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kitchen import agent  # noqa: E402
from kitchen.need import parse_with_rules  # noqa: E402
from shared.schemas import LineEdit  # noqa: E402
from test_agent import SATURDAY, RecordedGateway  # noqa: E402

DEFAULT = "1,000 L lactose-free milk by Tuesday"


def build(message: str):
    need = parse_with_rules(message, today=SATURDAY)
    state, reply, steps = agent.add_need(None, need, RecordedGateway())
    return need, state, reply, steps


def summarise(basket, steps) -> None:
    print(f"  status           {basket.status}")
    print(f"  net / VAT / gross  EUR {basket.net_total:,.2f} / {basket.vat_total:,.2f} / {basket.gross_total:,.2f}")
    if basket.policy:
        print(f"  needs approval by  {basket.policy.level}"
              + (f" ({'; '.join(basket.policy.reasons)})" if basket.policy.reasons else ""))
    for line in basket.lines:
        print(f"\n  {line.product_id}  {line.name[:62]}")
        print(f"     need {line.need_value:g} {line.need_unit}"
              f"  ->  {line.quantity_sales_units:g} {line.sales_unit}"
              f"  ({line.size_per_sales_unit:g} {line.size_unit} each, size from {line.size_source})")
        print(f"     EUR {line.price_per_sales_unit} per {line.price_unit}"
              f"  |  EUR {line.price_per_comparison_unit}/{line.comparison_unit}"
              f"  |  net {line.line_net:,.2f}  VAT {line.line_vat:,.2f}  gross {line.line_gross:,.2f}")
        if line.delivery:
            print(f"     delivery {line.delivery.can_fulfil} on {line.delivery.delivery_date}"
                  f"  |  cutoff {line.delivery.closing_time}"
                  f"  |  stock confirmed: {line.delivery.availability_known}")
        print(f"     why: {line.reason}")
        if line.flags:
            print(f"     flags: {', '.join(line.flags)}")
        for alt in line.alternatives:
            print(f"     alternative {alt.product_id}  {alt.quantity_sales_units:g} {alt.sales_unit}"
                  f"  net {alt.line_net:,.2f}  -  {alt.reason}")
    print("\n  trace")
    for step in steps:
        mark = "gateway" if step.crossed_gateway else "local  "
        print(f"     [{mark}] {step.step}: {step.detail[:80]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("message", nargs="?", default=DEFAULT)
    ap.add_argument("--out", help="also write the basket JSON here")
    ap.add_argument("--quantity", type=float, help="then edit the first line to this quantity")
    ap.add_argument("--swap-to", help="then swap the first line to this product id")
    ap.add_argument("--order", action="store_true",
                    help="then confirm and approve it, with real signing")
    ap.add_argument("--role", default="manager", help="who approves: chef, manager or owner")
    ap.add_argument("--stock-moves", type=float, metavar="UNITS",
                    help="with --order: pretend Aimo has only this many units left by the "
                         "time the order is placed, to see the basket repair itself")
    args = ap.parse_args()

    need, state, reply, steps = build(args.message)
    print(f'\nrequest  "{args.message}"')
    print(f"  parsed as        {need.quantity:g} {need.unit} of '{need.product_text}'"
          f" for {need.requested_date}, claims {need.claims or 'none'} ({need.parser} parser)")
    print(f'  reply            "{reply}"')
    summarise(state.basket, steps)

    if args.quantity or args.swap_to:
        edit = LineEdit(
            product_id=state.basket.lines[0].product_id,
            quantity_sales_units=args.quantity,
            swap_to_product_id=args.swap_to,
        )
        print(f"\nedit  {edit.model_dump(exclude_none=True)}")
        reply, steps = agent.apply_edits(state, [edit], RecordedGateway())
        print(f'  reply            "{reply}"')
        summarise(state.basket, steps)

    if args.order:
        # The whole commit step, offline: Aimo signs a real quote, the kitchen
        # signs a real mandate and approval, and Aimo runs all ten checks.
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
        from test_ordering import NOW, StubGateway                  # noqa: E402
        from kitchen import ordering                                # noqa: E402
        from kitchen.gateway_client import GatewayRefused           # noqa: E402
        from kitchen.ordering import KitchenSigner, OrderingError   # noqa: E402

        signing_gateway = StubGateway()
        kitchen = KitchenSigner()
        # Rebuild the basket against the same stub, so one gateway sees it all.
        need, state, reply, steps = build(args.message)
        state, reply, steps = agent.add_need(None, need, signing_gateway)
        print("\nconfirm")
        try:
            reply, steps = ordering.confirm(state.basket, signing_gateway, kitchen, now=NOW)
        except OrderingError as exc:
            print(f'  refused: {exc}')
            return
        print(f'  reply            "{reply}"')
        print(f"  status           {state.basket.status}")
        print(f"  quote            {state.basket.quote_id}  verified={state.basket.quote_verified}"
              f"  expires {state.basket.quote_expires}")
        print(f"  jws              {state.basket.quote_jws[:60]}...")
        print(f"  approval needed  {state.basket.policy.level} (binding={state.basket.policy.binding})")

        if args.stock_moves is not None:
            from test_orders import AVAILABILITY
            first = state.basket.lines[0].product_id
            moved = dict(AVAILABILITY)
            moved[first] = {**AVAILABILITY[first], "scheduleLines": [
                {**AVAILABILITY[first]["scheduleLines"][0],
                 "availableAmount": args.stock_moves}]}
            signing_gateway.availability = moved
            print(f"\n  [demo] Aimo now has only {args.stock_moves:g} "
                  f"{state.basket.lines[0].sales_unit} of {first} left")

        print(f"\napprove as {args.role}")
        try:
            result = ordering.approve(state, args.role, signing_gateway, kitchen, now=NOW)
        except OrderingError as exc:
            print(f"  not allowed: {exc}")
            return
        import textwrap
        print(f"  outcome          {result.outcome}")
        print("  reply            " + "\n                   ".join(
            textwrap.wrap(result.reply, 76)))
        if result.refusal:
            print(f"  refusal          {result.refusal.reason}"
                  + ("  [SECURITY EVENT]" if result.refusal.security_event else ""))
        if result.order:
            record = result.order
            print(f"  order            {record.order_id}  status {record.status}")
            print(f"  net / gross      EUR {record.net_total:,.2f} / {record.gross_total:,.2f}")
            print(f"  delivery         {record.delivery_date}  approved by {record.approver_role}")
            print(f"  confirmation     {record.confirmation_jws[:60]}...")
        else:
            print(f"  basket now       {state.basket.status}, "
                  f"EUR {state.basket.net_total:,.2f} net")
            for line in state.basket.lines:
                print(f"     {line.product_id} {line.quantity_sales_units:g} {line.sales_unit}"
                      f"  net {line.line_net:,.2f}  {line.flags}")
                for alt in line.alternatives:
                    print(f"       suggests {alt.product_id} {alt.quantity_sales_units:g} "
                          f"{alt.sales_unit} net {alt.line_net:,.2f} - {alt.reason}")
        print("\n  trace")
        for step in result.steps:
            mark = "gateway" if step.crossed_gateway else "local  "
            print(f"     [{mark}] {step.step}: {step.detail[:80]}")
        return

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(state.basket.model_dump_json(indent=2), encoding="utf-8")
        print(f"\nbasket JSON written to {path}")


if __name__ == "__main__":
    main()
