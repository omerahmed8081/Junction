# Two briefs for whoever is awake first

Written at hour 1 so nobody waits for a conversation. **Read `docs/CONTRACTS.md` before you
write anything.** The field names in it are already agreed and already in code: changing one
breaks someone else's work, so raise it in the group rather than renaming locally.

Everything below is already in the repo and passing:

```
cd Backend
python tests/test_units.py     # 14 passing: the size rule on real products
python tests/test_money.py     #  7 passing: the totals, VAT once per line
```

---

## Brief 1: the gateway (for the full-stack developer)

**Goal: by hour 7, one request becomes a priced basket on screen.** Nothing else. No ordering,
no approval, no signatures: those are hours 8.5 and 10.

Build in this order, and stop at each line to check it.

### 1. The catalogue in Postgres (about 2 hours)

`Backend/Data/valio_aimo_junction26_product_data.json` is 84 MB and gitignored, so download it
from the portal. Load it once into one table. Columns worth having beyond the raw JSON:

| Column | Where from |
|---|---|
| `product_id`, `category`, `sales_unit`, `base_unit` | top level of each product |
| `name_fi`, `name_en`, `name_sv` | `synkkaData.names` |
| `search_text` (tsvector over the three names) | Postgres full-text, no vector search |
| `claims` (text[]) | `synkkaData.classifications` where `name == "nutritionalClaim"`, take each value's `id` |
| `labels` (text[]) | same, `packagingMarkedLabel` |
| `size_per_sales_unit`, `size_unit`, `size_source` | `shared.units.pack_size(product)`, computed at load time |
| `variable_weight` | `synkkaData.variableUnit` |

Check: `FREE_FROM_LACTOSE` in category `10400` gives 35 rows.

### 2. Category labels (about 30 minutes)

The 349 codes have no names. One Gemini pass over a sample of product names per code gives a
label table (`10400` -> "milk drinks"). Check the dozen biggest by hand. Publish it at
`GET /gateway/categories`, because the agent must never invent a label.

`python scripts/find_products.py --categories` lists the codes by size with an example each.

### 3. `POST /gateway/search_products` (about 1 hour)

Request and response are in `shared/schemas.py`. Filter on text, claims, category, pack size
and `deliverable_by`. Return `ProductSummary`: catalogue data plus the pack size, no price and
no stock.

Check: "lactose free milk" with the claim filter returns milk and **not** the chocolate or
coffee milks.

### 4. `POST /gateway/check_delivery` (about 1.5 hours)

Wrap `gateway/aimo_client.py`, which is written and ready. It already handles the Cloudflare
user agent, the three "unlimited" sentinels and the full / partial / none answer.

**The rule that must not slip:** the warehouse count never leaves the gateway. `can_fulfil` is
`full`, `partial` or `none`, and `fulfil_amount` is capped at what was asked for.

Check: `464306` for 1,000 units on 2026-11-17 gives `full`, net 1.01 per unit, delivery
2026-11-16, cutoff 2026-11-15T17:00.

### 5. Wire it to the agent and the basket (the rest)

The agent calls those two endpoints, then `shared.units.convert()` and
`shared.money.line_totals()`. The basket comes back as the JSON in `docs/CONTRACTS.md`
section 2.

**Hour 7 is done when:** typing "1,000 L lactose-free milk by Tuesday" shows a basket with
1,000 ST of `464306`, net EUR 1,010.00, VAT EUR 136.35, gross EUR 1,146.35, delivery
2026-11-16, and changing the quantity updates the totals.

### Three traps already found, so you don't rediscover them

1. **Cloudflare** rejects a script-like user agent with 403 and "error code: 1010". The client
   sends a browser one; don't remove it.
2. **`netPriceFound: false` is not an error.** It means no customer-specific price, so the
   catalogue price applies. 24 of the 35 milks are like that.
3. **A cutoff that has passed makes a product unbuyable**, whatever the catalogue says. On
   14 November that is 20 of the 35 lactose-free milks, including both 10 L Novoboxes. Filter
   on the cutoff **before** ranking.

### Decide with the group, not alone

- Any change to a field name in `docs/CONTRACTS.md`.
- Anything that would make the gateway read the kitchen's database. It must not: the two
  services talk over HTTP only, and that separation is a scored part of the pitch.

---

## Brief 2: the seed data (for whoever has LLM access)

**Goal: one demo restaurant's recipes and four weeks of plausible trading, so the running-low
suggestion has something real to work from.** No coding beyond writing JSON.

### Use the finder, not guesswork

Every ingredient must be a **real product id** whose price the API answers for. A made-up id
breaks the demo.

```
cd Backend
python scripts/find_products.py --text cream --category 10401
python scripts/find_products.py --text butter --claim FREE_FROM_LACTOSE
python scripts/find_products.py --text milk --category 10400 --price 2026-11-17
```

With `--price` each line is marked `OK` or `NO`: `NO` means a passed cutoff or nothing
deliverable, so don't build a recipe on it. Useful ones found already:

| Product | What it is | Pack |
|---|---|---|
| `464306` | Valio Arki Eila semi skimmed milk, lactose free | 1 L per ST |
| `461490` | Valio Keittiön whipping cream 3,3 dl, lactose free | 0.33 L per ST, 3.3 L per KI |
| `461548` | Valio coffee cream 10% 3,3 dl, lactose free | 0.33 L per ST, 3.3 L per KI |
| `463254` | Millac Cooking 15, 1 L, lactose free | 1 L per ST |

`461490` and `461548` are sold by the case, which is what makes beat 3 show real case
arithmetic. Milk alone does not: every orderable milk is 1 L per unit.

### What to write

`Backend/data/seed/recipes.json` - 5 to 10 dishes a Finnish restaurant would actually serve,
each ingredient a real product id, quantities **per portion in base units** (litres or
kilograms, matching the product's `size_unit`):

```json
[
  { "dish_id": "d1", "name": "Creamy salmon soup",
    "ingredients": [
      { "product_id": "461490", "quantity_per_portion": 0.08, "unit": "L" },
      { "product_id": "464306", "quantity_per_portion": 0.05, "unit": "L" }
    ] }
]
```

`Backend/data/seed/sales_history.json` - 28 days of dishes sold, one row per dish per day:

```json
[ { "date": "2026-10-18", "dish_id": "d1", "portions": 34 } ]
```

Make it believable rather than random: weekends busier than Mondays, a consistent weekday
pattern, and one dish quietly growing week on week (that becomes the "quantities are creeping
up" line in the pitch).

`Backend/data/seed/ledger.json` - opening stock and anything already on order:

```json
{ "stock": [ { "product_id": "461490", "quantity": 2.0, "unit": "L" } ],
  "on_order": [ { "product_id": "461490", "quantity": 2.0, "unit": "L",
                  "delivery_date": "2026-11-16" } ] }
```

Set the cream deliberately low, because beat 3 of the demo is "cream runs out Thursday, 2 L
already on order, order 10 L before the cutoff".

### Definition of done

- Every `product_id` appears in the catalogue and is marked `OK` by `--price`.
- Units match each product's own `size_unit`: never litres for a product measured in kilograms.
- Portion quantities are sane (0.08 L of cream in a bowl of soup, not 8 L).
- The files are valid JSON and committed.

Then, and only then, start on the slides.
