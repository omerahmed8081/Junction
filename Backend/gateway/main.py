"""The Aimo gateway service. Run from Backend/:

    uvicorn gateway.main:app --reload --port 8001

Then http://localhost:8001/docs shows the OpenAPI page for every endpoint.
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Before any gateway import: aimo_client reads its settings when it is imported.
# Tests set TTA_SKIP_DOTENV so that importing this module does not pull the
# developer's own .env into the test process. It once did: a customer id from
# .env reached modules that read it at import, and tests run afterwards built
# mandates for a different customer than their fixtures and saw every order
# refused - but only under one test ordering.
if not os.environ.get("TTA_SKIP_DOTENV"):
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import Depends, FastAPI, Header, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from psycopg_pool import ConnectionPool  # noqa: E402

from gateway import catalog, delivery, keys, orders, quotes  # noqa: E402
from gateway import agents, audit, revocations  # noqa: E402
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
    MandatePayload,
    OrderRecord,
    PlaceOrderRequest,
    ProductSummary,
    RevokeMandateRequest,
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
    audit.log.set_pool(pool)        # the audit log writes to the same database
    revocations.revoked.set_pool(pool)
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


def orderable_by(product_ids: list[str], by: date, customer: str | None = None) -> set[str]:
    """Ids that can still be ordered for delivery by this date: deliverable, and cutoff not passed."""
    raw = aimo().availability(product_ids, by, customer=customer)
    keep = set()
    for pid in product_ids:
        answer = answer_delivery(raw.get(pid), 1, by)
        if answer["can_fulfil"] != "none" and not answer.get("past_closing_time"):
            keep.add(pid)
    return keep


def current_caller(x_api_key: str | None = Header(default=None)) -> agents.Caller:
    """Every gateway call names its agent. /health and the JWKS stay public.

    Aimo prices by contract, so we cannot price for an agent we cannot name;
    there is deliberately no anonymous mode.
    """
    found = agents.lookup(x_api_key)
    if found is None:
        raise HTTPException(status_code=401,
                            detail="unknown or missing X-API-Key: every agent needs a registered key")
    return found


def _record(caller: agents.Caller, action: str, outcome: str, status: int,
            started: float, **extra) -> None:
    """One audit row. Never raises: a failed log must not fail the request."""
    try:
        audit.log.record(
            agent_id=caller.agent_id, customer_id=caller.customer_id,
            key_fingerprint=caller.key_fingerprint, action=action, outcome=outcome,
            status=status, duration_ms=int((time.perf_counter() - started) * 1000),
            **extra)
    except Exception as exc:                        # noqa: BLE001
        print(f"[gateway] could not write the audit row for {action}: {exc}")


# Also under /gateway, which is the only part of this service a shared
# front door passes through. An outside agent finds Aimo's key at
# <issuer>/.well-known/jwks.json, and behind one origin the issuer ends in
# /gateway; the bare path is kept for a gateway reached directly.
@app.get("/.well-known/jwks.json")
@app.get("/gateway/.well-known/jwks.json")
def jwks() -> dict:
    """Aimo's public key, so any agent can verify a quote with a standard library."""
    return keys.own_jwks()


@app.get("/health")
@app.get("/gateway/health")
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
def list_categories(caller: agents.Caller = Depends(current_caller)) -> CategoryListResponse:
    with pool.connection() as conn:
        return CategoryListResponse(categories=catalog.categories(conn))


@app.post("/gateway/search_products", response_model=SearchResponse)
def search_products(req: SearchRequest,
                    caller: agents.Caller = Depends(current_caller)) -> SearchResponse:
    started = time.perf_counter()
    asked = {"text": req.text, "claims": req.claims, "category": req.category,
             "product_ids": req.product_ids, "deliverable_by": req.deliverable_by}
    try:
        with pool.connection() as conn:
            found = catalog.search(
                conn, req,
                cutoff_filter=lambda ids, by: orderable_by(ids, by, caller.customer_id))
    except AimoError as exc:
        _record(caller, "search_products", "error", 502, started, request=asked,
                detail=f"Aimo availability check failed: {exc}")
        # Only reachable when deliverable_by is set: the cutoff check needs Aimo's API.
        raise HTTPException(status_code=502, detail=f"Aimo availability check failed: {exc}") from exc
    _record(caller, "search_products", "ok", 200, started, request=asked,
            response={"matched": found.total_matched,
                      "returned": [p.product_id for p in found.products]},
            detail=f"searched {req.text!r}, returned {len(found.products)}")
    return found


