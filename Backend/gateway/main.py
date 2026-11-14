"""The Aimo gateway service. Run from Backend/:

    uvicorn gateway.main:app --reload --port 8001

Then http://localhost:8001/docs shows the OpenAPI page for every endpoint.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Before any gateway import: aimo_client reads its settings when it is imported.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from psycopg_pool import ConnectionPool  # noqa: E402

from gateway import catalog, delivery, keys, orders, quotes  # noqa: E402
from gateway.aimo_client import AimoClient, AimoError, answer_delivery  # noqa: E402
from gateway.orders import OrderRefused, OrderStore  # noqa: E402
from gateway.quotes import QuoteRegistry  # noqa: E402
from shared.schemas import (  # noqa: E402
    CategoryListResponse,
    CheckDeliveryItem,
    CheckDeliveryRequest,
    CheckDeliveryResponse,
    CreateQuoteRequest,
    CreateQuoteResponse,
    OrderRecord,
    PlaceOrderRequest,
    ProductSummary,
    SearchRequest,
    SearchResponse,
)
from shared.signing import Signer  # noqa: E402

pool: ConnectionPool | None = None
_aimo: AimoClient | None = None

# Quotes and orders live in memory for now, which matches the kitchen's baskets.
# Both are plain dict lookups so they can become tables with a unique index on
# the quote id without touching the logic above them.
registry = QuoteRegistry()
store = OrderStore()
_signer: Signer | None = None
_kitchen_verifier = None


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
# The kitchen reaches the gateway server-to-server, but the demo reset button and any
# second client run in a browser on another origin. Open in the prototype; in
# production this is the list of registered agents' origins.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# A health check must answer even when the database does not. Aiven's free tier
# powers off when idle and takes ~30 s to wake, and a check that blocks that long
# gets the container killed and restarted in a loop.
HEALTH_DB_TIMEOUT = 2.0


def signer() -> Signer:
    global _signer
    if _signer is None:
        _signer = Signer(keys.gateway_keypair())
    return _signer


def kitchen_keys():
    """The kitchen public keys registered at startup (see gateway/keys.py)."""
    global _kitchen_verifier
    # Only cache a verifier that actually holds keys. Caching an empty one meant
    # that if the kitchen was not up at the first order, every later order was
    # refused until the gateway was restarted.
    if _kitchen_verifier is None or not _kitchen_verifier.known_kids:
        _kitchen_verifier = keys.kitchen_verifier()
    return _kitchen_verifier


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


@app.get("/.well-known/jwks.json")
def jwks() -> dict:
    """Aimo's public key, so any agent can verify a quote with a standard library."""
    return keys.own_jwks()


@app.get("/health")
def health() -> dict:
    """Always answers, and says whether the catalogue database is reachable.

    Returns 200 even when degraded, on purpose: a 503 while Aiven is waking up
    would make a host restart us instead of waiting.
    """
    try:
        with pool.connection(timeout=HEALTH_DB_TIMEOUT) as conn:
            count = conn.execute("SELECT count(*) FROM gateway.products").fetchone()[0]
    except Exception as exc:        # noqa: BLE001 - any database trouble is the same answer here
        return {"status": "degraded", "database": False, "products": None,
                "detail": f"{type(exc).__name__}: {exc}"[:200]}
    return {"status": "ok", "database": True, "products": count}


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


def _summaries_and_results(product_ids: list[str], quantities: dict[str, float],
                           requested_date: date):
    """Catalogue rows and a fresh price and delivery answer for those products."""
    with pool.connection() as conn:
        summaries = {s.product_id: s for s in catalog.summaries(conn, product_ids)}
    sales_units = {pid: s.sales_unit for pid, s in summaries.items()}
    known = [pid for pid in product_ids if pid in summaries]
    raw_prices = aimo().prices(known, requested_date) if known else {}
    raw_availability = aimo().availability(known, requested_date) if known else {}
    items = [CheckDeliveryItem(product_id=pid, quantity_sales_units=quantities[pid])
             for pid in known]
    response = delivery.build_response(items, requested_date, raw_prices,
                                       raw_availability, sales_units)
    return summaries, {r.product_id: r for r in response.results}


