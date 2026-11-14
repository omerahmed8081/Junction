"""The kitchen service. Run from Backend/, with the gateway already running on 8001:

    uvicorn kitchen.main:app --reload --port 8002

http://localhost:8002/docs lists the endpoints the frontend calls.

Baskets are kept in memory for now, which is enough for the hour 7 checkpoint.
They move to the kitchen's own database schema with the order flow (block 2).
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from psycopg_pool import ConnectionPool  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from kitchen import agent, audit, edits, keys, ordering, rules, storage, suggestions  # noqa: E402
from kitchen.inventory import Inventory  # noqa: E402
from kitchen.gateway_client import GatewayClient, GatewayError, GatewayRefused  # noqa: E402
from kitchen import narrate  # noqa: E402
from kitchen.need import mentioned_date, parse_need, parse_needs  # noqa: E402
from kitchen.ordering import KitchenSigner, OrderingError  # noqa: E402
from shared.schemas import (  # noqa: E402
    ApproveRequest,
    InventoryView,
    ServiceRequest,
    ServiceResponse,
    StockRow,
    SuggestionsResponse,
    Basket,
    BasketEditRequest,
    ChatRequest,
    ChatResponse,
    MandateRequest,
    MandateView,
    RuleRequest,
    OrderRecord,
    OrderResponse,
)

gateway: GatewayClient | None = None
pool = None                 # the kitchen's own database connection, for its audit log
baskets: dict[str, agent.BasketState] = {}
orders: dict[str, OrderRecord] = {}
kitchen = KitchenSigner()
inventory = Inventory.from_seed()
# Services logged since start, kept so a restart can replay them: the ledger is
# derived from the seed plus these, rather than saved as raw movements.
logged_services: list[dict] = []


@asynccontextmanager
async def lifespan(_: FastAPI):
    global gateway, pool
    gateway = gateway or GatewayClient()
    # The kitchen's own connection, used only for kitchen.audit_log. It never
    # reads the gateway's schema; in production these are two roles that cannot
    # see each other. Without a URL the audit log simply keeps rows in memory.
    url = os.environ.get("DATABASE_URL")
    if url:
        try:
            pool = ConnectionPool(url, min_size=1, max_size=2, open=True)
            audit.log.set_pool(pool)
            rules.store.set_pool(pool)
        except Exception as exc:        # noqa: BLE001
            print(f"[kitchen] no audit database, keeping rows in memory: {exc}")
    else:
        print("[kitchen] DATABASE_URL is not set: audit rows stay in memory")
    # Baskets, orders and the mandate survive a restart: losing a basket on
    # stage is the one failure worth a file on disk (see kitchen/storage.py).
    stored_baskets, stored_orders, stored_mandate, stored_services = storage.load()
    for entry in stored_services:
        inventory.record_service(date.fromisoformat(entry["date"]), entry["portions"])
        logged_services.append(entry)
    if stored_services:
        print(f"[kitchen] replayed {len(stored_services)} logged service(s) into the ledger")
    baskets.update(stored_baskets)
    orders.update(stored_orders)
    if stored_mandate:
        kitchen.mandate = stored_mandate
    if stored_baskets or stored_orders:
        print(f"[kitchen] restored {len(stored_baskets)} basket(s) and {len(stored_orders)} order(s)")
    yield
    _save()
    gateway.close()
    if pool is not None:
        pool.close()


def _save() -> None:
    """Persist after every change. Cheap at this size, and it keeps the demo safe."""
    try:
        storage.save(baskets, orders, kitchen.mandate, services=logged_services)
    except Exception as exc:        # noqa: BLE001
        # Never fail a request because the state file could not be written.
        print(f"[kitchen] could not save state: {exc}")


app = FastAPI(title="Kitchen service", version="0.1.0", lifespan=lifespan)
# Open during the hackathon so the frontend dev server can call it; narrow it for production.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# Who is using the screen. There is no sign-in yet: the browser says which role
# it is acting as, in this header. Good enough to show each person their view;
# a real deployment puts this behind a login and reads it from the session.
ROLES = ("chef", "manager", "owner")


def _role(value: str | None) -> str:
    role = (value or "chef").strip().lower()
    if role not in ROLES:
        raise HTTPException(status_code=422, detail=f"{value!r} is not a role (chef, manager or owner)")
    return role


def _owner_only(value: str | None, what: str) -> None:
    """The mandate and the standing rules decide what can be spent without
    asking, so only the owner may change them."""
    if _role(value) != "owner":
        raise HTTPException(status_code=403, detail=f"only the owner can {what}")


def _state(basket_id: str) -> agent.BasketState:
    state = baskets.get(basket_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"basket {basket_id} not found")
    return state


@app.get("/.well-known/jwks.json")
def jwks() -> dict:
    """The kitchen's public key, so the gateway can verify its mandates and approvals."""
    return keys.own_jwks()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "baskets": len(baskets), "orders": len(orders),
            "mandate": kitchen.mandate.payload.jti if kitchen.mandate else None}


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, x_kitchen_role: str | None = Header(default=None)) -> ChatResponse:
    """One message in, a basket out.

    A message may ask for several things at once - nobody shops one item at a
    time - so every need in it is added to the same basket, in the order asked.
    With `basket_id` set they are added to that basket instead of a new one.
    """
    role = _role(x_kitchen_role)
    state = _state(req.basket_id) if req.basket_id else None
    if state is not None and state.basket.status != "draft":
        # A basket whose price Aimo has signed (or that is ordered) is closed to
        # the chat: adding to it would quietly void the signature. The message
        # starts the next order instead.
        state = None
    asked_date = mentioned_date(req.message)

    # "make it 20", "drop the rice": a change to what is already there, not a
    # new product. Checked first, because "only 10 kg of butter" parses as a
    # perfectly good new request and would add a second butter line.
    if state is not None and state.basket.lines:
        try:
            change = edits.read(state.basket, req.message)
        except edits.Ambiguous as exc:
            return ChatResponse(
                reply=f"Did you mean {_join(exc.names)}? Say which one and I will change it.",
                basket=state.basket)
        if change is not None:
            return _apply_change(state, change, req.message)

    try:
        needs = parse_needs(req.message)
    except ValueError as exc:
        if state is not None and asked_date is not None:
            # "Deliver on Friday instead": no product, only a new date for the basket.
            return _redate(state, asked_date)
        audit.log.record("parse_need", basket_id=req.basket_id or "", actor="human",
                         inputs={"message": req.message}, reason=str(exc),
                         detail="could not read a quantity and a product from the message")
        return ChatResponse(reply=str(exc), basket=state.basket if state else None)

    audit.log.record("parse_need", basket_id=req.basket_id or "", actor="human",
                     parser=needs[0].parser, inputs={"message": req.message},
                     result={"items": [n.model_dump(mode="json") for n in needs]},
                     detail=narrate.sentence("parse_need", "; ".join(
                         f"{n.quantity:g} {n.unit} of {n.product_text}" for n in needs)))

    replies: list[str] = []
    all_steps: list = []
    failed: list[str] = []
    if state is not None:
        if asked_date is None:
            # A follow-up that names no date keeps the basket's date, rather than
            # moving the whole basket to the parser's default.
            for need in needs:
                need.requested_date = state.basket.requested_date
        elif state.basket.lines and needs[0].requested_date != state.basket.requested_date:
            # A new date for the new items is a new date for the basket: every
            # line already in it is checked again for that day.
            state.basket.requested_date = needs[0].requested_date
            try:
                notes = agent.reprice(state, gateway, all_steps)
            except GatewayError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            replies.append(f"Delivery moved to {_day(state.basket.requested_date)}.")
            replies.extend(notes)
    for need in needs:
        try:
            state, reply, steps = agent.add_need(state, need, gateway)
        except GatewayError as exc:
            # One product the gateway could not answer for must not lose the
            # others: say which failed and keep what was found.
            if not replies:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            failed.append(need.product_text)
            continue
        replies.append(reply)
        all_steps.extend(steps)
        if state is not None:
            audit.log.record_steps(steps, state.basket.basket_id)

    if state is None:
        return ChatResponse(reply="I could not find anything to add.", basket=None)
    if state.basket.basket_id not in baskets:
        # A new basket belongs to whoever started it; that decides who approves.
        state.basket.created_by = role
    if failed:
        replies.append("I could not reach Aimo for " + _join(failed)
                       + ". Ask again and I will add " + ("it" if len(failed) == 1 else "them") + ".")
    baskets[state.basket.basket_id] = state
    _save()
    return ChatResponse(reply=" ".join(replies), basket=state.basket,
                        steps=all_steps, parser=needs[0].parser)


