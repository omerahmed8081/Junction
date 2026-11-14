"""The kitchen service. Run from Backend/, with the gateway already running on 8001:

    uvicorn kitchen.main:app --reload --port 8002

http://localhost:8002/docs lists the endpoints the frontend calls.

Baskets are kept in memory for now, which is enough for the hour 7 checkpoint.
They move to the kitchen's own database schema with the order flow (block 2).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from kitchen import agent  # noqa: E402
from kitchen.gateway_client import GatewayClient, GatewayError  # noqa: E402
from kitchen.need import parse_need  # noqa: E402
from shared.schemas import Basket, BasketEditRequest, ChatRequest, ChatResponse  # noqa: E402

gateway: GatewayClient | None = None
baskets: dict[str, agent.BasketState] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    global gateway
    gateway = gateway or GatewayClient()
    yield
    gateway.close()


app = FastAPI(title="Kitchen service", version="0.1.0", lifespan=lifespan)
# Open during the hackathon so the frontend dev server can call it; narrow it for production.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _state(basket_id: str) -> agent.BasketState:
    state = baskets.get(basket_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"basket {basket_id} not found")
    return state


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "baskets": len(baskets)}


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    """One sentence in, a basket out. With basket_id set, the need is added to that basket."""
    state = _state(req.basket_id) if req.basket_id else None
    try:
        need = parse_need(req.message)
    except ValueError as exc:
        return ChatResponse(reply=str(exc), basket=state.basket if state else None)
    try:
        state, reply, steps = agent.add_need(state, need, gateway)
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    baskets[state.basket.basket_id] = state
    return ChatResponse(reply=reply, basket=state.basket, steps=steps)


@app.get("/api/basket/{basket_id}", response_model=Basket)
def get_basket(basket_id: str) -> Basket:
    return _state(basket_id).basket


@app.patch("/api/basket/{basket_id}", response_model=ChatResponse)
def edit_basket(basket_id: str, req: BasketEditRequest) -> ChatResponse:
    """Change a quantity, swap to an alternative, or remove a line."""
    state = _state(basket_id)
    if req.requested_date:
        state.basket.requested_date = req.requested_date
    try:
        reply, steps = agent.apply_edits(state, req.edits, gateway)
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ChatResponse(reply=reply, basket=state.basket, steps=steps)
