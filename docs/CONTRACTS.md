# Contracts: the shapes we pass between tracks

Draft for the hour 0 meeting. Read it together, change what you don't like, commit it,
then split up. After that a rename is a team decision, not a local one.

The code version is authoritative and already compiles:

| File | What it fixes |
|---|---|
| `Backend/shared/schemas.py` | every shape below, as pydantic models |
| `Backend/shared/units.py` | the one conversion function |
| `Backend/shared/money.py` | line and basket totals, so VAT is computed one way |
| `Backend/gateway/aimo_client.py` | the only module that calls Aimo's API |
| `Backend/tests/test_units.py` | 14 tests on real products, all passing |
| `Backend/tests/test_money.py` | 7 tests on the hour 0 figures, all passing |

## Repo layout: two services from the start

```
Backend/
  shared/     units.py  money.py  schemas.py      imported by both, imports neither
  gateway/    aimo_client.py  catalog.py  main.py  Aimo's side: own DB schema, own key
  kitchen/    agent.py  policy.py  ledger.py  main.py   the restaurant's side, same
  tests/      test_units.py  test_money.py
  scripts/    hour0_check.py
```

Two FastAPI apps, two Postgres schemas, two database users. They talk over HTTP only, and
`shared/` must never import from either, or the services stop being separable and the
data-ownership claim becomes a slide rather than a fact. Starting this way costs ten minutes;
splitting one app in two at hour 10 costs an evening.

## 0. The four decisions

1. **Model host: Gemini.** One client, in the kitchen service. It sees the request text and
   candidate product data. Never recipes, sales or stock.
2. **Money is net** (excluding VAT and deposits) everywhere a limit is checked. The basket
   shows net, VAT and gross separately, and all of it comes from `shared/money.py`.
   **VAT is computed once per line, from the line's net value** (`line_net x taxRate`),
   never by multiplying the API's per-unit `valueWithTax`, which is already rounded to
   cents: 1,000 x EUR 1.15 is EUR 3.65 more than EUR 1,010.00 plus 13.5%. A basket's VAT is the
   **sum of its line VATs**, not VAT recomputed on the basket net; the two can differ by a
   cent. `money.py` uses `Decimal` with half a cent always rounding up, because Python's
   `round()` rounds half to even and would disagree with a hand calculation.
3. **Sizes and quantities come from `shared/units.py`.** Nobody computes a quantity anywhere else.
4. **Aimo's API is reached only through `gateway/aimo_client.py`**, and only the gateway imports it.

## 1. Frontend to agent

```
POST /api/chat   { "message": "1,000 L lactose-free milk by Tuesday", "basket_id": null }
             ->  { "reply": "...", "basket": Basket, "steps": [AgentStep] }
```

`basket_id` is set when the chef is still editing an existing basket. `steps` is the trace
the Activity tab shows; each step says whether it crossed the gateway.

Editing a line comes from the basket UI, not the chat:

```
PATCH /api/basket/{basket_id}
  { "edits": [ { "product_id": "464306", "quantity_sales_units": 1200 } ] }
  { "edits": [ { "product_id": "464306", "swap_to_product_id": "464401" } ] }
  { "edits": [ { "product_id": "464306", "remove": true } ] }
            -> { "reply": "...", "basket": Basket, "steps": [AgentStep] }
```

Each edit is re-priced, delivery is re-checked and the policy preview is recomputed. An edit
to a `quoted` basket puts it back to `draft` and clears `quote_id`, `quote_jws`,
`quote_expires` and `quote_verified`.

## 2. The basket line

This is what track C binds to and track B fills in. Full field list in `schemas.py`;
these are the ones that matter, with the real hour 0 numbers:

```json
{
  "product_id": "464306",
  "name": "Valio Hyvä suomalainen Arki Eila semi skimmed milk 1 l, lactose free",

  "need_value": 1000, "need_unit": "L",

  "sales_unit": "ST", "base_unit": "ST", "base_units_per_sales_unit": 1,
  "size_per_base_unit": 1.0, "size_per_sales_unit": 1.0,
  "size_unit": "L", "size_source": "structured",
  "quantity_sales_units": 1000,
  "delivered_amount": 1000, "overshoot": 0,

  "price_per_sales_unit": 1.01, "price_unit": "ST",
  "price_per_comparison_unit": 1.01, "comparison_unit": "L",
  "line_net": 1010.00, "line_vat": 136.35, "line_gross": 1146.35,
  "deposit_units": 0, "deposit_note": null,

  "delivery": { "can_fulfil": "full", "fulfil_amount": 1000, "sales_unit": "ST",
                "delivery_date": "2026-11-16",
                "closing_time": "2026-11-15T17:00:00+02:00",
                "past_closing_time": false, "sales_block": false },

  "origin": "fi", "claims": ["FREE_FROM_LACTOSE"], "labels": ["GOODS_FROM_FINLAND_BLUE_SWAN"],
  "delivery_reliability": 1.0, "carbon_kg_per_kg": null,

  "reason": "Cheapest lactose-free milk per litre (EUR 1.01/L); full amount deliverable Monday",
  "flags": [],

  "alternatives": [
    { "product_id": "464401", "name": "Valio Arki Eila skimmed milk 1 l, lactose free",
      "quantity_sales_units": 1000, "sales_unit": "ST", "line_net": 1100.00,
      "price_per_comparison_unit": 1.10, "comparison_unit": "L",
      "reason": "Skimmed instead of semi-skimmed, EUR 90 more, same Monday delivery",
      "flags": [] }
  ]
}
```

Rules track C can rely on:

- **Every money field is a number in EUR, already rounded.** The frontend formats, never computes.
- **`price_per_comparison_unit` is rounded by Aimo**, so quantity x that figure will not
  equal `line_net` (the Novobox is EUR 1.57/L but 50 x EUR 31.31 = EUR 1,565.50). Label it as a
  comparison price, and never total with it.
- **Only rank products whose `comparison_unit` matches the need's unit.** 34 of the 35
  lactose-free milks report per L and one per KG.
- **`null` means unknown and is shown as "unknown".** Never as 0. `delivery_reliability` and
  `carbon_kg_per_kg` are often null, and that must not look like a bad score.
- **`size_source`** is one of `structured`, `structured_case`, `name`, `derived`, `not_needed`,
  `unknown`. Show a quiet note for `name` and `derived` ("size from product name").
- **`overshoot` above 0** means we are delivering more than asked for, because packs don't
  divide evenly. Show it; don't hide it.
- **`flags`** are short strings to render as chips: `size_from_product_name`,
  `size_derived_from_price`, `size_disagrees_with_price`, `variable_weight`, `partial_split`,
  `past_closing_time`.
- **`alternatives` are whole objects, not ids**, so a swap needs no extra call: each carries
  its own name, quantity, net total, comparison price and a one-line reason.

## 3. The basket

```json
{ "basket_id": "b_...", "source": "chat", "requested_date": "2026-11-17",
  "status": "draft",
  "lines": [ ... ],
  "net_total": 1010.00, "vat_total": 136.35, "gross_total": 1146.35, "currency": "EUR",
  "policy": { "level": "manager", "reasons": ["Over the EUR 500 chef limit"], "binding": false },
  "quote_id": null, "quote_jws": null, "quote_expires": null, "quote_verified": false,
  "order_id": null, "demo_scenario": null }
```

- `status`: `draft` -> `quoted` -> `pending_approval` -> `approved` -> `ordered`, plus
  `rejected` and `blocked`.
- `policy.binding` is false on a draft (the preview that tells the chef a manager will be
  needed) and true once it is decided on the signed quote.
- Any edit to a `quoted` basket puts it back to `draft` and clears the quote fields.
- `demo_scenario` non-null means the scenario switch is on: the frontend shows the
  DEMO SCENARIO banner.

## 4. Agent to gateway

Two calls in block 1. Both are FastAPI endpoints on the gateway service; MCP is generated
from the same OpenAPI spec.

