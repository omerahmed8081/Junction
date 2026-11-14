"""Confirm, approve, order: the kitchen's half of the commit step.

The shape of it:

    draft  --confirm-->  quoted  --approve-->  ordered

**Confirm** asks Aimo to sign a price confirmation for exactly the lines the chef
settled on, verifies that signature against Aimo's published key, and copies the
figures out of the *verified* payload rather than keeping its own. From then on
the basket shows what Aimo signed, not what we calculated.

**Approve** is where a person is involved. The kitchen signs an approval naming
that one quote, and sends quote, mandate and approval together. Aimo refuses the
order without all three, so "nothing is ordered without a human's OK" is checked
on both sides rather than promised by us.

Any edit after a quote exists invalidates it: the basket drops back to draft and
the next confirm asks for a new quote. That is why the quote is signed at confirm
and not at draft.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from kitchen import agent, keys
from kitchen.gateway_client import GatewayClient, GatewayRefused
from kitchen import narrate
from shared.schemas import (
    AgentStep,
    ApprovalPayload,
    Basket,
    MandateLimits,
    MandatePayload,
    OrderRecord,
    OrderResponse,
    PlaceOrderRequest,
    QuoteLineRequest,
)
from shared.signing import Signer, VerificationError, now_seconds

AGENT_ID = os.environ.get("AGENT_ID", "agent:kitchen-demo")
AGENT_NAME = os.environ.get("AGENT_NAME", "Kitchen agent")
CUSTOMER_ID = os.environ.get("AIMO_CUSTOMER_ID", "123")
CHEF_LIMIT_EUR = float(os.environ.get("CHEF_LIMIT_EUR", "500"))

# Mandate defaults. The Agent rules tab edits these; they are the outer limit
# Aimo enforces, so they sit above the chef and manager limits, not beside them.
MANDATE_PER_ORDER_EUR = float(os.environ.get("MANDATE_PER_ORDER_EUR", "2000"))
MANDATE_PER_DAY_EUR = float(os.environ.get("MANDATE_PER_DAY_EUR", "2000"))
MANDATE_PER_WEEK_EUR = float(os.environ.get("MANDATE_PER_WEEK_EUR", "5000"))
MANDATE_DAYS = int(os.environ.get("MANDATE_DAYS", "7"))
# Whether Aimo must be shown a signed human approval with every order. Off by
# default: a person is still in the loop, because the kitchen's own policy sends
# every basket to someone unless a standing rule clears it. This flag only says
# whether *Aimo* refuses an order that arrives without one.
MANDATE_APPROVAL_REQUIRED = (os.environ.get("MANDATE_APPROVAL_REQUIRED", "false").strip().lower()
                             in {"1", "true", "yes", "on"})
MANDATE_CATEGORIES = [c for c in (os.environ.get("MANDATE_CATEGORIES") or "").split(",") if c]

# Who may approve what. The policy engine decides the level; this says which
# roles satisfy it, so a chef cannot wave through a basket needing a manager.
# "agent" is not a person. It ranks below everyone and may only ever approve a
# basket the kitchen's own rules already cleared (auto_ok), so it cannot sign
# off on anything a person would have had to look at.
ROLE_RANK = {"agent": 0, "chef": 1, "manager": 2, "owner": 3}
AUTO_LEVEL = "auto_ok"
# What each approval level demands of whoever approves. Separate from ROLE_RANK
# because a level is not a role: "auto_ok" is a level nobody holds, and leaving
# it out of the lookup made the agent fail the very check it was added for.
LEVEL_RANK = {AUTO_LEVEL: 0, "chef": 1, "manager": 2, "owner": 3}

# Refusals the basket can recover from by itself: the world moved, so re-check
# and come back with a basket that matches reality. Everything else (a mandate
# limit, a category that is not allowed) needs a person to change something.
RECOVERABLE = {"stock_changed", "past_closing_time"}


class OrderingError(RuntimeError):
    """Something the caller did in the wrong order, e.g. approving a draft."""


@dataclass
class Mandate:
    """The signed mandate currently in force, and what it says."""

    jws: str
    payload: MandatePayload

    @property
    def expires_at(self) -> datetime:
        return datetime.fromtimestamp(self.payload.exp, tz=timezone.utc)


@dataclass
class KitchenSigner:
    """The kitchen's key, its mandate, and Aimo's key to check quotes against."""

    signer: Signer = field(default_factory=lambda: Signer(keys.kitchen_keypair()))
    mandate: Mandate | None = None

    def issue_mandate(
        self,
        *,
        per_order: float | None = None,
        per_day: float | None = None,
        per_week: float | None = None,
        categories: list[str] | None = None,
        approval_required: bool | None = None,
        days: int | None = None,
        now: datetime | None = None,
    ) -> Mandate:
        """Sign a new mandate. The owner does this from the Agent rules tab.

        Issuing a new one replaces the old one here; Aimo accepts whichever is
        sent with an order, so revoking early is a production concern and is on
        the roadmap rather than built.
        """
        moment = (now or datetime.now(timezone.utc)).replace(microsecond=0)
        payload = MandatePayload(
            iss=keys.KITCHEN_URL,
            sub=AGENT_ID,
            aud=keys.GATEWAY_URL,
            customer=CUSTOMER_ID,
            jti=f"m_{secrets.token_hex(5)}",
            iat=now_seconds(moment),
            exp=now_seconds(moment + timedelta(days=days or MANDATE_DAYS)),
            limits=MandateLimits(
                max_net_per_order=per_order if per_order is not None else MANDATE_PER_ORDER_EUR,
                max_net_per_day=per_day if per_day is not None else MANDATE_PER_DAY_EUR,
                max_net_per_week=per_week if per_week is not None else MANDATE_PER_WEEK_EUR,
            ),
            allowed_categories=categories if categories is not None else list(MANDATE_CATEGORIES),
            approval_required=(MANDATE_APPROVAL_REQUIRED if approval_required is None
                               else approval_required),
        )
        self.mandate = Mandate(jws=self.signer.sign(payload.model_dump(mode="json")),
                               payload=payload)
        return self.mandate

    def mandate_in_force(self, now: datetime | None = None) -> Mandate:
        """The current mandate, issuing a first one if there is none yet."""
        moment = now or datetime.now(timezone.utc)
        if self.mandate is None or self.mandate.expires_at <= moment:
            return self.issue_mandate(now=moment)
        return self.mandate

    def sign_approval(self, quote_id: str, approver_role: str,
                      now: datetime | None = None) -> str:
        """One human's OK, naming one quote so it cannot pay for another."""
        moment = (now or datetime.now(timezone.utc)).replace(microsecond=0)
        payload = ApprovalPayload(
            iss=keys.KITCHEN_URL,
            aud=keys.GATEWAY_URL,
            jti=f"a_{secrets.token_hex(5)}",
            quote_jti=quote_id,
            approver_role=approver_role,
            approved_at=now_seconds(moment),
            iat=now_seconds(moment),
            exp=now_seconds(moment + timedelta(hours=1)),
        )
        return self.signer.sign(payload.model_dump(mode="json"))