def _join(names: list[str]) -> str:
    """'milk', 'rice' and 'butter' - the way a person writes a list."""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _day(when: date) -> str:
    """Tuesday 17 November: how a chef says a delivery date."""
    return f"{narrate.WEEKDAYS[when.weekday()]} {when.day} {narrate.MONTHS[when.month - 1]}"


def _apply_change(state: agent.BasketState, change, message: str) -> ChatResponse:
    """One spoken change, through the same path the basket's buttons use."""
    before = change.line.quantity_sales_units
    try:
        reply, steps = agent.apply_edits(state, [change.as_edit()], gateway)
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if change.action == "remove":
        said = f"Removed {change.line.name}."
    else:
        said = reply or f"{change.line.name}: {change.quantity_sales_units:g} {change.line.sales_unit}."
        if "rounded_up_to_whole_packs" in change.flags:
            said += (f" That is whole {change.line.sales_unit.lower()}s, so you get "
                     f"{change.line.delivered_amount:g} {change.line.need_unit}.")
        if "unit_ignored" in change.flags:
            said += (f" I read that as {change.quantity_sales_units:g} "
                     f"{change.line.sales_unit}, because that product has no pack size.")

    audit.log.record("edit_basket", basket_id=state.basket.basket_id, actor="human",
                     inputs={"message": message, "product_id": change.line.product_id,
                             "was": before},
                     result={"action": change.action,
                             "quantity_sales_units": change.quantity_sales_units},
                     detail=narrate.sentence("edit_basket", said))
    audit.log.record_steps(steps, state.basket.basket_id)
    _save()
    return ChatResponse(reply=said, basket=state.basket, steps=steps)


