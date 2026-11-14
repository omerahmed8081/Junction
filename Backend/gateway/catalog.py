"""Catalogue search over gateway.products (NIGHT_SHIFT brief 1, step 3).

Catalogue data only: no price and no stock ever comes out of this module. The one
place it touches Aimo's API is the optional cutoff filter (`deliverable_by`), and
that is applied to the ranked shortlist, never to the whole catalogue.

Loaded by scripts/load_catalogue.py; labels by scripts/label_categories.py.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable

from shared.schemas import CategoryLabel, ProductSummary, SearchRequest, SearchResponse

# How many ranked rows we look at before the cutoff filter and the limit.
SHORTLIST = 60
# A resolved category must be this convincing, or we do not filter on category at all.
MIN_CATEGORY_SCORE = 0.45

# Words that only restate a claim. With the claim filter on they add nothing, and
# leaving them in would drop products whose name does not spell the claim out.
CLAIM_WORDS = {
    "FREE_FROM_LACTOSE": {"lactose", "lactosefree", "laktoositon", "laktosfri"},
    "FREE_FROM_GLUTEN": {"gluten", "glutenfree", "gluteeniton", "glutenfri"},
    "VEGAN": {"vegan", "vegaaninen", "vegansk"},
    "VEGETARIAN": {"vegetarian"},
}
FILLER = {"free", "from", "of", "the", "a", "an", "and", "with", "without", "for", "in", "packs", "pack"}

COLUMNS = """
    p.product_id, coalesce(p.name_en, p.name_fi, p.name_sv, '') AS name, p.vendor, p.category,
    l.label AS category_label, p.sales_unit, p.base_unit, p.base_units_per_sales_unit,
    p.allowed_lot_size,
    p.size_per_sales_unit, p.size_unit, p.size_source, p.claims, p.labels, p.allergens,
    p.origin, p.gtin, p.variable_weight, p.delivery_reliability, p.carbon_kg_per_kg
