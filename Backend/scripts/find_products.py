"""Search the catalogue for real product ids, with the pack size worked out.

For whoever writes the seed recipes, and handy for the gateway work too. It uses
the same shared/units.py the product will use, so a size shown here is the size
the agent will compute.

    cd Backend
    python scripts/find_products.py --text cream --category 10401
    python scripts/find_products.py --text butter --claim FREE_FROM_LACTOSE
    python scripts/find_products.py --categories            # what the codes hold
    python scripts/find_products.py --text milk --price 2026-11-17   # live price too

--price also tells you whether the product is orderable: a cutoff that has passed
means it cannot be bought, whatever the catalogue says. 20 of the 35 lactose-free
milks are in that state today.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.units import pack_size  # noqa: E402

CATALOGUE = ROOT / "Data" / "valio_aimo_junction26_product_data.json"


def load() -> list[dict]:
    if not CATALOGUE.exists():
        sys.exit(f"Download the product file to {CATALOGUE} first (it is gitignored, 84 MB).")
    return json.loads(CATALOGUE.read_text(encoding="utf-8"))


def names(product: dict) -> dict[str, str]:
    return {n["language"]: n["value"] for n in product["synkkaData"].get("names", [])}


def claims_of(product: dict) -> set[str]:
    return {
        v["id"]
        for c in product["synkkaData"].get("classifications", [])
        if c["name"] == "nutritionalClaim"
        for v in c["values"]
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="", help="matched against the Finnish and English names")
    ap.add_argument("--category", default="", help="category code, e.g. 10400")
    ap.add_argument("--claim", default="", help="e.g. FREE_FROM_LACTOSE")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--categories", action="store_true", help="list category codes by size")
    ap.add_argument("--price", default="", help="also call the live API for this delivery date")
    args = ap.parse_args()

    products = load()

    if args.categories:
        counts = Counter(p["category"] for p in products)
        samples: dict[str, str] = {}
        for p in products:
            samples.setdefault(p["category"], names(p).get("en") or names(p).get("fi") or "")
        print(f"{len(counts)} category codes, largest first\n")
        for code, n in counts.most_common(60):
            print(f"  {code}  {n:>4} products   e.g. {samples[code][:60]}")
        return

    needle = args.text.lower()
    hits = []
    for p in products:
        if args.category and p["category"] != args.category:
            continue
        if args.claim and args.claim not in claims_of(p):
            continue
        label = names(p)
        text = " ".join(label.values()).lower()
        if needle and needle not in text:
            continue
        size_base, size_sales, unit, source = pack_size(p)
        hits.append({
            "id": p["id"],
            "name": label.get("en") or label.get("fi") or "",
            "category": p["category"],
            "sales_unit": p["salesUnit"],
            "base_unit": p["baseUnit"],
            "size_per_base_unit": size_base,
            "size_per_sales_unit": size_sales,
            "size_unit": unit,
            "size_source": source,
            "variable_weight": bool(p["synkkaData"].get("variableUnit")),
        })
        if len(hits) >= args.limit:
            break

    print(f"{len(hits)} shown\n")
    for h in hits:
        size = (
            f"{h['size_per_base_unit']:g} {h['size_unit']} per {h['base_unit']}"
            if h["size_per_base_unit"] else "size unknown"
        )
        per_sales = (
            f", {h['size_per_sales_unit']:g} {h['size_unit']} per {h['sales_unit']}"
            if h["size_per_sales_unit"] and h["sales_unit"] != h["base_unit"] else ""
        )
        flag = "  [variable weight: not auto-orderable]" if h["variable_weight"] else ""
        print(f"  {h['id']}  cat {h['category']}  sold per {h['sales_unit']}")
        print(f"      {h['name'][:72]}")
        print(f"      {size}{per_sales}  (from {h['size_source']}){flag}")

    if args.price and hits:
        from datetime import date, datetime, timezone

        from gateway.aimo_client import AimoClient, answer_delivery, read_price

        client = AimoClient()
        ids = [h["id"] for h in hits]
        when = date.fromisoformat(args.price)
        prices = client.prices(ids, when)
        avails = client.availability(ids, when)
        now = datetime.now(timezone.utc)
        print(f"\nlive price and delivery for {args.price}\n")
        for h in hits:
            price = read_price(prices.get(h["id"]))
            delivery = answer_delivery(avails.get(h["id"]), 1, when, now)
            orderable = delivery["can_fulfil"] != "none" and not delivery.get("past_closing_time")
            mark = "OK " if orderable else "NO "
            print(f"  {mark}{h['id']}  net {price.get('price_per_sales_unit')} per {h['sales_unit']}"
                  f"  |  {price.get('comparison_price')}/{price.get('comparison_unit')}"
                  f"  |  {delivery['can_fulfil']}"
                  f"  |  cutoff {delivery.get('closing_time')}"
                  f"{'  (PASSED)' if delivery.get('past_closing_time') else ''}")
        client.close()


if __name__ == "__main__":
    main()