def _redate(state: agent.BasketState, new_date: date) -> ChatResponse:
    """Move the whole basket to another delivery date and check every line again."""
    if new_date == state.basket.requested_date:
        return ChatResponse(reply=f"The basket is already for {_day(new_date)}.", basket=state.basket)
    steps: list = []
    state.basket.requested_date = new_date
    try:
        notes = agent.reprice(state, gateway, steps)
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit.log.record_steps(steps, state.basket.basket_id)
    _save()
    reply = f"Delivery moved to {_day(new_date)}. " + (
        " ".join(notes) if notes else "Every line can still be delivered on that day.")
    return ChatResponse(reply=reply, basket=state.basket, steps=steps)


@app.get("/api/baskets", response_model=list[Basket])
def list_baskets(status: str = "") -> list[Basket]:
    """Every basket, or those in one status, newest price first.

    The Approvals page uses `?status=quoted`, so a basket the chef confirmed on
    the kitchen tablet shows up on the manager's phone."""
    found = [s.basket for s in baskets.values() if not status or s.basket.status == status]
    return sorted(found, key=lambda b: str(b.quote_expires or ""), reverse=True)


@app.get("/api/basket/{basket_id}", response_model=Basket)
def get_basket(basket_id: str) -> Basket:
    return _state(basket_id).basket