"""
FROM = "FROM gateway.products p LEFT JOIN gateway.category_labels l ON l.category = p.category"
TEXT_MATCH = (
    "(p.search_text @@ websearch_to_tsquery('english', %(q)s)"
    " OR p.search_text @@ websearch_to_tsquery('finnish', %(q)s)"
    " OR p.search_text @@ websearch_to_tsquery('swedish', %(q)s)"
    # Finnish and Swedish build compounds ("kevytmaitojuoma"), which full-text search
    # cannot see inside, so those two names also match on every word as a substring.
    " OR p.name_fi ILIKE ALL(%(likes)s) OR p.name_sv ILIKE ALL(%(likes)s)"
    # English names join words too ("milkdrink"), so a word may also match the start
    # of a word. Start only: "ham" must not find "champignon".
    " OR p.name_en ~* ALL(%(starts)s))"
)


def clean_text(text: str | None, claims: list[str]) -> str:
    """The search words, minus filler and minus words that only restate a claim."""
    drop = set(FILLER)
    for claim in claims:
        drop |= CLAIM_WORDS.get(claim, set())
    words = re.findall(r"[0-9A-Za-zÀ-ÿ]+", (text or "").lower().replace("-", ""))
    return " ".join(w for w in words if w not in drop)


def _filters(req: SearchRequest, query: str, category: str | None) -> tuple[str, dict[str, Any]]:
    where, params = ["true"], {
        "q": query,
        "likes": [f"%{w}%" for w in query.split()],
        "starts": [r"\m" + re.escape(w) for w in query.split()],
    }
    if query:
        where.append(TEXT_MATCH)
    if req.claims:
        where.append("p.claims @> %(claims)s")
        params["claims"] = list(req.claims)
    if category:
        where.append("p.category = %(category)s")
        params["category"] = category
    if req.brand:
        where.append("(p.vendor ILIKE %(brand)s OR p.name_en ILIKE %(brand)s OR p.name_fi ILIKE %(brand)s)")
        params["brand"] = f"%{req.brand.strip()}%"
    if req.exclude_variable_weight:
        where.append("NOT p.variable_weight")
    if req.pack_size:
        # Pack size means one base unit (one 10 L box), not one case of them.
        where.append(
            "p.size_per_sales_unit IS NOT NULL AND upper(p.size_unit) = %(unit)s AND "
            "abs(p.size_per_sales_unit / nullif(p.base_units_per_sales_unit, 0) - %(pack)s) <= %(pack)s * 0.02"
        )
        params["unit"] = (req.pack_size_unit or "L").upper()
        params["pack"] = req.pack_size
    return " AND ".join(where), params


def resolve_category(conn, req: SearchRequest, query: str) -> str | None:
    """Category code for this request: given, looked up from a label, or worked out from the text."""
    if req.category:
        return req.category
    if req.category_label:
        row = conn.execute(
            "SELECT category FROM gateway.category_labels WHERE lower(label) = lower(%s) "
            "ORDER BY product_count DESC LIMIT 1",
            (req.category_label.strip(),),
        ).fetchone()
        return row[0] if row else None
    if not query:
        return None

    where, params = _filters(req, query, None)
    rows = conn.execute(
        f"SELECT p.category, count(*), max(l.product_count), max(l.label) {FROM} "
        f"WHERE {where} GROUP BY p.category",
        params,
    ).fetchall()
    total = sum(r[1] for r in rows)
    if not total:
        return None

    words = {w.rstrip("s") for w in query.split()}
    best, best_score, best_evidence = None, 0.0, False
    for category, matched, size, label in rows:
        label_words = [w.rstrip("s") for w in re.findall(r"[a-z]+", (label or "").lower())]
        overlap = sum(1 for w in label_words if w in words) / len(label_words) if label_words else 0.0
        # share: how much of the result is this category; coverage: how much of the
        # category matches; overlap: does our own label say the same thing as the request.
        score = matched / total + 0.5 * matched / max(size or matched, 1) + 0.5 * overlap
        if score > best_score:
            # Evidence: most of the matches sit here, or our own label names the request.
            best, best_score, best_evidence = category, score, (matched / total >= 0.5 or overlap > 0)
    return best if best_evidence and best_score >= MIN_CATEGORY_SCORE else None


def search(conn, req: SearchRequest, cutoff_filter: Callable[[list[str], date], set[str]] | None = None) -> SearchResponse:
    """Run one search. `cutoff_filter(ids, date)` returns the ids still orderable by that date."""
    query = clean_text(req.text, req.claims)
    category = resolve_category(conn, req, query)
    where, params = _filters(req, query, category)
    params["shortlist"] = max(SHORTLIST, req.limit)

    total = conn.execute(f"SELECT count(*) {FROM} WHERE {where}", params).fetchone()[0]
    rank = "ts_rank(p.search_text, websearch_to_tsquery('english', %(q)s))" if query else "0.0::float"
    cur = conn.execute(
        f"SELECT {COLUMNS} {FROM} WHERE {where} ORDER BY {rank} DESC, name LIMIT %(shortlist)s",
        params,
    )
    names = [d.name for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]

    if req.deliverable_by and cutoff_filter and rows:
        orderable = cutoff_filter([r["product_id"] for r in rows], req.deliverable_by)
        kept = [r for r in rows if r["product_id"] in orderable]
        total -= len(rows) - len(kept)      # exact when the shortlist held every match
        rows = kept

    products = [
        ProductSummary(**{
            **r,
            "size_unit": r["size_unit"] or "",
            "size_per_sales_unit": None if r["size_per_sales_unit"] is None else round(r["size_per_sales_unit"], 4),
        })
        for r in rows[: req.limit]
    ]
    return SearchResponse(products=products, total_matched=total)


def categories(conn) -> list[CategoryLabel]:
    rows = conn.execute(
        "SELECT category, label, product_count FROM gateway.category_labels ORDER BY product_count DESC"
    ).fetchall()
    return [CategoryLabel(category=c, label=l, product_count=n) for c, l, n in rows]