@app.post("/gateway/check_delivery", response_model=CheckDeliveryResponse)
def check_delivery(req: CheckDeliveryRequest,
                   caller: agents.Caller = Depends(current_caller)) -> CheckDeliveryResponse:
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
    started = time.perf_counter()
    asked = {"requested_date": req.requested_date,
             "items": [{"product_id": i.product_id, "quantity_sales_units": i.quantity_sales_units}
                       for i in req.items]}
    try:
        # Priced for whoever is asking: Aimo prices by contract.
        raw_prices = (aimo().prices(known, req.requested_date, customer=caller.customer_id)
                      if known else {})
        raw_availability = (aimo().availability(known, req.requested_date,
                                                customer=caller.customer_id)
                            if known else {})
    except AimoError as exc:
        _record(caller, "check_delivery", "error", 502, started, request=asked,
                detail=f"Aimo price or availability call failed: {exc}")
        raise HTTPException(status_code=502, detail=f"Aimo price or availability call failed: {exc}") from exc

    answer = delivery.build_response(req.items, req.requested_date, raw_prices,
                                     raw_availability, sales_units)
    _record(caller, "check_delivery", "ok", 200, started, request=asked,
            response={"results": [{"product_id": r.product_id,
                                   "can_fulfil": r.delivery.can_fulfil,
                                   "price_per_sales_unit": r.price.price_per_sales_unit}
                                  for r in answer.results]},
            detail=f"priced {len(known)} product(s) for {req.requested_date}")
    return answer


def _summaries_and_results(product_ids: list[str], quantities: dict[str, float],
                           requested_date: date, customer: str | None = None,
                           fresh: bool = False):
    """Catalogue rows and a price and delivery answer for those products.

    `customer` is whose contract to price on. `fresh` asks Aimo again rather
    than reusing an answer from the last minute, which the order-time re-check
    needs: its whole job is to see what changed since the quote.
    """
    with pool.connection() as conn:
        summaries = {s.product_id: s for s in catalog.summaries(conn, product_ids)}
    sales_units = {pid: s.sales_unit for pid, s in summaries.items()}
    known = [pid for pid in product_ids if pid in summaries]
    raw_prices = (aimo().prices(known, requested_date, customer=customer, fresh=fresh)
                  if known else {})
    raw_availability = (aimo().availability(known, requested_date, customer=customer, fresh=fresh)
                        if known else {})
    items = [CheckDeliveryItem(product_id=pid, quantity_sales_units=quantities[pid])
             for pid in known]
    response = delivery.build_response(items, requested_date, raw_prices,
                                       raw_availability, sales_units)
    return summaries, {r.product_id: r for r in response.results}


@app.post("/gateway/create_quote", response_model=CreateQuoteResponse)
def create_quote(req: CreateQuoteRequest,
                 caller: agents.Caller = Depends(current_caller)) -> CreateQuoteResponse:
    """Sign a price confirmation for exactly these lines.

    The price is fixed until it expires; delivery is a snapshot and is checked
    again when the order is placed.
    """
    if req.agent_id and req.agent_id != caller.agent_id:
        # The body may not name a different agent than the key: that is how one
        # agent would get a quote issued to another and ride its mandate.
        _record(caller, "create_quote", "refused", 403, time.perf_counter(),
                reason="agent_mismatch", security_event=True,
                request={"claimed_agent": req.agent_id},
                detail=f"key belongs to {caller.agent_id}, request claimed {req.agent_id}")
        raise HTTPException(status_code=403,
                            detail=f"this key is {caller.agent_id}, the request says {req.agent_id}")
    if not req.lines:
        raise HTTPException(status_code=422, detail="lines must not be empty")
    if any(l.quantity_sales_units <= 0 for l in req.lines):
        raise HTTPException(status_code=422, detail="quantity_sales_units must be above 0")

    quantities = {l.product_id: l.quantity_sales_units for l in req.lines}
    ids = list(quantities)
    started = time.perf_counter()
    asked = {"requested_date": req.requested_date, "lines": quantities}
    try:
        summaries, results = _summaries_and_results(ids, quantities, req.requested_date,
                                                    customer=caller.customer_id)
    except AimoError as exc:
        _record(caller, "create_quote", "error", 502, started, request=asked,
                detail=f"Aimo price or availability call failed: {exc}")
        raise HTTPException(status_code=502, detail=f"Aimo price or availability call failed: {exc}") from exc

    try:
        quote = quotes.build_quote(
            issuer=keys.gateway_issuer(), customer_id=caller.customer_id, agent_id=caller.agent_id,
            requested_date=req.requested_date, summaries=summaries, results=results,
            wanted=quantities, signer=signer(),
        )
        issued = audit.token_summary(quote.quote_jws)
        _record(caller, "create_quote", "ok", 200, started, request=asked,
                quote_jti=str(issued.get("jti") or ""),
                response={"quote": issued, "net_total": quote.net_total,
                          "expires": str(quote.expires_at)},
                detail=f"quoted {len(quantities)} line(s), {quote.net_total} EUR net, "
                       f"for customer {caller.customer_id}")
        return quote
    except ValueError:
        # Nothing could be stood behind. Say which line and why, rather than 500.
        refused = [quotes.refusal_for(r, quantities[pid]) or {"product_id": pid, "reason": "unknown"}
                   for pid, r in results.items()]
        missing = [{"product_id": pid, "reason": "not_found"} for pid in ids if pid not in summaries]
        _record(caller, "create_quote", "refused", 409, started, request=asked,
                reason="nothing_quotable", response={"refused": refused + missing},
                detail="nothing on this request could be stood behind")
        raise HTTPException(status_code=409,
                            detail={"reason": "nothing_quotable", "refused": refused + missing})