@app.delete("/api/basket/{basket_id}")
def delete_basket(basket_id: str) -> dict:
    """Throw away a draft. A basket Aimo has signed, or that is ordered, is kept:
    it is part of the record, and its signed price may already be with a manager."""
    state = _state(basket_id)
    if state.basket.status != "draft":
        raise HTTPException(status_code=409,
                            detail=f"this basket is {state.basket.status} and can no longer be deleted")
    del baskets[basket_id]
    audit.log.record("delete_basket", basket_id=basket_id, actor="human",
                     detail=f"draft deleted with {len(state.basket.lines)} line(s)")
    _save()
    return {"deleted": basket_id}


@app.patch("/api/basket/{basket_id}", response_model=ChatResponse)
def edit_basket(basket_id: str, req: BasketEditRequest) -> ChatResponse:
    """Change a quantity, swap to an alternative, or remove a line."""
    state = _state(basket_id)
    if req.requested_date and not req.edits:
        # A new date alone: check every line again for that day, not just relabel.
        return _redate(state, req.requested_date)
    if req.requested_date:
        state.basket.requested_date = req.requested_date
    try:
        reply, steps = agent.apply_edits(state, req.edits, gateway)
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    _save()
    return ChatResponse(reply=reply, basket=state.basket, steps=steps)


@app.post("/api/basket/{basket_id}/confirm", response_model=ChatResponse)
def confirm_basket(basket_id: str) -> ChatResponse:
    """Ask Aimo to sign a price confirmation for this basket.

    The basket moves from draft to quoted, and its figures become the ones Aimo
    signed. Editing it afterwards drops it back to draft and voids the quote.
    """
    state = _state(basket_id)
    try:
        reply, steps = ordering.confirm(state.basket, gateway, kitchen)
    except OrderingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GatewayRefused as exc:
        raise HTTPException(status_code=409, detail=exc.payload or {"reason": exc.reason,
                                                                   "detail": exc.detail}) from exc
    except GatewayError as exc:
        audit.log.record("confirm", basket_id=basket_id, reason=str(exc),
                         detail="the gateway could not be reached for a quote",
                         crossed_gateway=True)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit.log.record_steps(steps, basket_id)
    audit.log.record("confirm", basket_id=basket_id,
                     quote_jti=_quote_id(state.basket), crossed_gateway=True,
                     result={"net_total": state.basket.net_total,
                             "gross_total": state.basket.gross_total,
                             "expires": str(getattr(state.basket, "quote_expires", "") or "")},
                     detail=f"Aimo signed {state.basket.net_total} EUR net; "
                            f"the basket now shows Aimo's own figures")
    _save()
    return ChatResponse(reply=reply, basket=state.basket, steps=steps)


def _quote_id(basket) -> str:
    """The quote's id, for joining this log to the gateway's."""
    return audit.quote_id_of(getattr(basket, "quote_jws", None))


@app.post("/api/basket/{basket_id}/approve", response_model=OrderResponse)
def approve_basket(basket_id: str, req: ApproveRequest) -> OrderResponse:
    """A person approves the signed price, and the order is placed.

    Always 200 with an `outcome`, because all three answers are useful:

    * `ordered`  - done, with the order and Aimo's signed confirmation.
    * `adjusted` - stock moved or a cut-off passed, so the basket now matches
      what Aimo says, carries suggestions for the shortfall, and is a draft
      again. `reply` explains it in words the chat can show.
    * `refused`  - a limit the basket cannot fix itself, such as the mandate.

    Only a caller mistake is an error: approving a draft, or a role that is not
    senior enough for what the policy decided.
    """
    state = _state(basket_id)
    try:
        result = ordering.approve(state, req.approver_role, gateway, kitchen)
    except OrderingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result.order is not None:
        orders[result.order.order_id] = result.order
    audit.log.record_steps(getattr(result, "steps", []) or [], basket_id,
                           order_id=result.order.order_id if result.order else "")
    audit.log.record("approve", basket_id=basket_id, actor="human",
                     approver_role=req.approver_role,
                     order_id=result.order.order_id if result.order else "",
                     quote_jti=_quote_id(state.basket), crossed_gateway=True,
                     reason=(result.refusal.reason if result.refusal else ""),
                     result={"outcome": result.outcome,
                             "net_total": result.order.net_total if result.order else None},
                     detail=_approval_sentence(result, req.approver_role))
    _save()
    return result


