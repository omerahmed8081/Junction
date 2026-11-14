"""The shapes passed between the four tracks.

CONTRACT - agreed hour 0. Field names are what the frontend binds to and what the
agent fills in, so a rename here is a team decision, not a local one.
See docs/CONTRACTS.md for the prose version and a worked example.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

# EUR, 2 decimals, always the net goods value unless the field name says otherwise.
# Every figure is produced by app/money.py: VAT once per line, from the line net.
Money = float
CanFulfil = Literal["full", "partial", "none"]
SizeSource = Literal["structured", "structured_case", "name", "derived", "not_needed", "unknown"]
BasketStatus = Literal["draft", "quoted", "pending_approval", "approved", "ordered", "rejected", "blocked"]
ApprovalLevel = Literal["auto_ok", "chef", "manager", "blocked"]


# ---------------------------------------------------------------- gateway: search

class ProductSummary(BaseModel):
    """One candidate from the catalogue. No price, no stock: those are separate calls."""

    product_id: str
    name: str
    vendor: str | None = None
    category: str
    category_label: str | None = None          # from our own label table
    sales_unit: str
    base_unit: str
    base_units_per_sales_unit: float
    allowed_lot_size: float | None = None      # smallest order step, in sales units
    size_per_sales_unit: float | None = None   # litres or kilograms
    size_unit: str = ""                        # "L" | "KG" | ""
    size_source: SizeSource = "unknown"
    claims: list[str] = Field(default_factory=list)      # e.g. ["FREE_FROM_LACTOSE"]
    labels: list[str] = Field(default_factory=list)      # e.g. ["GOODS_FROM_FINLAND_BLUE_SWAN"]
    allergens: list[str] = Field(default_factory=list)
    origin: str | None = None
    gtin: str | None = None
    variable_weight: bool = False
    delivery_reliability: float | None = None  # None means unknown, never 0
    carbon_kg_per_kg: float | None = None


class SearchRequest(BaseModel):
    """What the agent asks the gateway for.

    The agent does not need to know the gateway's category labels: it may send a
    `category` code, a `category_label` from GET /gateway/categories, or neither,
    in which case the gateway resolves the category from `text` itself.
    """

    text: str | None = None
    claims: list[str] = Field(default_factory=list)
    category: str | None = None
    category_label: str | None = None
    brand: str | None = None
    pack_size: float | None = None             # e.g. 10 with pack_size_unit "L"
    pack_size_unit: str = ""                   # "L" | "KG"
    exclude_variable_weight: bool = True
    deliverable_by: date | None = None         # drop products whose cutoff has passed
    limit: int = 20


class CategoryLabel(BaseModel):
    """One row of the gateway's label table for the 349 unnamed category codes."""

    category: str
    label: str
    product_count: int


class CategoryListResponse(BaseModel):
    categories: list[CategoryLabel]


class SearchResponse(BaseModel):
    products: list[ProductSummary]
    total_matched: int


# ------------------------------------------------- gateway: price and delivery

class DeliveryAnswer(BaseModel):
    """What the gateway will say about stock. Never a raw warehouse count."""

    can_fulfil: CanFulfil
    fulfil_amount: float = 0.0        # capped at what was asked for
    sales_unit: str = ""
    delivery_date: date | None = None
    closing_time: datetime | None = None
    past_closing_time: bool = False
    sales_block: bool = False
    # False when Aimo answered with a "we do not know" sentinel (>= 999999).
    # Any quantity may be ordered, but no stock is confirmed, so the line is
    # flagged and the policy does not treat it as a certainty.
    availability_known: bool = True


class PriceAnswer(BaseModel):
    price_per_sales_unit: Money | None = None
    price_unit: str = ""                       # the sales unit the price applies to
    price_with_tax_per_sales_unit: Money | None = None
    comparison_price: Money | None = None      # Aimo's own price per comparison_unit
    comparison_unit: str = ""                  # "L" | "KG"
    tax_rate_percent: float | None = None      # the API's tax.taxRate, e.g. 13.5
    valid_to: datetime | None = None
    customer_specific: bool = False            # the API's netPriceFound
    unavailable_reason: str | None = None       # set only when there is no usable price


class CheckDeliveryItem(BaseModel):
    product_id: str
    quantity_sales_units: float


class CheckDeliveryRequest(BaseModel):
    requested_date: date
    items: list[CheckDeliveryItem]


class CheckDeliveryResult(BaseModel):
    product_id: str
    price: PriceAnswer
    delivery: DeliveryAnswer


class CheckDeliveryResponse(BaseModel):
    requested_date: date
    checked_at: datetime
    results: list[CheckDeliveryResult]


# ------------------------------------------------------------------- the basket

