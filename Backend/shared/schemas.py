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