def _approval_sentence(result, role: str) -> str:
    """What a person reading the log a week later needs to see first."""
    if result.outcome == "ordered" and result.order is not None:
        return (f"{role} approved; Aimo accepted order {result.order.order_id} "
                f"for {result.order.net_total} EUR net")
    if result.outcome == "adjusted":
        return (f"{role} approved, but Aimo's answer had changed "
                f"({result.refusal.reason if result.refusal else 'unknown'}): "
                "the basket was adjusted to match and went back to draft")
    return (f"{role} approved, but the order was refused: "
            f"{result.refusal.reason if result.refusal else 'unknown'}")


@app.get("/api/orders/{order_id}", response_model=OrderRecord)
def get_order(order_id: str) -> OrderRecord:
    record = orders.get(order_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"order {order_id} not found")
    return record


@app.get("/api/orders", response_model=list[OrderRecord])
def list_orders() -> list[OrderRecord]:
    """Newest first. The Activity tab reads this."""
    return sorted(orders.values(), key=lambda o: o.placed_at, reverse=True)


@app.get("/api/mandate", response_model=MandateView)
def get_mandate() -> MandateView:
    """What this kitchen's agent is currently allowed to order."""
    mandate = kitchen.mandate_in_force()
    return MandateView(mandate_id=mandate.payload.jti, agent_id=mandate.payload.sub,
                       agent_name=ordering.AGENT_NAME,
                       customer=mandate.payload.customer, expires_at=mandate.expires_at,
                       limits=mandate.payload.limits,
                       allowed_categories=mandate.payload.allowed_categories,
                       approval_required=mandate.payload.approval_required,
                       mandate_jws=mandate.jws,
                       role_limits={"chef": agent.CHEF_LIMIT_EUR,
                                    "manager": agent.MANAGER_LIMIT_EUR})


@app.post("/api/mandate", response_model=MandateView)
def issue_mandate(req: MandateRequest, x_kitchen_role: str | None = Header(default=None)) -> MandateView:
    """Sign a new mandate. Only the owner may change what the agent can spend."""
    _owner_only(x_kitchen_role, "change the agent's spending limits")
    mandate = kitchen.issue_mandate(
        per_order=req.max_net_per_order, per_week=req.max_net_per_week,
        categories=req.allowed_categories, approval_required=req.approval_required,
        days=req.days,
    )
    _save()
    return get_mandate()


@app.post("/api/demo/reset")
def demo_reset() -> dict:
    """Clear this kitchen's baskets, orders and mandate. Labelled, demo only.

    The gateway has its own reset for its side: the two services never reach
    into each other's data, so a full reset calls both. Run this before the
    video take and before the pitch, or the weekly limit creeps up on you.
    """
    counts = {"baskets": len(baskets), "orders": len(orders)}
    baskets.clear()
    orders.clear()
    kitchen.mandate = None
    storage.clear()
    mandate = kitchen.mandate_in_force()        # a fresh one, so the next order works
    return {"cleared": counts, "mandate": mandate.payload.jti}


# ------------------------------------------------ inventory and suggestions


def _stock_rows() -> list[StockRow]:
    until = suggestions.window_until(inventory)
    stock, on_order = inventory.stock(), inventory.on_order_totals()
    usage = inventory.expected_usage(inventory.as_of + date.resolution, until)
    rows = []
    for product_id in sorted(set(stock) | set(on_order) | set(usage)):
        rows.append(StockRow(
            product_id=product_id, unit=inventory.units.get(product_id, ""),
            stock=round(stock.get(product_id, 0.0), 3),
            on_order=round(on_order.get(product_id, 0.0), 3),
            expected_usage=round(usage.get(product_id, 0.0), 3),
            runs_out_on=inventory._runs_out_on(
                product_id, stock.get(product_id, 0.0) + on_order.get(product_id, 0.0),
                inventory.as_of + date.resolution, until),
            dishes=sorted({r.name for r in inventory.recipes.values()
                           if any(i["product_id"] == product_id for i in r.ingredients)}),
        ))
    return rows


