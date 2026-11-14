"""Placing an order: the checks Aimo makes before it accepts one.

This is the guardrails half of the pitch. "Nothing is ordered without a human's
OK" is not a promise the kitchen makes about itself; Aimo refuses any order that
does not carry the kitchen's signed approval for that exact quote.

The checks, in this order, so the reason in the audit log is the useful one:

  1. the quote verifies against Aimo's own key, is for this agent, has not expired
  2. the quote has not already been ordered            -> quote_already_used
  3. the same idempotency key returns the same order   (a retry, not a new order)
  4. the mandate verifies against the key registered for that customer
  5. the mandate is for this agent and this customer
  6. the quote total is inside the per-order limit
  7. this order plus the week's orders are inside the weekly limit
  8. every line's category is one the mandate allows
  9. a signed approval names this quote, when the mandate requires approval
 10. delivery is checked again                          -> stock_changed

Valio confirmed there is no order API on their side and none is expected: "it is
enough that your solution can produce orders for human approval, they don't need
to be sent anywhere". So this is where an order comes to rest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable

from shared.schemas import (
    ApprovalPayload,
    CheckDeliveryItem,
    CheckDeliveryResult,
    MandatePayload,
    OrderRecord,
    OrderRejected,
    PlaceOrderRequest,
    QuotePayload,
)
from shared.signing import Signer, Verifier, VerificationError, read_payload_unverified

RecheckDelivery = Callable[[date, list[CheckDeliveryItem]], dict[str, CheckDeliveryResult]]


class OrderRefused(Exception):
    """An order Aimo will not accept. Carries the reason for the audit log."""

    def __init__(self, reason: str, detail: str, security_event: bool = False) -> None:
        super().__init__(detail)
        self.rejection = OrderRejected(reason=reason, detail=detail, security_event=security_event)


@dataclass
class OrderStore:
    """Orders Aimo has accepted.

    In memory for the hackathon. `weekly_net` is the only query that is not a
    lookup by id, and it is the one the mandate's weekly limit needs.
    """

    orders: dict[str, OrderRecord] = field(default_factory=dict)

    def add(self, record: OrderRecord) -> None:
        self.orders[record.order_id] = record

    def get(self, order_id: str) -> OrderRecord | None:
        return self.orders.get(order_id)

    def for_customer(self, customer: str) -> list[OrderRecord]:
        return [o for o in self.orders.values() if o.customer == customer]

    def weekly_net(self, customer: str, when: datetime) -> float:
        """Net already ordered this ISO week, Monday to Sunday.

        An ISO week rather than a rolling seven days, so the figure a kitchen
        sees in its rules screen is the same one the gateway enforces.
        """
        year, week, _ = when.isocalendar()
        total = 0.0
        for order in self.orders.values():
            if order.customer != customer or order.status != "placed":
                continue
            placed = order.placed_at
            if placed.tzinfo is None:
                placed = placed.replace(tzinfo=timezone.utc)
            o_year, o_week, _ = placed.isocalendar()
            if (o_year, o_week) == (year, week):
                total += order.net_total
        return round(total, 2)

    def forget_customer(self, customer: str) -> list[str]:
        """For the labelled demo reset only. Returns the ids it removed."""
        gone = [o.order_id for o in self.for_customer(customer)]
        for order_id in gone:
            self.orders.pop(order_id, None)
        return gone


def _verify_quote(token: str, agent_id: str, gateway_url: str,
                  own: Verifier, now: datetime) -> QuotePayload:
    try:
        payload = own.verify(token, issuer=gateway_url, audience=agent_id, now=now)
    except VerificationError as exc:
        # Our own signature failing means the token was altered after we signed
        # it, so this is a security event rather than something to retry.
        raise OrderRefused("quote_invalid", f"the quote does not verify: {exc}",
                           security_event=True) from exc
    try:
        return QuotePayload(**payload)
    except Exception as exc:        # noqa: BLE001
        raise OrderRefused("quote_invalid", f"the quote is not a quote: {exc}",
                           security_event=True) from exc


def _verify_mandate(token: str, customer: str, agent_id: str, gateway_url: str,
                    kitchen: Verifier, now: datetime) -> MandatePayload:
    try:
        payload = kitchen.verify(token, audience=gateway_url, now=now)
    except VerificationError as exc:
        raise OrderRefused("mandate_invalid", f"the mandate does not verify: {exc}",
                           security_event=True) from exc
    try:
        mandate = MandatePayload(**payload)
    except Exception as exc:        # noqa: BLE001
        raise OrderRefused("mandate_invalid", f"the mandate is malformed: {exc}",
                           security_event=True) from exc
    if mandate.sub != agent_id:
        raise OrderRefused("mandate_wrong_agent",
                           f"the mandate is for {mandate.sub}, not {agent_id}",
                           security_event=True)
    if mandate.customer != customer:
        raise OrderRefused("mandate_wrong_customer",
                           f"the mandate is for customer {mandate.customer}, "
                           f"the quote for {customer}", security_event=True)
    return mandate


def _verify_approval(token: str | None, quote: QuotePayload, gateway_url: str,
                     kitchen: Verifier, now: datetime) -> ApprovalPayload:
    if not token:
        raise OrderRefused("approval_missing",
                           "the mandate requires a human approval and none was sent")
    try:
        payload = kitchen.verify(token, audience=gateway_url, now=now)
    except VerificationError as exc:
        raise OrderRefused("approval_invalid", f"the approval does not verify: {exc}",
                           security_event=True) from exc
    try:
        approval = ApprovalPayload(**payload)
    except Exception as exc:        # noqa: BLE001
        raise OrderRefused("approval_invalid", f"the approval is malformed: {exc}",
                           security_event=True) from exc
    if approval.quote_jti != quote.jti:
        # An approval for one quote must never pay for another.
        raise OrderRefused("approval_for_another_quote",
                           f"the approval names quote {approval.quote_jti}, "
                           f"this order is for {quote.jti}", security_event=True)
    return approval


def _check_limits(quote: QuotePayload, mandate: MandatePayload, customer: str,
                  store: OrderStore, now: datetime) -> None:
    if quote.net_total > mandate.limits.max_net_per_order:
        raise OrderRefused(
            "over_order_limit",
            f"{quote.currency} {quote.net_total:,.2f} net is over the mandate's "
            f"{quote.currency} {mandate.limits.max_net_per_order:,.2f} per order")
    already = store.weekly_net(customer, now)
    if already + quote.net_total > mandate.limits.max_net_per_week:
        raise OrderRefused(
            "over_weekly_limit",
            f"{quote.currency} {already:,.2f} already ordered this week plus "
            f"{quote.net_total:,.2f} is over the mandate's "
            f"{mandate.limits.max_net_per_week:,.2f} a week")
    if mandate.allowed_categories:
        outside = sorted({l.category for l in quote.lines
                          if l.category and l.category not in mandate.allowed_categories})
        if outside:
            raise OrderRefused("category_not_allowed",
                               f"the mandate does not allow category {', '.join(outside)}")


def _recheck_delivery(quote: QuotePayload, recheck: RecheckDelivery) -> date | None:
    """Delivery is a snapshot, so it is checked again at the moment of ordering."""
    items = [CheckDeliveryItem(product_id=l.product_id,
                               quantity_sales_units=l.quantity_sales_units)
             for l in quote.lines]
    results = recheck(quote.requested_date, items)
    delivery_date: date | None = None
    for line in quote.lines:
        result = results.get(line.product_id)
        if result is None:
            raise OrderRefused("stock_changed",
                               f"{line.product_id} is no longer answered by Aimo")
        delivery = result.delivery
        if delivery.sales_block:
            raise OrderRefused("stock_changed", f"{line.product_id} is now blocked for sale")
        if delivery.past_closing_time:
            raise OrderRefused("past_closing_time",
                               f"the cut-off for {line.product_id} passed while the quote was open")
        if delivery.can_fulfil == "none":
            raise OrderRefused("stock_changed",
                               f"{line.product_id} can no longer be delivered by "
                               f"{quote.requested_date}")
        if delivery.can_fulfil == "partial" and delivery.fulfil_amount < line.quantity_sales_units:
            raise OrderRefused(
                "stock_changed",
                f"only {delivery.fulfil_amount:g} {line.sales_unit} of {line.product_id} "
                f"is left, the quote was for {line.quantity_sales_units:g}")
        if delivery.delivery_date and (delivery_date is None or delivery.delivery_date > delivery_date):
            delivery_date = delivery.delivery_date
    return delivery_date


def place_order(
    *,
    request: PlaceOrderRequest,
    agent_id: str,
    gateway_url: str,
    own_verifier: Verifier,
    kitchen_verifier: Verifier,
    recheck_delivery: RecheckDelivery,
    registry: Any,                  # gateway.quotes.QuoteRegistry
    store: OrderStore,
    signer: Signer,
    new_order_id: Callable[[], str],
    now: datetime | None = None,
) -> OrderRecord:
    """Accept the order, or raise OrderRefused with a reason worth logging."""
    moment = (now or datetime.now(timezone.utc)).replace(microsecond=0)

    quote = _verify_quote(request.quote_jws, agent_id, gateway_url, own_verifier, moment)
    customer = quote.sub.split(":", 1)[-1]

    # A retry of the same request returns the same order rather than a second one.
    existing_for_key = registry.order_for_key(request.idempotency_key)
    if existing_for_key:
        record = store.get(existing_for_key)
        if record and record.quote_id == quote.jti:
            return record
        raise OrderRefused("idempotency_key_reused",
                           "that idempotency key already answered a different order")

    already = registry.order_for_quote(quote.jti)
    if already:
        raise OrderRefused("quote_already_used",
                           f"quote {quote.jti} was already ordered as {already}")

    mandate = _verify_mandate(request.mandate_jws, customer, agent_id, gateway_url,
                              kitchen_verifier, moment)
    _check_limits(quote, mandate, customer, store, moment)

    # Verify whenever one is sent, not only when the mandate demands it. The
    # kitchen often has a person approve anyway, and throwing that away would
    # lose who signed off from the order record and from the audit trail. A
    # missing approval is only an error when the mandate requires one.
    approval = None
    if mandate.approval_required or request.approval_jws:
        approval = _verify_approval(request.approval_jws, quote, gateway_url,
                                    kitchen_verifier, moment)

    delivery_date = _recheck_delivery(quote, recheck_delivery)

    order_id = new_order_id()
    record = OrderRecord(
        order_id=order_id,
        quote_id=quote.jti,
        customer=customer,
        agent_id=agent_id,
        status="placed",
        placed_at=moment,
        requested_date=quote.requested_date,
        delivery_date=delivery_date,
        net_total=quote.net_total,
        vat_total=quote.vat_total,
        gross_total=quote.gross_total,
        currency=quote.currency,
        lines=list(quote.lines),
        approver_role=approval.approver_role if approval else None,
    )
    # Aimo signs the confirmation too, so the kitchen can prove what was accepted.
    record.confirmation_jws = signer.sign({
        "iss": gateway_url,
        "sub": quote.sub,
        "aud": agent_id,
        "jti": order_id,
        "iat": int(moment.timestamp()),
        "exp": int(moment.timestamp()) + 30 * 24 * 60 * 60,
        "quote_jti": quote.jti,
        "order_id": order_id,
        "requested_date": quote.requested_date.isoformat(),
        "delivery_date": delivery_date.isoformat() if delivery_date else None,
        "net_total": quote.net_total,
        "approver_role": approval.approver_role if approval else None,
    })
    store.add(record)
    registry.mark_used(quote.jti, request.idempotency_key, order_id)
    return record


def describe_failed_token(token: str | None) -> dict[str, Any]:
    """What a rejected token claimed, for the audit entry. Never trusted."""
    if not token:
        return {}
    payload = read_payload_unverified(token)
    return {k: payload.get(k) for k in ("iss", "sub", "aud", "jti", "quote_jti", "exp")
            if payload.get(k) is not None}
