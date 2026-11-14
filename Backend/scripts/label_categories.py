"""Give the 349 unnamed category codes a label (docs/ARCHITECTURE.md section 4).

Reads sample product names per category from gateway.products, asks Gemini for a
short label per code, and stores the result in gateway.category_labels and in
Data/category_labels.json (commit that file: it lets anyone reload the labels
without calling Gemini again).

    cd Backend
    python scripts/label_categories.py --dry-run          # show the first prompt, call nothing
    python scripts/label_categories.py                    # label every category
    python scripts/label_categories.py --show 15          # print the biggest categories to check by hand
    python scripts/label_categories.py --set 10400 "milk drinks"   # fix one by hand; it is never overwritten again
    python scripts/label_categories.py --from-file        # reload Data/category_labels.json into the database

Needs DATABASE_URL and GEMINI_API_KEY in Backend/.env. GEMINI_MODEL is optional
(default gemini-3.5-flash-lite). No embedding model is used.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABELS_FILE = ROOT / "Data" / "category_labels.json"

DDL = """
CREATE SCHEMA IF NOT EXISTS gateway;
CREATE TABLE IF NOT EXISTS gateway.category_labels (
    category      text PRIMARY KEY,
    label         text NOT NULL,
    product_count integer NOT NULL,
    checked       boolean NOT NULL DEFAULT false,   -- true once a human confirmed or set it
    updated_at    timestamptz NOT NULL DEFAULT now()
);
"""

UPSERT = """
INSERT INTO gateway.category_labels (category, label, product_count, checked)
VALUES (%s, %s, %s, %s)
ON CONFLICT (category) DO UPDATE
   SET label = EXCLUDED.label, product_count = EXCLUDED.product_count,
       checked = EXCLUDED.checked, updated_at = now()
 WHERE NOT gateway.category_labels.checked OR EXCLUDED.checked
"""

INSTRUCTIONS = """You label product categories for a Finnish food-service wholesaler.
For each category code below you get the number of products and a few example product names.
Give each code one short English label: 1 to 3 lowercase words, a plain plural noun phrase
that a chef would use, such as "milk drinks", "frozen fish", "paper napkins", "cleaning chemicals".
Describe what the products are, not a brand. Do not repeat the code in the label.

Answer with JSON only, no prose and no code fence: a list of objects
[{"category": "<code>", "label": "<label>"}], one object for every code given, no others.