```
GET  /gateway/categories
  -> { "categories": [ { "category": "10400", "label": "milk drinks", "product_count": 91 } ] }

POST /gateway/search_products
  { "text": "lactose free milk", "claims": ["FREE_FROM_LACTOSE"],
    "category": null, "category_label": null, "brand": null,
    "pack_size": null, "pack_size_unit": "",
    "deliverable_by": "2026-11-17", "exclude_variable_weight": true, "limit": 20 }
  -> { "products": [ProductSummary], "total_matched": 35 }
```

`ProductSummary` carries no price and no stock: it is catalogue data plus the pack size our
size rule worked out, so the agent can convert the need before asking about price.

**Category labels belong to the gateway.** The 349 codes have no names in the data, so the
gateway builds the label table and publishes it at `GET /gateway/categories`. The agent may
send a `category` code, a `category_label` copied from that list, or neither, in which case
the gateway resolves the category from `text`. The agent never invents a label string.

`pack_size` + `pack_size_unit` rather than litres only, because a kilogram pack is just as
common. `deliverable_by` lets the gateway drop products whose cutoff has already passed,
which on 14 November is 20 of the 35 lactose-free milks.

```
POST /gateway/check_delivery
  { "requested_date": "2026-11-17",
    "items": [ { "product_id": "464306", "quantity_sales_units": 1000 } ] }
  -> { "requested_date": "2026-11-17", "checked_at": "...",
       "results": [ { "product_id": "464306",
                      "price": { "price_per_sales_unit": 1.01, "comparison_price": 1.01,
                                 "comparison_unit": "L", "tax_rate": 13.5,
                                 "customer_specific": true, "unavailable_reason": null },
                      "delivery": { "can_fulfil": "full", "fulfil_amount": 1000, ... } } ] }
```

**What the gateway never returns:** the warehouse count. `can_fulfil` is `full`, `partial` or
`none`, and `fulfil_amount` is capped at what was asked for. That cap is the whole point, so
don't "helpfully" pass `availableAmount` through while debugging.

## 5. The conversion function

```python
from shared.units import convert

c = convert(product, 1000, "L")          # product = a row of the catalogue JSON
c.ok                     # False when there is no usable size
c.reason                 # "size_unknown" | "variable_weight" | ...
c.quantity_sales_units   # 1000 for 464306, 50 for the 10 L Novobox 463038
c.size_source            # how we found the pack size
c.delivered_amount, c.overshoot
```

Optional fourth argument `derived_size_per_sales_unit`, from
`gateway.aimo_client.derived_size_per_sales_unit(raw_price)`, is the last-resort fallback for a
product whose size is nowhere else. It is flagged when used.

`c.ok is False` is normal, not an error: the basket line then says "size unknown, enter a
quantity in cases" and the policy engine blocks ordering it automatically.

## 6. Names worth not re-inventing

| Thing | Name we use | Not |
|---|---|---|
| net value of a line | `line_net` | `total`, `netTotal`, `sum` |
| how many we order | `quantity_sales_units` | `qty`, `amount`, `count` |
| the unit we buy in | `sales_unit` | `unit`, `uom` |
| stock answer | `can_fulfil` | `available`, `inStock` |
| dietary claim | `claims` (the claim `id`, e.g. `FREE_FROM_LACTOSE`) | `tags`, `certificates` |
| VAT rate | `tax_rate_percent` (13.5 means 13.5%) | `tax_rate`, `vat` |
| packaging mark | `labels` | `certificates` |
| why this product | `reason` | `explanation`, `note` |

Dates are ISO (`2026-11-17`), timestamps are ISO with an offset, money is a number, and the
currency is `EUR` in one place on the basket.

## 7. Setup

PowerShell:

```
cd Backend
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env                              # then fill in JUNCTION_API_KEY
python tests/test_units.py                          # 14 passing
python tests/test_money.py                          # 7 passing
python scripts/hour0_check.py 2026-11-17            # re-run the real API any time
```

bash (Linux, macOS, WSL):

```
cd Backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 tests/test_units.py && python3 tests/test_money.py
JUNCTION_API_KEY=... python3 scripts/hour0_check.py 2026-11-17
```