@app.post("/gateway/place_order", response_model=OrderRecord)
def place_order(req: PlaceOrderRequest,
                caller: agents.Caller = Depends(current_caller)) -> OrderRecord:
    """Accept an order, if the quote, the mandate and the human approval all hold."""
    def recheck(requested_date: date, items: list[CheckDeliveryItem]):
        quantities = {i.product_id: i.quantity_sales_units for i in items}
        # Asked again, never from the cache: this is the check that notices the
        # stock moved or a cut-off passed between the quote and the order.
        _, results = _summaries_and_results(list(quantities), quantities, requested_date,
                                            customer=caller.customer_id, fresh=True)
        return results

    started = time.perf_counter()
    quote_seen = audit.token_summary(req.quote_jws)
    asked = {"quote": quote_seen, "mandate": audit.token_summary(req.mandate_jws),
             "approval": audit.token_summary(getattr(req, "approval_jws", None)),
             "idempotency_key": getattr(req, "idempotency_key", None)}
    quote_jti = str(quote_seen.get("jti") or "")

    agent_id = _agent_from_quote(req.quote_jws)
    if agent_id != caller.agent_id:
        _record(caller, "place_order", "refused", 403, started, request=asked,
                reason="quote_for_another_agent", security_event=True, quote_jti=quote_jti,
                detail=f"quote was issued to {agent_id}, the key is {caller.agent_id}")
        raise HTTPException(status_code=403,
                            detail=f"this quote was issued to {agent_id}, not to {caller.agent_id}")
    try:
        placed = orders.place_order(
            request=req, agent_id=agent_id, gateway_url=keys.gateway_issuer(),
            own_verifier=keys.own_verifier(), kitchen_verifier=kitchen_keys(),
            recheck_delivery=recheck, registry=registry, store=store,
            signer=signer(), new_order_id=quotes.new_order_id,
            revoked=revocations.revoked,
        )
        _record(caller, "place_order", "ok", 200, started, request=asked,
                quote_jti=quote_jti, order_id=placed.order_id,
                response={"order_id": placed.order_id, "net_total": placed.net_total,
                          "status": placed.status},
                detail=f"order {placed.order_id} accepted, {placed.net_total} EUR net")
        return placed
    except OrderRefused as exc:
        # 409 rather than 400: the request was well formed, the state said no.
        detail = exc.rejection.model_dump()
        detail["claimed"] = orders.describe_failed_token(req.quote_jws)
        # The most interesting row in the table: who was turned away, and why.
        _record(caller, "place_order", "refused", 409, started, request=asked,
                quote_jti=quote_jti, reason=exc.rejection.reason,
                security_event=bool(exc.rejection.security_event),
                response=detail, detail=str(exc.rejection.detail or exc.rejection.reason))
        raise HTTPException(status_code=409, detail=detail) from exc
    except AimoError as exc:
        _record(caller, "place_order", "error", 502, started, request=asked,
                quote_jti=quote_jti, detail=f"Aimo delivery re-check failed: {exc}")
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
def get_order(order_id: str, caller: agents.Caller = Depends(current_caller)) -> OrderRecord:
    record = store.get(order_id)
    # Another customer's order is "no such order", never "not yours": the second
    # answer would confirm the id exists.
    if record is None or record.customer != caller.customer_id:
        raise HTTPException(status_code=404, detail="no such order")
    return record