Categories:
"""


def spread(names: list[str], k: int) -> list[str]:
    """k names taken evenly across the list, so the sample is not all one brand."""
    names = [n for n in names if n]
    if len(names) <= k:
        return names
    step = len(names) / k
    return [names[int(i * step)] for i in range(k)]


def load_categories(conn, samples: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT category, count(*), array_agg(name_en ORDER BY product_id) "
            "FROM gateway.products GROUP BY category ORDER BY count(*) DESC"
        )
        return [
            {"category": c, "product_count": n, "examples": spread(names, samples)}
            for c, n, names in cur.fetchall()
        ]


def build_prompt(batch: list[dict]) -> str:
    lines = [
        f'{c["category"]} ({c["product_count"]} products): ' + " | ".join(c["examples"])
        for c in batch
    ]
    return INSTRUCTIONS + "\n".join(lines)


def ask_gemini(client, model: str, prompt: str) -> str:
    """One call. Tries the Interactions API first, then generate_content.

    No temperature, top_p or top_k: the 3.5 models ignore them and later ones reject them.
    Thinking is left at the model default (minimal for Flash-Lite), which suits labelling.
    """
    if hasattr(client, "interactions"):
        try:
            return client.interactions.create(model=model, input=prompt).output_text
        except Exception as exc:  # noqa: BLE001 - fall back to the older call
            print(f"  interactions call failed ({type(exc).__name__}: {exc}); trying generate_content")
    return client.models.generate_content(model=model, contents=prompt).text


def parse_labels(text: str, wanted: set[str]) -> dict[str, str]:
    """Pull {category: label} out of the reply, ignoring anything we did not ask for."""
    match = re.search(r"\[.*\]", text or "", flags=re.S)
    if not match:
        return {}
    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    out: dict[str, str] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("category", "")).strip()
        label = " ".join(str(item.get("label", "")).lower().split())
        if code in wanted and label:
            out[code] = label[:60]
    return out


def label_all(categories: list[dict], ask, batch_size: int, pause: float) -> dict[str, str]:
    labels: dict[str, str] = {}
    batches = [categories[i:i + batch_size] for i in range(0, len(categories), batch_size)]
    for number, batch in enumerate(batches, 1):
        todo = batch
        for attempt in (1, 2, 3):
            wanted = {c["category"] for c in todo}
            try:
                got = parse_labels(ask(build_prompt(todo)), wanted)
            except Exception as exc:  # noqa: BLE001 - rate limit or network: wait and retry
                print(f"  batch {number} attempt {attempt} failed: {type(exc).__name__}: {exc}")
                got = {}
                time.sleep(10 * attempt)
            labels.update(got)
            todo = [c for c in todo if c["category"] not in got]
            if not todo:
                break
        print(f"  batch {number}/{len(batches)}: {len(batch) - len(todo)}/{len(batch)} labelled")
        time.sleep(pause)
    return labels


def save(conn, rows: list[tuple[str, str, int, bool]]) -> None:
    with conn.cursor() as cur:
        cur.execute(DDL)
        cur.executemany(UPSERT, rows)
    conn.commit()


def export(conn) -> int:
    """Write every stored label to Data/category_labels.json, so the file matches the database."""
    with conn.cursor() as cur:
        cur.execute("SELECT category, label, product_count, checked FROM gateway.category_labels ORDER BY product_count DESC")
        stored = [
            {"category": c, "label": l, "product_count": n, "checked": k}
            for c, l, n, k in cur.fetchall()
        ]
    LABELS_FILE.write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(stored)


def show(conn, limit: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT l.category, l.label, l.product_count, l.checked, "
            "       (SELECT name_en FROM gateway.products p WHERE p.category = l.category "
            "         ORDER BY product_id LIMIT 1) "
            "FROM gateway.category_labels l ORDER BY l.product_count DESC LIMIT %s",
            (limit,),
        )
        for category, label, count, checked, example in cur.fetchall():
            mark = "checked" if checked else "       "
            print(f"  {category:>7}  {count:>5}  {mark}  {label:<28}  e.g. {example}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the first prompt; call and write nothing")
    ap.add_argument("--show", type=int, metavar="N", help="print the N biggest categories with their labels")
    ap.add_argument("--set", nargs=2, metavar=("CODE", "LABEL"), help="set one label by hand and mark it checked")
    ap.add_argument("--export", action="store_true", help="rewrite Data/category_labels.json from the database")
    ap.add_argument("--from-file", action="store_true", help="load Data/category_labels.json into the database")
    ap.add_argument("--samples", type=int, default=8, help="example names per category")
    ap.add_argument("--batch", type=int, default=40, help="categories per Gemini call")
    ap.add_argument("--pause", type=float, default=4.0, help="seconds between calls (free-tier rate limits)")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    url = os.environ.get("DATABASE_URL")
    if not url or "user:pass@host" in url:
        sys.exit("Set DATABASE_URL in Backend/.env first.")

    import psycopg

    with psycopg.connect(url) as conn:
        if args.show:
            show(conn, args.show)
            return

        if args.export:
            print(f"Wrote {export(conn)} labels to {LABELS_FILE.name}.")
            return

        if args.set:
            code, label = args.set[0], " ".join(args.set[1].lower().split())
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM gateway.products WHERE category = %s", (code,))
                count = cur.fetchone()[0]
            if not count:
                sys.exit(f"No products in category {code}.")
            save(conn, [(code, label, count, True)])
            export(conn)
            print(f"{code} -> {label!r} (checked); {LABELS_FILE.name} updated")
            return

        if args.from_file:
            rows = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
            save(conn, [(r["category"], r["label"], r["product_count"], bool(r.get("checked"))) for r in rows])
            print(f"Loaded {len(rows)} labels from {LABELS_FILE.name}.")
            return

        categories = load_categories(conn, args.samples)
        print(f"{len(categories)} categories, {sum(c['product_count'] for c in categories)} products")

        if args.dry_run:
            print("\n--- first prompt ---\n" + build_prompt(categories[:5]) + "\n--- dry run: nothing called or written ---")
            return

        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            sys.exit("Set GEMINI_API_KEY in Backend/.env first.")
        model = os.environ.get("GEMINI_MODEL") or "gemini-3.5-flash-lite"
        from google import genai

        client = genai.Client(api_key=key)
        print(f"Labelling with {model} ...")
        labels = label_all(categories, lambda p: ask_gemini(client, model, p), args.batch, args.pause)

        counts = {c["category"]: c["product_count"] for c in categories}
        save(conn, [(code, label, counts[code], False) for code, label in labels.items()])

        stored_count = export(conn)

        missing = [c["category"] for c in categories if c["category"] not in labels]
        print(f"Labelled {len(labels)}/{len(categories)}; wrote {LABELS_FILE.name} ({stored_count} rows).")
        if missing:
            print(f"Not labelled ({len(missing)}): {', '.join(missing[:20])} ... run again to retry them.")
        print("\nThe biggest 15, to check by hand:")
        show(conn, 15)


if __name__ == "__main__":
    main()