def _sentence(text: str) -> str:
    """The gateway's reasons are phrases; a reply reads better as sentences."""
    text = (text or "").strip()
    return text if not text or text[-1] in ".!?" else text + "."


def _step(steps: list[AgentStep], step: str, detail: str, crossed: bool = False) -> None:
    """Keep the terse facts, and add the sentence (kitchen/narrate.py)."""
    steps.append(AgentStep(step=step, detail=narrate.sentence(step, detail),
                           facts=detail, for_chef=narrate.is_for_chef(step),
                           crossed_gateway=crossed, at=datetime.now(timezone.utc)))


# ------------------------------------------------------------------- confirm


def confirm(basket: Basket, gateway: GatewayClient, kitchen: KitchenSigner,
            steps: list[AgentStep] | None = None,
            now: datetime | None = None) -> tuple[str, list[AgentStep]]:
    """Ask Aimo to sign these lines, then hold what it signed.

    `now` is for tests, so the clock can be pinned; in service it is the real one.
    """
    steps = steps if steps is not None else []
    if not basket.lines:
        raise OrderingError("there is nothing in the basket to confirm")
    if basket.status == "ordered":
        raise OrderingError("this basket has already been ordered")

    lines = [QuoteLineRequest(product_id=l.product_id,
                              quantity_sales_units=l.quantity_sales_units)
             for l in basket.lines]
    _step(steps, "create_quote", f"{len(lines)} line(s) for {basket.requested_date}", True)
    quote = gateway.create_quote(AGENT_ID, basket.requested_date, lines)

    # Verify Aimo's signature before believing any of it. A quote we cannot
    # verify is worse than no quote: it would put unchecked figures on screen.
    try:
        payload = keys.aimo_verifier().verify(
            quote.quote_jws, issuer=keys.GATEWAY_URL, audience=AGENT_ID, now=now)
    except VerificationError as exc:
        basket.status = "blocked"
        basket.quote_verified = False
        _step(steps, "verify_quote", f"REFUSED: {exc}")
        raise OrderingError(f"Aimo's price confirmation did not verify: {exc}") from exc
    _step(steps, "verify_quote", f"signature ok, expires {quote.expires_at.isoformat()}")

    # From here the basket shows the verified figures, not ours. They should be
    # identical; if they are not, Aimo's are the ones it will stand behind.
    signed = {l["product_id"]: l for l in payload["lines"]}
    kept = []
    for line in basket.lines:
        match = signed.get(line.product_id)
        if match is None:
            continue                            # refused below, with its reason
        line.quantity_sales_units = match["quantity_sales_units"]
        line.price_per_sales_unit = match["price_per_sales_unit"]
        line.line_net = match["line_net"]
        line.line_vat = match["line_vat"]
        line.line_gross = match["line_gross"]
        kept.append(line)

    dropped = [l for l in basket.lines if l.product_id not in signed]
    basket.lines = kept
    basket.net_total = payload["net_total"]
    basket.vat_total = payload["vat_total"]
    basket.gross_total = payload["gross_total"]
    basket.quote_id = quote.quote_id
    basket.quote_jws = quote.quote_jws
    basket.quote_expires = quote.expires_at
    basket.quote_verified = True
    basket.status = "quoted"

    # The policy preview now becomes the decision that is actually enforced.
    if basket.policy is not None:
        basket.policy.binding = True
    _step(steps, "policy_binding",
          f"{basket.policy.level if basket.policy else 'chef'} must approve "
          f"EUR {basket.net_total:,.2f} net")

    reply = (f"Aimo confirmed EUR {basket.net_total:,.2f} net "
             f"({basket.gross_total:,.2f} with VAT), signed and valid until "
             f"{quote.expires_at.strftime('%H:%M')}. ")
    if dropped or quote.refused:
        reasons = ", ".join(f"{r.get('product_id')} ({r.get('reason')})" for r in quote.refused)
        reply += f"Aimo would not quote {reasons or 'one line'}, so it was left out. "
    reply += (f"{(basket.policy.level if basket.policy else 'chef').title()} approval is "
              f"needed to place it.")
    return reply, steps


