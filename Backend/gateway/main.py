"""The Aimo gateway service. Run from Backend/:

    uvicorn gateway.main:app --reload --port 8001

Then http://localhost:8001/docs shows the OpenAPI page for every endpoint.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

# Before any gateway import: aimo_client reads its settings when it is imported.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, HTTPException  # noqa: E402
from psycopg_pool import ConnectionPool  # noqa: E402

from gateway import catalog, delivery  # noqa: E402
from gateway.aimo_client import AimoClient, AimoError, answer_delivery  # noqa: E402
from shared.schemas import (  # noqa: E402
    CategoryListResponse,
    CheckDeliveryRequest,
    CheckDeliveryResponse,
    SearchRequest,
    SearchResponse,
)

pool: ConnectionPool | None = None
_aimo: AimoClient | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global pool
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (see Backend/.env.example)")
    # Small on purpose: the free Aiven plan allows 20 connections for everyone.
    pool = ConnectionPool(url, min_size=1, max_size=4, open=True)
    yield
    pool.close()
    if _aimo:
        _aimo.close()


app = FastAPI(title="Aimo agent gateway", version="0.1.0", lifespan=lifespan)


def aimo() -> AimoClient:
    global _aimo
    if _aimo is None:
        _aimo = AimoClient()
    return _aimo


def orderable_by(product_ids: list[str], by: date) -> set[str]:
    """Ids that can still be ordered for delivery by this date: deliverable, and cutoff not passed."""
    raw = aimo().availability(product_ids, by)
    keep = set()
    for pid in product_ids:
        answer = answer_delivery(raw.get(pid), 1, by)
        if answer["can_fulfil"] != "none" and not answer.get("past_closing_time"):
            keep.add(pid)
    return keep


@app.get("/health")
def health() -> dict:
    with pool.connection() as conn:
        count = conn.execute("SELECT count(*) FROM gateway.products").fetchone()[0]
    return {"status": "ok", "products": count}


@app.get("/gateway/categories", response_model=CategoryListResponse)
def list_categories() -> CategoryListResponse:
    with pool.connection() as conn:
        return CategoryListResponse(categories=catalog.categories(conn))


@app.post("/gateway/search_products", response_model=SearchResponse)
def search_products(req: SearchRequest) -> SearchResponse:
    try:
        with pool.connection() as conn:
            return catalog.search(conn, req, cutoff_filter=orderable_by)
    except AimoError as exc:
        # Only reachable when deliverable_by is set: the cutoff check needs Aimo's API.
        raise HTTPException(status_code=502, detail=f"Aimo availability check failed: {exc}") from exc


@app.post("/gateway/check_delivery", response_model=CheckDeliveryResponse)
def check_delivery(req: CheckDeliveryRequest) -> CheckDeliveryResponse:
    """Price and delivery for a shortlist. Never returns the warehouse count."""
    if not req.items:
        raise HTTPException(status_code=422, detail="items must not be empty")
    if len(req.items) > delivery.MAX_ITEMS:
        raise HTTPException(
            status_code=422,
            detail=f"at most {delivery.MAX_ITEMS} items per call: check a shortlist, not the catalogue",
        )
    if any(i.quantity_sales_units <= 0 for i in req.items):
        raise HTTPException(status_code=422, detail="quantity_sales_units must be above 0")

    ids = list(dict.fromkeys(i.product_id for i in req.items))
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT product_id, sales_unit FROM gateway.products WHERE product_id = ANY(%s)", (ids,)
        ).fetchall()
    sales_units = dict(rows)

    known = [i for i in ids if i in sales_units]
    try:
        raw_prices = aimo().prices(known, req.requested_date) if known else {}
        raw_availability = aimo().availability(known, req.requested_date) if known else {}
    except AimoError as exc:
        raise HTTPException(status_code=502, detail=f"Aimo price or availability call failed: {exc}") from exc

    return delivery.build_response(req.items, req.requested_date, raw_prices, raw_availability, sales_units)