class Alternative(BaseModel):
    """Another product for this line, ready to render and to swap to.

    An id alone is not enough: the frontend has to show why it is worth a look
    ("50 packs to carry instead of 1,000 cartons") without another round trip.
    """

    product_id: str
    name: str
    quantity_sales_units: float
    sales_unit: str
    line_net: Money
    price_per_comparison_unit: Money | None = None
    comparison_unit: str = ""
    delivery: "DeliveryAnswer | None" = None
    reason: str = ""
    flags: list[str] = Field(default_factory=list)


class BasketLine(BaseModel):
    """One line. Every number here was computed in code, never written by the model."""

    product_id: str
    name: str

    # what was asked for
    need_value: float
    need_unit: str                              # "L" | "KG" | a sales unit

    # how we got from the need to a quantity (app/units.py)
    sales_unit: str
    base_unit: str
    base_units_per_sales_unit: float
    size_per_base_unit: float | None = None
    size_per_sales_unit: float | None = None
    size_unit: str = ""
    size_source: SizeSource = "unknown"
    quantity_sales_units: float = 0.0
    delivered_amount: float = 0.0               # in need_unit
    overshoot: float = 0.0                      # in need_unit

    # money, all net unless the name says otherwise
    price_per_sales_unit: Money | None = None
    price_unit: str = ""
    price_per_comparison_unit: Money | None = None   # Aimo's, rounded: never total with it
    comparison_unit: str = ""
    line_net: Money = 0.0
    line_vat: Money = 0.0
    line_gross: Money = 0.0
    deposit_units: float = 0.0                  # count only: the price API returns no value
    deposit_note: str | None = None

    delivery: DeliveryAnswer | None = None
    origin: str | None = None
    claims: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    delivery_reliability: float | None = None
    carbon_kg_per_kg: float | None = None

    reason: str = ""                            # why this product, in one line
    flags: list[str] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list)   # ranked, best first


class PolicyDecision(BaseModel):
    level: ApprovalLevel
    reasons: list[str] = Field(default_factory=list)
    binding: bool = False                       # False while it is only a draft preview


class Basket(BaseModel):
    basket_id: str
    source: str = "chat"                        # chat | suggestion:low_stock | ...
    requested_date: date | None = None
    status: BasketStatus = "draft"
    lines: list[BasketLine] = Field(default_factory=list)

    net_total: Money = 0.0
    vat_total: Money = 0.0
    gross_total: Money = 0.0
    currency: str = "EUR"

    policy: PolicyDecision | None = None

    quote_id: str | None = None
    quote_jws: str | None = None
    quote_expires: datetime | None = None
    quote_verified: bool = False

    order_id: str | None = None
    demo_scenario: str | None = None            # set when the scenario switch is on


# ------------------------------------------------- quotes, mandates, approvals
# The three signed tokens. These models are the claim names both services agree
# on; shared/signing.py does the signing and the verifying.


class QuoteLine(BaseModel):
    """One line inside a signed quote: what Aimo states about this product."""

    product_id: str
    name: str
    gtin: str | None = None
    quantity_sales_units: float
    sales_unit: str
    delivered_amount: float
    delivered_unit: str                        # "L" | "KG" | the sales unit
    size_source: SizeSource = "unknown"
    price_per_sales_unit: Money
    price_per_comparison_unit: Money | None = None
    comparison_unit: str = ""
    line_net: Money
    line_vat: Money
    line_gross: Money
    tax_rate_percent: float | None = None
    delivery: DeliveryAnswer
    origin: str | None = None
    claims: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    category: str | None = None                # the mandate is checked against this


class QuotePayload(BaseModel):
    """What Aimo signs at confirm time. "Signed price confirmation" in the UI."""

    iss: str                                   # the gateway
    sub: str                                   # customer:123
    aud: str                                   # agent:kitchen-demo
    jti: str                                   # the quote id, and it is single use
    iat: int
    exp: int                                   # min(price validTo, cutoff, now + 2 h)
    requested_date: date
    lines: list[QuoteLine]
    net_total: Money
    vat_total: Money
    gross_total: Money
    currency: str = "EUR"
    price_checked_at: datetime
    delivery_checked_at: datetime


class MandateLimits(BaseModel):
    max_net_per_order: Money
    max_net_per_week: Money
    currency: str = "EUR"
    basis: str = "net goods value excluding VAT and deposits"


class MandatePayload(BaseModel):
    """What the kitchen signs once, naming the outer limit Aimo will enforce."""

    iss: str                                   # the kitchen service
    sub: str                                   # agent:kitchen-demo
    aud: str                                   # the gateway
    customer: str
    jti: str
    iat: int
    exp: int
    limits: MandateLimits
    allowed_categories: list[str] = Field(default_factory=list)
    approval_required: bool = True


