# Brief 1 handover: the gateway and the chat-to-basket flow

Written Saturday 14 November 2026, 12:35 (hour 9.5). Covers all five steps of Brief 1 in `docs/NIGHT_SHIFT.md`.

**Status: done.** Typing "1,000 L lactose-free milk by Tuesday" returns a basket with 1,000 ST of `464306`, net EUR 1,010.00, VAT EUR 136.35, gross EUR 1,146.35, delivery 2026-11-16, and changing the quantity updates the totals. This was checked against the live Aimo API and the Aiven database, with Gemini doing the parse.

Not yet committed: three shared files changed (section 6), so read that first.

---

## 1. What exists now

| Step | What was built | Checked how |
|---|---|---|
| 1. Catalogue in Postgres | `gateway.products` on Aiven: 17,546 rows, 77 MB | `FREE_FROM_LACTOSE` in `10400` gives 35 rows |
| 2. Category labels | `gateway.category_labels`: 349 labels from Gemini; `GET /gateway/categories` | Top 15 read by hand; `10400` set to "milk drinks" and marked checked |
| 3. `POST /gateway/search_products` | Text, claims, category, brand, pack size, `deliverable_by` | Live: 34 lactose-free milks found; 12 left with the cutoff filter |
| 4. `POST /gateway/check_delivery` | Price and full / partial / none for a shortlist | Live: `464306` x 1,000 gives full, 1.01 per ST, delivery 16 Nov, cutoff 15 Nov 17:00 |
| 5. Agent and basket | `POST /api/chat`, `PATCH /api/basket/{id}`, `GET /api/basket/{id}` | Live: the hour 7 basket, quantity edit, swap, empty result, partial delivery |

Tests, all passing: `test_units.py` 14, `test_money.py` 7, `test_delivery.py` 9, `test_agent.py` 16,
`test_timeparse.py` 10 (added in review, see the note below).

### Reviewed at hour 10.5: one bug fixed

Every timestamp the mock sends uses an offset with no colon (`"+0200"`), and
`datetime.fromisoformat` only accepts that from **Python 3.11**. On 3.10 the delivery check
raised `ValueError` on `closingTime`, and `delivery._when()` silently returned `None` for a
price validity date. It passed here only because this venv is 3.12.

Fixed with `shared/timeparse.py`, now used by `gateway/aimo_client.py` and
`gateway/delivery.py`. `parse_api_datetime()` also returns `None` for the year-9999
"no end date" sentinel, which answers the frontend note in section 7. All 56 tests pass on
3.10 and on 3.12. **Never call `fromisoformat` on a string from the API again; go through
`shared.timeparse`.**

---

## 2. How to run it

One-time setup, from `Backend/`:

```
python3 -m venv .venv && source .venv/bin/activate      # PowerShell: .venv\\Scripts\\Activate.ps1
python -m pip install -r requirements.txt
cp .env.example .env                                    # then fill it in, see below
```

`.env` needs:

| Key | For |
|---|---|
| `DATABASE_URL` | The Aiven service URI (ask Chamath; never commit it) |
| `JUNCTION_API_KEY` | Aimo's mock API |
| `GEMINI_API_KEY` | The request parser and the label script |
| `GEMINI_MODEL` | Optional; default `gemini-3.5-flash-lite` |
| `GATEWAY_URL` | Optional; default `http://localhost:8001` |
| `KITCHEN_PARSER` | Optional; `rules` skips Gemini and uses the rule-based parser |
| `CHEF_LIMIT_EUR` | Optional; default 500 |

Run the tests (no network, database or model needed):

```
python tests/test_units.py && python tests/test_money.py
python tests/test_delivery.py && python tests/test_agent.py
```

Start both services, each in its own terminal with the venv active:

```
uvicorn gateway.main:app --reload --port 8001
uvicorn kitchen.main:app --reload --port 8002
```

- Gateway docs: `http://localhost:8001/docs`
- Kitchen docs (what the frontend calls): `http://localhost:8002/docs`

The catalogue and labels are already loaded on Aiven, so nobody needs to run the load scripts again. If the database is ever rebuilt:

```
python scripts/load_catalogue.py                 # about 5 minutes over the network
python scripts/label_categories.py --from-file   # reloads Data/category_labels.json, no Gemini call
```

---

## 3. Files

### New

