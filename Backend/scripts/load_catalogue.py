"""Load the product catalogue into Postgres (NIGHT_SHIFT brief 1, step 1).

Creates the `gateway` schema and one table, `gateway.products`, then loads every
product with the columns the search endpoint needs. Safe to re-run: it drops and
rebuilds the table each time.

    cd Backend
    python scripts/load_catalogue.py --dry-run     # no database: build rows, print the checks
    python scripts/load_catalogue.py               # load into DATABASE_URL from .env

The pack size comes from shared.units.pack_size(), the same function the agent
uses, so a size stored here is the size the product will compute.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.units import base_units_per_sales_unit, pack_size  # noqa: E402

DATA = ROOT / "Data"
CATALOGUE = DATA / "valio_aimo_junction26_product_data.json"
RELIABILITY = DATA / "valio_aimo_junction26_delivery_reliability.csv"
CARBON = DATA / "valio_aimo_junction26_carbon_footprint.csv"

# Temperature marks sit in the same group as the real packaging labels.
NOT_A_LABEL = {"warmTemperature", "coolTemperature", "freezeTemperature"}

DDL = """
CREATE SCHEMA IF NOT EXISTS gateway;
DROP TABLE IF EXISTS gateway.products;
CREATE TABLE gateway.products (
    product_id                text PRIMARY KEY,
    category                  text NOT NULL,
    sales_unit                text NOT NULL,
    base_unit                 text NOT NULL,
    base_units_per_sales_unit double precision NOT NULL,
    allowed_lot_size          double precision,
    name_fi                   text,
    name_en                   text,
    name_sv                   text,
    vendor                    text,
    origin                    text,
    gtin                      text,
    temperature_condition     integer,
    claims                    text[] NOT NULL DEFAULT '{}',
    labels                    text[] NOT NULL DEFAULT '{}',
    allergens                 text[] NOT NULL DEFAULT '{}',
    size_per_sales_unit       double precision,
    size_unit                 text NOT NULL DEFAULT '',
    size_source               text NOT NULL DEFAULT 'unknown',
    variable_weight           boolean NOT NULL DEFAULT false,
    delivery_reliability      double precision,
    carbon_kg_per_kg          double precision,
    raw                       jsonb,
    search_text               tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(name_en, '')) ||
        to_tsvector('finnish', coalesce(name_fi, '')) ||
        to_tsvector('swedish', coalesce(name_sv, ''))
    ) STORED
);
CREATE INDEX products_search_idx   ON gateway.products USING gin (search_text);
CREATE INDEX products_claims_idx   ON gateway.products USING gin (claims);
CREATE INDEX products_category_idx ON gateway.products (category);
"""

COLUMNS = [
    "product_id", "category", "sales_unit", "base_unit", "base_units_per_sales_unit",
    "allowed_lot_size", "name_fi", "name_en", "name_sv", "vendor", "origin", "gtin",
    "temperature_condition", "claims", "labels", "allergens", "size_per_sales_unit",
    "size_unit", "size_source", "variable_weight", "delivery_reliability",
    "carbon_kg_per_kg", "raw",
]


def read_scores(path: Path) -> dict[str, float]:
    """productId -> value from one of the two CSV files. Missing file -> empty."""
    if not path.exists():
        print(f"  note: {path.name} not found, that column stays NULL")
        return {}
    out: dict[str, float] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        for row in reader:
            if len(row) >= 2 and row[1] != "":
                out[row[0]] = float(row[1])
    return out


def group_ids(product: dict, group: str, unit: str | None = None) -> list[str]:
    """The `id` of every value in one classification group, optionally by unit."""
    out = []
    for c in product["synkkaData"].get("classifications") or []:
        if c.get("name") != group:
            continue
        for v in c.get("values") or []:
            if v.get("id") and (unit is None or v.get("unit") == unit):
                out.append(v["id"])
    return sorted(set(out))


def build_row(product: dict, reliability: dict, carbon: dict, keep_raw: bool) -> dict:
    synkka = product["synkkaData"]
    names = {n["language"]: n["value"] for n in synkka.get("names") or []}
    _, size_per_sales, size_unit, size_source = pack_size(product)
    pid = product["id"]
    return {
        "product_id": pid,
        "category": product.get("category") or "",
        "sales_unit": product.get("salesUnit") or "",
        "base_unit": product.get("baseUnit") or "",
        "base_units_per_sales_unit": base_units_per_sales_unit(product),
        "allowed_lot_size": product.get("allowedLotSize"),
        "name_fi": names.get("fi"),
        "name_en": names.get("en"),
        "name_sv": names.get("sv"),
        "vendor": product.get("vendorName"),
        "origin": product.get("countryOfOrigin"),
        "gtin": product.get("salesUnitGtin"),
        "temperature_condition": product.get("temperatureCondition"),
        "claims": group_ids(product, "nutritionalClaim"),
        "labels": [x for x in group_ids(product, "packagingMarkedLabel") if x not in NOT_A_LABEL],
        "allergens": group_ids(product, "allergen", unit="CONTAINS"),
        "size_per_sales_unit": size_per_sales,
        "size_unit": size_unit or "",
        "size_source": size_source,
        "variable_weight": bool(synkka.get("variableUnit")),
        "delivery_reliability": reliability.get(pid),
        "carbon_kg_per_kg": carbon.get(pid),
        "raw": product if keep_raw else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(CATALOGUE), help="path to the product JSON")
    ap.add_argument("--dry-run", action="store_true", help="build rows and print checks, no database")
    ap.add_argument("--no-raw", action="store_true", help="leave the raw JSON column empty (saves ~100 MB)")
    ap.add_argument("--batch", type=int, default=500)
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        sys.exit(f"Product file not found: {path}\nCopy it into Backend/Data/ first (it is gitignored).")

    print(f"Reading {path.name} ...")
    products = json.loads(path.read_text(encoding="utf-8"))
    reliability, carbon = read_scores(RELIABILITY), read_scores(CARBON)
    rows = [build_row(p, reliability, carbon, keep_raw=not args.no_raw) for p in products]

    lactose_milk = [r for r in rows if r["category"] == "10400" and "FREE_FROM_LACTOSE" in r["claims"]]
    print(f"  {len(rows)} products built")
    print(f"  size_source: {dict(Counter(r['size_source'] for r in rows))}")
    print(f"  check: FREE_FROM_LACTOSE in 10400 -> {len(lactose_milk)} rows (expected 35)")

    if args.dry_run:
        print("Dry run: nothing written.")
        return

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    url = os.environ.get("DATABASE_URL")
    if not url or "user:pass@host" in url:
        sys.exit("Set DATABASE_URL in Backend/.env first (see .env.example).")

    import psycopg
    from psycopg.types.json import Jsonb

    insert = (
        f"INSERT INTO gateway.products ({', '.join(COLUMNS)}) "
        f"VALUES ({', '.join('%s' for _ in COLUMNS)})"
    )
    started = time.time()
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            for start in range(0, len(rows), args.batch):
                chunk = rows[start:start + args.batch]
                cur.executemany(insert, [
                    [Jsonb(r[c]) if c == "raw" and r[c] is not None else r[c] for c in COLUMNS]
                    for r in chunk
                ])
                print(f"  inserted {min(start + args.batch, len(rows))}/{len(rows)}", end="\r")
            print()
            cur.execute("ANALYZE gateway.products")
            cur.execute("SELECT count(*) FROM gateway.products")
            total = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM gateway.products "
                "WHERE category = '10400' AND claims @> ARRAY['FREE_FROM_LACTOSE']"
            )
            check = cur.fetchone()[0]
            cur.execute("SELECT pg_size_pretty(pg_total_relation_size('gateway.products'))")
            size = cur.fetchone()[0]
        conn.commit()

    print(f"Loaded {total} rows in {time.time() - started:.0f} s, table size {size}.")
    print(f"Check in Postgres: FREE_FROM_LACTOSE in 10400 -> {check} rows (expected 35)")
    if check != 35 or total != len(rows):
        sys.exit("CHECK FAILED: counts do not match.")


if __name__ == "__main__":
    main()