def demo_caller(x_api_key: str | None = Header(default=None)) -> agents.Caller:
    """The demo reset is the one endpoint that works without a key.

    The reset button lives in the frontend, which holds no key, and this clears
    demo state rather than revealing anything. With a key it resets that
    caller's customer; without one, the demo kitchen's.
    """
    return agents.lookup(x_api_key) or agents.lookup(agents.DEMO_KITCHEN_KEY)


@app.post("/gateway/revoke_mandate")
def revoke_mandate(req: RevokeMandateRequest,
                   caller: agents.Caller = Depends(current_caller)) -> dict:
    """Call a mandate back, so no order on it is accepted from now on.

    The customer's own stop button. The mandate is verified first - a token
    that does not verify is not somebody's mandate to revoke - and it must name
    the customer behind this key, so one customer can never call back another's.

    Revoking twice is not an error: the answer is the same either way, which
    matters because the kitchen may be retrying.
    """
    started = time.perf_counter()
    try:
        payload = kitchen_keys().verify(req.mandate_jws, audience=keys.gateway_issuer(),
                                        max_age_seconds=None)
        mandate = MandatePayload(**payload)
    except Exception as exc:        # noqa: BLE001
        _record(caller, "revoke_mandate", "refused", 422, started,
                reason="mandate_invalid", security_event=True,
                detail=f"not a mandate we signed off on: {exc}"[:200])
        raise HTTPException(status_code=422,
                            detail=f"that is not a verifiable mandate: {exc}") from exc

    if mandate.customer != caller.customer_id:
        _record(caller, "revoke_mandate", "refused", 403, started,
                reason="mandate_wrong_customer", security_event=True,
                detail=f"the mandate is customer {mandate.customer}'s, "
                       f"the key is customer {caller.customer_id}'s")
        raise HTTPException(status_code=403, detail="that mandate is not yours to call back")

    entry = revocations.revoked.revoke(
        mandate.jti, caller.customer_id, reason=req.reason or "",
        revoked_by=caller.key_fingerprint)
    _record(caller, "revoke_mandate", "ok", 200, started,
            response={"mandate_jti": mandate.jti, "degraded": revocations.revoked.degraded},
            detail=f"mandate {mandate.jti} called back"
                   + (f": {req.reason}" if req.reason else ""))
    return {"mandate_jti": entry.mandate_jti, "revoked_at": entry.at.isoformat(),
            "agent": mandate.sub, "degraded": revocations.revoked.degraded,
            "note": ("the database was unreachable, so this is remembered only until "
                     "the gateway restarts") if revocations.revoked.degraded else ""}


@app.get("/gateway/revoked_mandates")
def list_revoked(limit: int = 50,
                 caller: agents.Caller = Depends(current_caller)) -> dict:
    """The mandates this customer has called back, newest first."""
    rows = revocations.revoked.for_customer(caller.customer_id, limit=min(max(limit, 1), 200))
    return {"customer": caller.customer_id, "count": len(rows),
            "degraded": revocations.revoked.degraded, "records": rows}


@app.get("/gateway/audit")
def read_audit(limit: int = 100, order_id: str = "", quote_jti: str = "",
               security_only: bool = False,
               caller: agents.Caller = Depends(current_caller)) -> dict:
    """Everything the gateway recorded for this customer, newest first.

    Scoped to the caller's own customer: an agent reads its own history, never
    another customer's. This is "what Aimo sees" with nothing hidden - including
    every refusal, and every signature that did not verify.
    """
    rows = audit.log.recent(caller.customer_id, limit=min(max(limit, 1), 500),
                            order_id=order_id, quote_jti=quote_jti,
                            security_only=security_only)
    return {"customer": caller.customer_id, "count": len(rows),
            "degraded": audit.log.degraded,
            "note": ("the database was unreachable, these rows are from memory and "
                     "are lost on restart") if audit.log.degraded else "",
            "records": rows}


@app.post("/gateway/demo/reset")
def demo_reset(caller: agents.Caller = Depends(demo_caller)) -> dict:
    """Clear this customer's orders, used quote ids and weekly spend. Demo only.

    Labelled on purpose. Without it, rehearsing eats the mandate's weekly limit
    and the first beat gets refused on stage. The gateway only clears its own
    side; the kitchen has its own reset, because neither service reaches into
    the other's data.
    """
    customer = caller.customer_id
    removed = store.forget_customer(customer)
    registry.forget_customer(removed)
    return {"customer": customer, "orders_cleared": len(removed),
            "weekly_net_now": store.weekly_net(customer, datetime.now(timezone.utc))}