# ------------------------------------------------------------------- approve


def approve(state, approver_role: str, gateway: GatewayClient,
            kitchen: KitchenSigner, steps: list[AgentStep] | None = None,
            idempotency_key: str | None = None,
            now: datetime | None = None) -> OrderResponse:
    """Sign this person's approval of this quote, then place the order.

    Returns an outcome rather than raising for a refusal, because a refusal is
    information the chef can act on. If stock moved or a cut-off passed, the
    basket is brought back in line with what Aimo says now, suggestions are
    attached for whatever it is short of, and it comes back as a draft to confirm
    again. Only a caller mistake, such as approving an unconfirmed basket,
    raises.
    """
    steps = steps if steps is not None else []
    basket = state.basket
    moment = now or datetime.now(timezone.utc)
    role = (approver_role or "").lower()
    if role not in ROLE_RANK:
        raise OrderingError(f"{approver_role!r} is not a role that can approve")
    if basket.status == "ordered":
        raise OrderingError("this basket has already been ordered")
    if basket.status != "quoted" or not basket.quote_jws:
        raise OrderingError("confirm the basket first: there is no signed price to approve")
    if basket.quote_expires and basket.quote_expires <= moment:
        basket.status = "draft"
        basket.quote_id = basket.quote_jws = basket.quote_expires = None
        basket.quote_verified = False
        raise OrderingError("the price confirmation expired; confirm again for a fresh one")

    needed = basket.policy.level if basket.policy else "chef"
    if needed == "blocked":
        raise OrderingError("this basket is blocked and cannot be approved")
    if role == "agent" and needed != AUTO_LEVEL:
        raise OrderingError(
            "the agent may only place an order your rules already cleared; "
            "this one needs a person")
    if ROLE_RANK[role] < LEVEL_RANK.get(needed, 1):
        # The chef cannot wave through what the policy sent to a manager. The
        # gateway cannot check this for us: it sees a signature, not a person.
        raise OrderingError(f"this basket needs {needed} approval, not {role}")

    mandate = kitchen.mandate_in_force(now=moment)
    approval = kitchen.sign_approval(basket.quote_id, role, now=moment)
    _step(steps, "sign_approval", f"{role} approved quote {basket.quote_id}")

    request = PlaceOrderRequest(
        quote_jws=basket.quote_jws,
        mandate_jws=mandate.jws,
        approval_jws=approval,
        idempotency_key=idempotency_key or f"{basket.basket_id}:{basket.quote_id}",
    )
    _step(steps, "place_order", f"quote {basket.quote_id} with mandate and approval", True)
    try:
        record = gateway.place_order(request)
    except GatewayRefused as exc:
        _step(steps, "order_refused",
              f"{exc.reason}: {exc.detail}" + ("  [SECURITY EVENT]" if exc.security_event else ""))
        if exc.reason in RECOVERABLE:
            return _adjust(state, exc, gateway, kitchen, steps)
        if exc.reason in {"quote_invalid", "quote_already_used"}:
            basket.status = "blocked" if exc.security_event else "draft"
            basket.quote_verified = False
        reply = f"Aimo would not accept the order: {_sentence(exc.detail)}"
        if exc.security_event:
            reply += " This one is logged as a security event."
        return OrderResponse(outcome="refused", reply=reply, basket=basket,
                             refusal=exc.rejection_model(), steps=steps)

    basket.order_id = record.order_id
    basket.status = "ordered"
    _step(steps, "order_placed",
          f"{record.order_id}, delivery {record.delivery_date}, "
          f"EUR {record.net_total:,.2f} net")
    reply = (f"Ordered. {record.order_id} for EUR {record.net_total:,.2f} net, "
             f"delivery {record.delivery_date}, approved by the {role}. "
             f"Aimo signed the confirmation.")
    return OrderResponse(outcome="ordered", reply=reply, basket=basket,
                         order=record, steps=steps)