| File | What it does |
|---|---|
| `scripts/load_catalogue.py` | Loads the product JSON into `gateway.products`; `--dry-run` needs no database |
| `scripts/label_categories.py` | Labels the 349 categories with Gemini; `--show`, `--set`, `--export`, `--from-file` |
| `Data/category_labels.json` | The labels, so they can be reloaded without Gemini |
| `gateway/catalog.py` | Search and category resolution over the catalogue |
| `gateway/delivery.py` | Turns Aimo's raw price and availability into the contract's response |
| `gateway/main.py` | The gateway FastAPI app |
| `kitchen/gateway_client.py` | The kitchen's HTTP client for the gateway |
| `kitchen/need.py` | Sentence to structured need: Gemini, with a rule-based fallback |
| `kitchen/agent.py` | Search, convert, check, rank, build the basket, apply edits |
| `kitchen/main.py` | The kitchen FastAPI app |
| `tests/test_delivery.py`, `tests/test_agent.py` | 9 and 16 tests on the recorded hour 0 data |
| `tests/fixtures/milk_summaries.json` | The 35 real lactose-free milks, for the agent tests |

### Changed

| File | Change |
|---|---|
| `shared/schemas.py` | `ProductSummary` gained one optional field, `allowed_lot_size` |
| `shared/units.py` | One function added, `convert_summary()`; nothing existing was touched |
| `requirements.txt` | `psycopg[binary,pool]` for the connection pool |
| `.gitignore` | `.venv/` added |

---

## 4. The endpoints

### Gateway (port 8001)

| Endpoint | Returns |
|---|---|
| `GET /health` | `{"status": "ok", "products": 17546}` |
| `GET /gateway/categories` | Every category code with its label and product count |
| `POST /gateway/search_products` | `ProductSummary` list: catalogue data and pack size, no price, no stock |
| `POST /gateway/check_delivery` | Price and delivery answer per item; never the warehouse count |

### Kitchen (port 8002)

| Endpoint | Returns |
|---|---|
| `POST /api/chat` | `{reply, basket, steps}`; with `basket_id` set, the need is added as a new line |
| `PATCH /api/basket/{basket_id}` | Same shape, after a quantity change, swap or remove |
| `GET /api/basket/{basket_id}` | The basket |

Try it:

```json
POST /api/chat
{"message": "1,000 L lactose-free milk by Tuesday"}
```

```json
PATCH /api/basket/<basket_id>
{"edits": [{"product_id": "464306", "quantity_sales_units": 1200}]}
{"edits": [{"product_id": "464306", "swap_to_product_id": "464401"}]}
{"edits": [{"product_id": "464306", "remove": true}]}
```

---

## 5. How a request becomes a basket

| Step | Done by | Crosses the gateway |
|---|---|---|
| Parse the sentence into a need | Gemini (`need.py`), rules as fallback | No |
| Search the catalogue | Gateway | Yes |
| Convert the need to a quantity per candidate | `shared.units.convert_summary()` | No |
| Price and delivery for the candidates | Gateway, which calls Aimo | Yes |
| Drop what can't be ordered, then rank | `agent.py` | No |
| Build the line, two alternatives, totals | `agent.py`, `shared.money` | No |
| Policy preview | `agent.py` | No |

Every call writes an `AgentStep` with `crossed_gateway` set, which is what the "What Aimo sees" toggle needs.

A product is orderable only if it has a price, is not sales-blocked, its cutoff has not passed, something can be delivered by the date, and its size is known in the unit asked for.

Ranking order: plain products before off-request ones, full delivery before partial, then Aimo's comparison price, then delivery reliability (unknown never beats known), then carbon footprint.

---

## 6. Changes to agreed files (please read before I commit)

1. **`ProductSummary.allowed_lot_size`** (new, optional). The kitchen needs it to round quantities.
2. **`shared.units.convert_summary()`** (new). `convert()` takes a raw catalogue row, and the kitchen never has one: it only gets a `ProductSummary`. Without this the kitchen would have to read the catalogue itself, which breaks the two-service rule. It was checked against `convert()` on all 17,546 products for four different needs, with zero mismatches.
3. **`gateway/catalog.py`** returns the new field.

All three are additive. Nothing that existed was renamed or removed.

Also: `kitchen/need.py` and `kitchen/agent.py` are in the agent track. If you have your own version, let's compare before either overwrites the other. `parse_with_gemini()` is one function and can be replaced freely as long as it returns a `ParsedNeed`.

---

## 7. Things found along the way

### Data