@app.get("/api/audit")
def read_audit(limit: int = 200, basket_id: str = "", order_id: str = "",
               crossed_only: bool = False) -> dict:
    """Why this kitchen's agent did what it did, newest first.

    `crossed_only=true` is the "what Aimo sees" toggle: the few rows that left
    the building. Everything else - the sentence typed, what the model read,
    how a quantity was worked out - stays here and Aimo never sees it.
    """
    rows = audit.log.recent(limit=min(max(limit, 1), 500), basket_id=basket_id,
                            order_id=order_id, crossed_only=crossed_only)
    return {"count": len(rows), "degraded": audit.log.degraded,
            "note": ("the database was unreachable, these rows are from memory and "
                     "are lost on restart") if audit.log.degraded else "",
            "records": rows}


@app.get("/api/orders/{order_id}/audit")
def order_audit(order_id: str) -> dict:
    """Everything both sides recorded about one order, oldest first.

    One call for the Activity tab: click an order, see the whole story. The
    kitchen's own rows come from its log; Aimo's come from the gateway, which
    answers only for the customer behind our key. Each row says which side it
    is from, so "what Aimo sees" stays visible as the smaller half.
    """
    ours = audit.log.recent(limit=500, order_id=order_id)
    # Only the approval row carries the order id; everything before it belongs
    # to the basket that became this order, and that is most of the story.
    basket_id = next((b.basket.basket_id for b in baskets.values()
                      if b.basket.order_id == order_id), "")
    if basket_id:
        seen = {r.get("id") for r in ours}
        ours += [r for r in audit.log.recent(limit=500, basket_id=basket_id)
                 if r.get("id") not in seen]
    for row in ours:
        row["side"] = "kitchen"
    theirs = gateway.audit(order_id) if gateway else []
    for row in theirs:
        row["side"] = "aimo"
    rows = sorted(ours + theirs, key=lambda r: str(r.get("at") or ""))
    return {"order_id": order_id, "count": len(rows),
            "kitchen_rows": len(ours), "aimo_rows": len(theirs),
            "degraded": audit.log.degraded, "records": rows}


@app.get("/api/rules")
def list_rules(include_inactive: bool = False) -> dict:
    """The kitchen's standing rules, in the order they were set."""
    found = rules.store.all(active_only=not include_inactive)
    return {"count": len(found), "degraded": rules.store.degraded,
            "note": ("the database was unreachable, these rules are in memory and "
                     "are lost on restart") if rules.store.degraded else "",
            "rules": [r.as_json() for r in found]}


@app.post("/api/rules")
def add_rule(req: RuleRequest, x_kitchen_role: str | None = Header(default=None)) -> dict:
    """Set a standing rule: what may go through without anyone looking.

    A rule only ever relaxes an approval the kitchen would ask for itself, and
    the signed mandate still sits above it, so the worst a wrong rule can do is
    skip a click.
    """
    _owner_only(x_kitchen_role, "set standing rules")
    if req.match not in rules.MATCHES:
        raise HTTPException(status_code=422,
                            detail=f"match must be one of {', '.join(rules.MATCHES)}")
    if req.match != "any" and not req.value.strip():
        raise HTTPException(status_code=422, detail=f"a {req.match} rule needs a value")
    if req.max_net_per_order is not None and req.max_net_per_order <= 0:
        raise HTTPException(status_code=422, detail="the ceiling must be above 0")

    rule = rules.store.add(rules.Rule(
        name=req.name.strip(), match=req.match, value=req.value.strip(),
        max_net_per_order=req.max_net_per_order))
    audit.log.record("rule_added", actor="human", result=rule.as_json(),
                     detail=narrate.sentence("rule_added", rule.said()))
    for state in baskets.values():                  # the drafts on screen follow the new rule
        agent._refresh(state.basket)
    _save()
    return rule.as_json()