def _adjust(state, refusal: GatewayRefused, gateway: GatewayClient,
            kitchen: KitchenSigner, steps: list[AgentStep]) -> OrderResponse:
    """The world moved: rebuild the basket from what Aimo says now.

    The chef asked for 1,000 litres and 400 are left. Refusing and stopping
    there leaves them to work out what to do; instead the line comes back at
    400, anything that could cover the other 600 is attached as a suggestion,
    and the basket is a draft again so confirming gets a fresh signed price.
    """
    basket = state.basket
    basket.quote_id = basket.quote_jws = basket.quote_expires = None
    basket.quote_verified = False
    # The alternatives on each line were priced for the quantity we no longer
    # have, so drop them; suggest_cover puts fresh ones back.
    for line in basket.lines:
        line.alternatives = []
    changes = agent.reprice(state, gateway, steps)
    suggestions = agent.suggest_cover(state, gateway, steps)

    if not basket.lines:
        reply = (f"The order could not go through: {_sentence(refusal.detail)} "
                 f"Nothing in the basket can be delivered for "
                 f"{basket.requested_date} any more, so it is empty. ")
        reply += (" ".join(suggestions) if suggestions
                  else "Try a later delivery date.")
        return OrderResponse(outcome="adjusted", reply=reply, basket=basket,
                             refusal=refusal.rejection_model(), steps=steps)

    reply = f"The order did not go through: {_sentence(refusal.detail)} "
    if changes:
        reply += "I have brought the basket in line with what Aimo says now: " + " ".join(changes) + " "
    reply += f"It is now EUR {basket.net_total:,.2f} net. "
    if suggestions:
        reply += "To cover the shortfall: " + " ".join(suggestions) + " "
    reply += "Confirm again for a fresh signed price."
    return OrderResponse(outcome="adjusted", reply=reply, basket=basket,
                         refusal=refusal.rejection_model(), steps=steps)