- **Four of the 35 lactose-free "milks" in `10400` are coffee milks, and several are sour milks.** Aimo files them in the same category, so no category filter separates them.
- **English names join words: "milkdrink".** Full-text search for "milk" missed five products until the search was changed to also match the start of a word. This is why the search first returned 30, then 34. The 35th is a latte drink with no "milk" in its name.
- **Both 10 L Novoboxes (`463038`, `464179`) are past their cutoff** for delivery on the 17th. They can't be the alternative in beat 1 with this date.
- **Of the orderable milks, two are sold by the case:** `463779` (15 x 2.5 dl, 3.75 L per case) and `463203` (portion milk). `463779` is the one that shows a real unit conversion: 1,000 L is 267 cases.
- **The mock looks stable:** `462775` had 51 units at 03:40 and still had 51 at 10:54.
- **`valid_to` of year 9999 means "no end date".** The frontend should not show it as a date.

### API behaviour

- **A passed cutoff does not change `can_fulfil`.** The Novobox answers `full` with `past_closing_time: true`. Anything reading the gateway must treat that flag as "cannot be ordered". The agent does.
- **A partial answer reveals the stock.** Asking for 1,000 and getting 51 back tells you there are 51. This is inherent in full / partial / none and is the known limit already in the architecture.
- **On Gemini 3.5 Flash-Lite, `temperature`, `top_p` and `top_k` are deprecated**, and thinking is set by `thinking_level` (default minimal), not a budget. No call in this code sends any of them.

### Infrastructure

- **Aiven:** free PostgreSQL plan, 1 GB storage, service `trade-agent-pg`. The connection limit is 20 for everyone, so the gateway pool is capped at 4. Free services power off when inactive: check it is running before the video and the pitch.
- **Python:** the venv uses the system Python 3.12. The conda base is 3.14, which is new enough to be a risk for prebuilt packages.

---

## 8. Decisions I made that the team should confirm

| Decision | Why | Where |
|---|---|---|
| Plain products rank before ones with "sour", "coffee", "portion" and similar that the request didn't mention | Otherwise a sour milk one cent cheaper would be ordered for a "milk" request | `QUALIFIERS` in `kitchen/agent.py` |
| If only off-request products can be ordered, the line is flagged `differs_from_request` and goes to a manager | A closest match should never be silent | `kitchen/agent.py` |
| The agent searches without `deliverable_by` and drops past-cutoff products itself | So the trace can say "18 past cutoff" and an empty result gets a reason | `kitchen/agent.py` |
| Alternatives are the next best product and the best in a different pack size | The rule from the contract review | `kitchen/agent.py` |
| A swap rebuilds the line from the original need | Pack sizes differ between products, so a typed quantity doesn't carry over | `apply_edits` |
| `check_delivery` refuses more than 100 items | Shortlist, not the catalogue | `gateway/delivery.py` |

---

## 9. Known limits

- **Baskets are in memory.** Restarting the kitchen service loses them. They move to the kitchen's own schema with the order flow.
- **A partial delivery is not split or reduced.** The Arla example orders 1,000 while only 384 can be delivered. The reason text and the policy both say so, but the quantity and total are for the full 1,000. This is block 3 work.
- **`excluded` and `earliest_deliverable_date` are not in the search response.** The contract review asked for them; `shared/schemas.py` doesn't have the fields yet.
- **A Finnish request can't be matched to a category,** because the labels are English. Gemini translates the product text to English, so the chat path is fine; a direct Finnish search is broader.
- **`deposit_units` is always 0** on a basket line, since `ProductSummary` doesn't carry deposits.
- **The policy is only a preview** (chef limit, partial delivery, pack-size disagreement, off-request product). The binding decision, mandate and approvals are block 2.
- **Not deployed yet.** Everything above ran on a laptop against Aiven. The mock API has not been called from a cloud host, which Cloudflare may treat differently.

---

## 10. What is next

We are at hour 9.5; the plan had a placed order by hour 8.5 and signatures by hour 10.

1. **Block 2a:** unsigned quote and order. Gateway: `create_quote`, `place_order`, `get_order`. Kitchen: confirm and approve endpoints, binding policy decision.
2. **Block 2b:** mandate, signed approval, single-use quotes.
3. **Deploy both services** and call the mock from the deployed host.
4. **Seed data check:** when `recipes.json` arrives, verify every product ID exists in the catalogue and is orderable through `check_delivery`. Note the folder is `Backend/Data`, capital D; `Backend/data` is a different folder on Linux.
5. **Demo:** decide how beat 1 shows a unit conversion now that the Novobox is out for Tuesday. Options: use `463779` (267 cases), or a later delivery date if the multi-date check shows the Novobox cutoff moves.