@app.post("/gateway/create_quote", response_model=CreateQuoteResponse)
def create_quote(req: CreateQuoteRequest) -> CreateQuoteResponse:
    """Sign a price confirmation for exactly these lines.

    The price is fixed until it expires; delivery is a snapshot and is checked
    again when the order is placed.
    """
    if not req.lines:
        raise HTTPException(status_code=422, detail="lines must not be empty")
    if any(l.quantity_sales_units <= 0 for l in req.lines):
        raise HTTPException(status_code=422, detail="quantity_sales_units must be above 0")

    quantities = {l.product_id: l.quantity_sales_units for l in req.lines}
    ids = list(quantities)
    try:
        summaries, results = _summaries_and_results(ids, quantities, req.requested_date)
    except AimoError as exc:
        raise HTTPException(status_code=502, detail=f"Aimo price or availability call failed: {exc}") from exc

    customer = os.environ.get("AIMO_CUSTOMER_ID", "123")
    try:
        return quotes.build_quote(
            issuer=keys.GATEWAY_URL, customer_id=customer, agent_id=req.agent_id,
            requested_date=req.requested_date, summaries=summaries, results=results,
            wanted=quantities, signer=signer(),
        )
    except ValueError:
        # Nothing could be stood behind. Say which line and why, rather than 500.
        refused = [quotes.refusal_for(r, quantities[pid]) or {"product_id": pid, "reason": "unknown"}
                   for pid, r in results.items()]
        missing = [{"product_id": pid, "reason": "not_found"} for pid in ids if pid not in summaries]
        raise HTTPException(status_code=409,
                            detail={"reason": "nothing_quotable", "refused": refused + missing})


@app.post("/gateway/place_order", response_model=OrderRecord)
def place_order(req: PlaceOrderRequest) -> OrderRecord:
    """Accept an order, if the quote, the mandate and the human approval all hold."""
    def recheck(requested_date: date, items: list[CheckDeliveryItem]):
        quantities = {i.product_id: i.quantity_sales_units for i in items}
        _, results = _summaries_and_results(list(quantities), quantities, requested_date)
        return results

    agent_id = _agent_from_quote(req.quote_jws)
    try:
        return orders.place_order(
            request=req, agent_id=agent_id, gateway_url=keys.GATEWAY_URL,
            own_verifier=keys.own_verifier(), kitchen_verifier=kitchen_keys(),
            recheck_delivery=recheck, registry=registry, store=store,
            signer=signer(), new_order_id=quotes.new_order_id,
        )
    except OrderRefused as exc:
        # 409 rather than 400: the request was well formed, the state said no.
        detail = exc.rejection.model_dump()
        detail["claimed"] = orders.describe_failed_token(req.quote_jws)
        raise HTTPException(status_code=409, detail=detail) from exc
    except AimoError as exc:
        raise HTTPException(status_code=502, detail=f"Aimo delivery re-check failed: {exc}") from exc


def _agent_from_quote(token: str) -> str:
    """Who the quote was made out to.

    Read before verification only to know which agent to verify against; every
    check that matters then runs against the verified payload.
    """
    claimed = orders.describe_failed_token(token)
    agent = claimed.get("aud")
    if not agent:
        raise HTTPException(status_code=422, detail="the quote names no agent")
    return str(agent)


@app.get("/gateway/orders/{order_id}", response_model=OrderRecord)
def get_order(order_id: str) -> OrderRecord:
    record = store.get(order_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no such order")
    return record


@app.post("/gateway/demo/reset")
def demo_reset() -> dict:
    """Clear this customer's orders, used quote ids and weekly spend. Demo only.

    Labelled on purpose. Without it, rehearsing eats the mandate's weekly limit
    and the first beat gets refused on stage. The gateway only clears its own
    side; the kitchen has its own reset, because neither service reaches into
    the other's data.
    """
    customer = os.environ.get("AIMO_CUSTOMER_ID", "123")
    removed = store.forget_customer(customer)
    registry.forget_customer(removed)
    return {"customer": customer, "orders_cleared": len(removed),
            "weekly_net_now": store.weekly_net(customer, datetime.now(timezone.utc))}