class ApprovalPayload(BaseModel):
    """What the kitchen signs when a human presses Approve, for one quote."""

    iss: str
    aud: str                                   # the gateway
    jti: str
    quote_jti: str                             # the quote being approved, and only that one
    approver_role: str                         # "chef" | "manager"
    approved_at: int
    iat: int
    exp: int


# ------------------------------------------------------- the order endpoints


class QuoteLineRequest(BaseModel):
    product_id: str
    quantity_sales_units: float


class CreateQuoteRequest(BaseModel):
    agent_id: str
    requested_date: date
    lines: list[QuoteLineRequest]


class CreateQuoteResponse(BaseModel):
    quote_id: str
    quote_jws: str
    expires_at: datetime
    net_total: Money
    vat_total: Money
    gross_total: Money
    currency: str = "EUR"
    lines: list[QuoteLine]
    # Set when a line could not be quoted at the quantity asked for, so the
    # kitchen can show why rather than silently quoting something else.
    refused: list[dict[str, str]] = Field(default_factory=list)


class PlaceOrderRequest(BaseModel):
    quote_jws: str
    mandate_jws: str
    approval_jws: str | None = None            # required when the mandate says so
    idempotency_key: str


class OrderRecord(BaseModel):
    order_id: str
    quote_id: str
    customer: str
    agent_id: str
    status: Literal["placed", "rejected"]
    placed_at: datetime
    requested_date: date
    delivery_date: date | None = None
    net_total: Money
    vat_total: Money
    gross_total: Money
    currency: str = "EUR"
    lines: list[QuoteLine] = Field(default_factory=list)
    approver_role: str | None = None
    confirmation_jws: str | None = None        # Aimo signs the confirmation too


class OrderRejected(BaseModel):
    """Why an order was refused, in a form the audit log and the UI can both use."""

    reason: str                                # a stable code, e.g. quote_already_used
    detail: str
    security_event: bool = False


# ------------------------------------------------------------- chat and the agent

class AgentStep(BaseModel):
    """One row of the trace, written by the decorator on every tool call."""

    step: str
    detail: str = ""
    crossed_gateway: bool = False               # drives the "What Aimo sees" toggle
    at: datetime


class ChatRequest(BaseModel):
    message: str
    basket_id: str | None = None                # to keep editing an existing basket


class ApproveRequest(BaseModel):
    """Who is approving. The policy decides what their role is allowed to pass."""

    approver_role: Literal["chef", "manager", "owner"] = "manager"


class OrderResponse(BaseModel):
    """What approving produced. All three outcomes are answers, not failures.

    * `ordered`  - Aimo accepted it; `order` is the record and its signed confirmation.
    * `adjusted` - something moved (stock fell, a cut-off passed), so the basket was
      brought back in line with what Aimo says now and is a draft again. `reply`
      says what changed, and the lines may carry suggestions to cover the gap.
    * `refused`  - Aimo said no for a reason the basket cannot fix itself, such as
      a mandate limit. `refusal` says which.
    """

    outcome: Literal["ordered", "adjusted", "refused"]
    reply: str
    basket: Basket
    order: OrderRecord | None = None
    refusal: OrderRejected | None = None
    steps: list["AgentStep"] = Field(default_factory=list)


class MandateRequest(BaseModel):
    """What the owner sets in the Agent rules tab. Omitted fields keep the default."""

    max_net_per_order: Money | None = None
    max_net_per_week: Money | None = None
    allowed_categories: list[str] | None = None
    approval_required: bool = True
    days: int | None = None


class MandateView(BaseModel):
    """The mandate in force, for the rules screen. The JWS is included so the
    trace can show exactly what was signed."""

    mandate_id: str
    agent_id: str
    customer: str
    expires_at: datetime
    limits: MandateLimits
    allowed_categories: list[str] = Field(default_factory=list)
    approval_required: bool
    mandate_jws: str


class LineEdit(BaseModel):
    """One change to a basket line, from the basket UI rather than the chat.

    Exactly one of the three is set. Any accepted edit puts a quoted basket back
    to draft and clears its quote fields.
    """

    product_id: str
    quantity_sales_units: float | None = None   # the chef typed a new quantity
    swap_to_product_id: str | None = None       # the chef picked an alternative
    remove: bool = False


class BasketEditRequest(BaseModel):
    edits: list[LineEdit]
    requested_date: date | None = None          # changing the date re-checks delivery


class ChatResponse(BaseModel):
    reply: str                                  # one or two sentences for the chat pane
    basket: Basket | None = None
    steps: list[AgentStep] = Field(default_factory=list)