@app.delete("/api/rules/{rule_id}")
def remove_rule(rule_id: int, x_kitchen_role: str | None = Header(default=None)) -> dict:
    """Switch a rule off. Rules are never deleted: one that was in force when an
    order went through has to stay readable afterwards."""
    _owner_only(x_kitchen_role, "switch standing rules off")
    if not rules.store.remove(rule_id):
        raise HTTPException(status_code=404, detail=f"no rule {rule_id}")
    audit.log.record("rule_removed", actor="human", result={"rule_id": rule_id},
                     detail=narrate.sentence("rule_removed", f"rule {rule_id} switched off"))
    for state in baskets.values():
        agent._refresh(state.basket)
    _save()
    return {"rule_id": rule_id, "active": False}


@app.get("/api/inventory", response_model=InventoryView)
def get_inventory() -> InventoryView:
    """Stock, what is on order, and what the menu will use. `on_order` is never
    part of `stock`: an order becomes stock when it is received."""
    return InventoryView(as_of=inventory.as_of,
                         window_until=suggestions.window_until(inventory),
                         rows=_stock_rows())


@app.get("/api/suggestions", response_model=SuggestionsResponse)
def get_suggestions() -> SuggestionsResponse:
    """What the kitchen will run out of. Reads the ledger only, so it is cheap
    and makes no call to Aimo; draft a basket for one with the endpoint below."""
    return SuggestionsResponse(as_of=inventory.as_of,
                               window_until=suggestions.window_until(inventory),
                               suggestions=suggestions.find(inventory))


@app.post("/api/suggestions/{product_id}/basket", response_model=ChatResponse)
def draft_suggestion(product_id: str, x_kitchen_role: str | None = Header(default=None)) -> ChatResponse:
    """Turn one suggestion into a draft basket, through the normal agent path."""
    found = next((s for s in suggestions.find(inventory) if s.product_id == product_id), None)
    if found is None:
        raise HTTPException(status_code=404,
                            detail=f"{product_id} is not short at the moment")
    try:
        state, reply, steps = suggestions.draft(found, inventory, gateway, baskets)
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    state.basket.created_by = _role(x_kitchen_role)
    _save()
    return ChatResponse(reply=reply, basket=state.basket, steps=steps)


@app.post("/api/service", response_model=ServiceResponse)
def log_service(req: ServiceRequest) -> ServiceResponse:
    """"What did you cook today?" - one number per dish.

    The recipes turn that into stock out, which is the whole reason a chef is
    asked about dishes rather than about ingredients. Then the running-low
    trigger runs, and the most urgent shortfall comes back as a draft basket.
    """
    when = req.service_date or inventory.as_of
    unknown = [d for d in req.portions if d not in inventory.recipes]
    if unknown:
        raise HTTPException(status_code=422,
                            detail=f"no recipe for {', '.join(unknown)}")
    written = inventory.record_service(when, req.portions)
    logged_services.append({"date": when.isoformat(), "portions": req.portions})

    found = suggestions.find(inventory)
    basket = None
    steps: list = []
    if found:
        try:
            state, _, steps = suggestions.draft(found[0], inventory, gateway, baskets)
            basket = state.basket
        except GatewayError as exc:
            # The service was still logged; the draft can be retried.
            print(f"[kitchen] logged the service but could not draft a basket: {exc}")
    _save()

    dishes = ", ".join(f"{count:g} x {inventory.recipes[d].name}"
                       for d, count in req.portions.items() if count)
    reply = f"Logged {dishes or 'nothing'} for {when}. "
    reply += (f"{len(found)} product(s) are running low; "
              f"{found[0].reason}" if found else "Nothing is running low.")
    return ServiceResponse(reply=reply, as_of=inventory.as_of,
                           products_used=len({m.product_id for m in written}),
                           suggestions=found, basket=basket, steps=steps)
