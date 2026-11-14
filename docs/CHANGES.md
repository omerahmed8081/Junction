# Changes

Newest first. One line per change that affects somebody else's work: a field name, a
behaviour, a file that moved. Add to the top when you push.

---

## Saturday 14 November

### 16:15 - eleven documents down to four

Notes had piled up faster than anyone could read them, so they are now:

```
README.md              where things are, how to run them
docs/ARCHITECTURE.md   the frozen plan + Status (what is built) + what the live API does
docs/CHANGES.md        this file
docs/CONTRACTS.md      the shapes the services pass each other
```

- `ARCHITECTURE.md` moved into `docs/` and lost 250 lines of review history (the old sections
  18 to 23, which recorded how each draft was revised and nobody needs now). The **Status**
  section at the top is the only part that changes, and it now has a row per block, who is
  building what, how to run it, and the five known gaps.
- `HOUR0_FINDINGS.md` and `MENTOR_ANSWERS.md` are folded into **section 18, "What the live API
  actually does"**: access and rate limits, prices, availability, ordering, and the demo's
  numbers, with Valio's own answers marked. One place to look instead of two files and a
  changelog.
- Section 17's mentor questions are marked answered where they have been.
- Deleted: `WORKSPLIT.md` (the split changed twice and is now in Status), `docs/history/`
  (`NIGHT_SHIFT.md` and `BRIEF1_HANDOVER.md`, both superseded by entries in this file), and
  the one-line `Backend/README.md` and `Frontend/README.md`.
- Code docstrings that cited the deleted files now cite `docs/ARCHITECTURE.md` and its
  sections. No dangling references remain.

### 16:00 - inventory, the forecast and the running-low suggestion (block 7)

Beat 3 of the demo works. **161 tests passing**, all offline.

**Built on your teammate's seed data, which checks out.** All 17 product ids are real, no unit
mismatches, 28 days of sales with a believable weekday pattern (Mon 95 portions rising to Sat
161, closed Sundays) and volume growing 94 to 168 over the month. The ledger reconciles
exactly: opening count + deliveries - (dishes sold x recipes) = the stock it states. That is
now a test (`test_the_ledger_reproduces_the_seed_exactly`).

**Their fix to `shared/units.py` is right and I have left it alone.** A product with base unit
`KG` is sold by weight, so one base unit is one kilogram; the weight in the name or the data
is a box weight. Without it, ordering 15 kg of salmon fillet whose name ends "10KG" would have
ordered 150 kg.

**New endpoints on the kitchen service**

| Endpoint | What it does |
|---|---|
| `GET /api/inventory` | Stock, what is on order, expected usage, when each runs out |
| `GET /api/suggestions` | What the kitchen will run out of. Ledger only, no call to Aimo, so the panel can refresh freely |
| `POST /api/suggestions/{product_id}/basket` | Turn one suggestion into a draft basket |
| `POST /api/service` | "What did you cook today?" One number per dish; returns the shortfalls and a draft basket for the most urgent |

**What a suggestion looks like.** The arithmetic is in the sentence, because a number without
it is not a reason:

> Short 10.333 L: the menu needs 18.065 L, plus 4.758 L of cover, 2.59 L in stock, 9.9 L
> already on order. Runs out 2026-11-19. Used in Creamy mushroom soup, Creamy salmon soup.

**How it works** (`kitchen/inventory.py`)

- The ledger is **append-only movements**: `count`, `delivery`, `sale`, `waste`. Stock is their
  sum. Ordering creates an **on-order record**, not a movement, so an amount is never in both
  `stock` and `on_order`; the delivery movement is written on receipt and closes the record.
- Usage is forecast from **what the kitchen cooked**, not what it ordered: a dish's expected
  portions are its average over the last four of that weekday. The seed closes Sundays, and a
  weekday average keeps that zero where it belongs instead of smearing it across the week.
- `shortfall = expected usage + cover - stock - on order`. Subtracting on-order is what stops
  the agent proposing the same order every morning, and there is a test for exactly that.
- The parts are rounded before the shortfall is taken from them, so the figures in the reason
  add up to the figure acted on.

**A reorder is not a search** (`agent.add_reorder`). The kitchen knows which cream it wants,
and picking a cheaper one because it ranked higher would change the dish. The named product
wins if it can be ordered at all; only if it cannot does the agent fall back to the closest
and flag the line `differs_from_request`. Similar products are still fetched so the chef has
alternatives to swap to.

**Also**

- `SearchRequest.product_ids` looks products up by id, which is how a reorder gets a product's
  name and pack size. Additive.
- `ServiceRequest.service_date`, not `date`: a field called `date` shadows the `date` type
  inside the class and pydantic then cannot resolve the annotation.
- Logged services survive a restart, stored as "what was cooked" and replayed into the ledger
  rather than saved as raw movements. `storage.load()` now returns a fourth value.

**Still open:** the trace is returned per call but not stored, so the Activity tab has nothing
to query; the second-client script for beat 5; multi-item chat requests.

### 15:30 - a refusal repairs the basket instead of ending the conversation

**139 tests passing.** Try it:
`python scripts/sample_basket.py --order --stock-moves 400`

A refused order used to be a dead end: an error, and the chef left to work out what to do.
Now the agent treats it as information and fixes what it can.

**`POST /api/basket/{id}/approve` always answers 200 with an `outcome`:**

| outcome | what it means |
|---|---|
| `ordered` | Aimo accepted it. `order` carries the record and the signed confirmation. |
| `adjusted` | Stock moved or a cut-off passed. The basket now matches what Aimo says, carries suggestions for the shortfall, and is a draft again. |
| `refused` | A limit the basket cannot fix itself, such as the mandate. `refusal` says which. |

Only a caller mistake is still an error (409): approving an unconfirmed basket, or a role that
is not senior enough for what the policy decided.

**What `adjusted` does.** Asked for 1,000 litres, Aimo has 400 left by the time the order is
placed:

1. The line comes back at **400 units, EUR 404.00 net**, flagged `partial_split`.
2. Anything that could cover the other **600 litres** is attached to the line as a suggestion
   with its own quantity and total, so the existing swap button works on it.
3. The reply explains all of it in words the chat can show, ending with "Confirm again for a
   fresh signed price".

It **suggests rather than substitutes**: a different product is offered, never quietly
ordered. Swapping one in without asking is the thing the guardrails exist to prevent.

**New in `kitchen/agent.py`**

- `reprice(state, gateway)` re-checks every line against Aimo and brings it back in line:
  reduces a quantity to what is deliverable, drops a line that is blocked, past its cut-off or
  undeliverable, picks up a price change, and returns a note per change. Useful on its own as
  a basket refresh.
- `suggest_cover(state, gateway)` works out what each need is now short of and attaches the
  best two products that could close it, flagged `covers_shortfall`.
- Stale alternatives are cleared first, since they were priced for a quantity we no longer
  have.

**Also:** `OrderResponse` gained `outcome` and `refusal`, and `order` is now optional.
`ordering.approve` takes the basket state rather than the basket, because repairing needs the
need behind each line. `GatewayRefused.rejection_model()` converts a gateway 409 into the
shared `OrderRejected` shape.

### 15:05 - the kitchen half: confirm, approve, order (block 2 done)

A sentence now becomes a placed order. **139 tests passing**, all offline.
`python scripts/sample_basket.py --order` runs the whole thing on your machine
with no database, no Gemini and no network.

**New endpoints on the kitchen service**

| Endpoint | What it does |
|---|---|
| `GET /.well-known/jwks.json` | The kitchen's public key, so the gateway can verify its mandates |
| `POST /api/basket/{id}/confirm` | Aimo signs a price confirmation; draft becomes quoted |
| `POST /api/basket/{id}/approve` | A person approves, the kitchen signs it, the order is placed |
| `GET /api/orders`, `GET /api/orders/{id}` | Orders, newest first |
| `GET /api/mandate`, `POST /api/mandate` | What the agent may order, for the Agent rules tab |
| `POST /api/demo/reset` | Labelled: clears baskets, orders and mandate |
| `POST /gateway/demo/reset` | The gateway's own side: orders, used quote ids, weekly spend |

**How the flow hangs together**

- **Confirm** calls `create_quote`, **verifies Aimo's signature**, then copies the figures out
  of the verified payload. From then on the basket shows what Aimo signed, not our own
  arithmetic. A quote that does not verify blocks the basket rather than showing unchecked
  numbers.
- The policy preview becomes **binding** at confirm.
- **Approve** checks the role is senior enough (a chef cannot wave through a basket the policy
  sent to a manager), signs an approval naming that one quote, and sends quote, mandate and
  approval together.
- **Any edit to a quoted basket drops it back to draft** and voids the quote, which is why the
  quote is signed at confirm and not at draft.
- A refusal from Aimo is an answer, not a failure: `409` with `{reason, detail,
  security_event}` and the basket as it now stands, so the screen can explain it.

**Partial delivery is fixed** (the gap the status section has been carrying, and beat 2 of the
demo). A line used to order the full quantity even when Aimo could only deliver part of it.
Now the need is **split across products**: asking for 1,000 L where the two Arla products hold
384 and 51 units gives two lines of 384 and 51, flagged `partial_split`, with the 565 L
shortfall stated on the last line and in the reply. It also makes the line quotable at all,
because the gateway refuses to sign for more than it can deliver.

**Baskets, orders and the mandate now survive a restart** (`kitchen/storage.py`): a JSON file
written atomically after every change. Candidate products are deliberately not saved, since
they are a search result rather than state, so a swap after a restart searches again. A
missing or corrupt file starts empty instead of crashing, and one unreadable basket does not
cost the others.

**New files:** `kitchen/keys.py`, `kitchen/ordering.py`, `kitchen/storage.py`,
`tests/test_ordering.py` (21 tests, a sentence to an order with real signing).
**New shapes:** `ApproveRequest`, `OrderResponse`, `MandateRequest`, `MandateView`.
`GatewayRefused` carries a gateway 409 with its reason through to the caller.

**Still open:** the chat takes one item per message and cannot be used to edit a basket;
nothing is deployed; the running-low suggestion needs the seed data.

### 14:35 - the gateway half of the order flow (blocks 2a and 2b)

Signing, quotes and orders. **116 tests passing**, all offline.

**New files**

| File | What it is |
|---|---|
| `shared/signing.py` | Sign and verify the three tokens. Compact JWS over Ed25519, published as a JWKS. |
| `gateway/quotes.py` | Build and sign a quote; the single-use registry. |
| `gateway/orders.py` | `place_order` and every check it makes; the order store. |
| `gateway/keys.py` | Aimo's own key, and the kitchen public keys it will accept. |
| `tests/test_signing.py` | 20 tests: forgeries, expiry, skew, the JWKS. |
| `tests/test_orders.py` | 28 tests: one per way an order can be refused. |

**New endpoints on the gateway**

| Endpoint | What it does |
|---|---|
| `GET /.well-known/jwks.json` | Aimo's public key, so any agent can verify a quote with a standard library |
| `POST /gateway/create_quote` | Signs a price confirmation for exactly these lines |
| `POST /gateway/place_order` | Accepts an order if the quote, mandate and approval all hold |
| `GET /gateway/orders/{order_id}` | The order and its signed confirmation |

**Things to know if you are calling this**

- **`alg` is `Ed25519`, not `EdDSA`.** RFC 9864 renamed it, and joserfc warns on the old
  name. Verification accepts both, so an older client still interoperates.
- **A quote is single use.** Ordering the same quote again is refused with
  `quote_already_used`, even with a fresh idempotency key. The same key with the same quote
  returns the original order, which is what a retry should do.
- **A quote refuses a line it cannot stand behind** rather than quoting it with a caveat:
  partial availability, a passed cut-off, a sales block or no price all land in `refused[]`
  with a reason. `462775` with 51 units in stock is refused for a 1,000 unit need.
- **`exp` is the earliest of** the price validity, the order cut-off and two hours, so a quote
  can never outlive the cut-off it depends on.
- **Delivery is re-checked when the order is placed**, and a change refuses it with
  `stock_changed`. A cut-off that passed while the quote was open gives `past_closing_time`.
- **Refusals are HTTP 409** with `{reason, detail, security_event}`. The request was fine; the
  state said no. `security_event` is true when a signature failed or a token was for
  something else: that is for the audit log, not a retry.
- **The kitchen's public key is registered, never discovered.** `KITCHEN_JWKS` (comma
  separated URLs) or `KITCHEN_JWKS_FILE` for local work. A key is never taken from a URL
  inside a token, or anyone could mint a mandate naming their own issuer.
- **Private keys** are created on first run in `Backend/.keys/`, which is gitignored.

**New shapes in `shared/schemas.py`** (all additive): `QuoteLine`, `QuotePayload`,
`MandatePayload`, `MandateLimits`, `ApprovalPayload`, `CreateQuoteRequest`,
`CreateQuoteResponse`, `PlaceOrderRequest`, `OrderRecord`, `OrderRejected`.
`catalog.summaries(conn, ids)` looks products up by id for quoting.

**Still the kitchen's side:** a confirm endpoint that calls `create_quote`, an approve
endpoint that signs the approval and calls `place_order`, mandate issuing, and persistence.

### 13:55 - documents tidied

- All notes now live in `docs/`. `ARCHITECTURE.md` stays at the repo root as the single
  agreed plan, with a **Status** section at the top that is the only part that changes.
- `docs/history/` holds notes that have served their purpose: `NIGHT_SHIFT.md` (the hour 0
  briefs) and `BRIEF1_HANDOVER.md`.
- **A stale copy of `ARCHITECTURE.md` was found in a `Claude outputs/` folder and it was the
  newer one:** the root copy was missing section 23 (the 7.1 and 7.2 changelog). The complete
  file is now at the root and the folder is gone. If anyone was reading the root copy, that
  section is new to you.
- This file.

### 13:50 - a basket you can print without running anything

- `Backend/scripts/sample_basket.py` prints a real basket from the recorded API responses:
  no database, no Gemini, no network. `--quantity` and `--swap-to` exercise an edit, `--out`
  writes the JSON.
- `Frontend/fixtures/sample_basket.json` is that output, in the contract shape, so the
  frontend can be built without the backend running.
- Line reasons read properly again: one said "does not confirm stock deliverable 2026-11-16".

### 13:45 - Valio's answers applied (docs/MENTOR_ANSWERS.md)

Five answers from the Valio team, four of which changed code.

- **`availableAmount` at or above 999999 means availability is UNKNOWN, not unlimited.**
  Aimo allows ordering any quantity but confirms no stock. `DeliveryAnswer` gained
  **`availability_known: bool`** (additive), the basket line gains the flag
  `availability_unknown`, and the reason says so instead of promising the full amount. The
  threshold is now exactly 999999, not the 100,000 we had guessed.
  It is deliberately **not** an approval reason: 10 of the 14 orderable milks carry a
  sentinel, so it would send nearly every basket to a manager.
- **A 429 blocks us for 30 seconds.** `gateway/aimo_client.py` now waits it out and retries
  twice, honours `Retry-After`, spaces calls 0.25 s apart and caches each question. New
  exception `AimoRateLimited`. Twelve tests in `tests/test_aimo_client.py`, all offline.
- **An empty `scheduleLines` means not available**: the reason is now `not_available`.
- **`allowedLotSize` 0.01 means 1**, which is what the code already did; Valio's wording is
  quoted in the docstring so nobody changes it.
- `is_unlimited()` is renamed `is_availability_unknown()`. The old name still works.
- **No order API exists and none is expected.** Producing orders for human approval is the
  whole requirement, so our order step is the real thing, not a stand-in for an Aimo call.

### 13:30 - timestamp parsing bug fixed (would have broken deployment)

- Every timestamp Aimo sends uses an offset with no colon (`"+0200"`), which
  `datetime.fromisoformat` only accepts from **Python 3.11**. On 3.10 the delivery check
  raised `ValueError`, and `delivery._when()` silently returned `None` for price validity
  dates. It passed in development only because that venv is 3.12.
- New **`shared/timeparse.py`** is now the only place an API timestamp is parsed. It also
  turns the year-9999 "no end date" sentinel into `None`, so `valid_to: null` means
  open-ended and the frontend never renders `9999-12-31`.
- **Never call `fromisoformat` on a value from the API.** Use `parse_api_datetime()` or
  `parse_api_date()`.
- Ten tests in `tests/test_timeparse.py`. Everyone and the deploy host: **Python 3.11+**.

### 12:35 - the gateway and the chat-to-basket flow (teammate)

Full note in `docs/history/BRIEF1_HANDOVER.md`.

- Catalogue loaded into Aiven Postgres: 17,546 products, full-text search over the Finnish,
  English and Swedish names, claims, labels and the pack size computed at load time.
- All 349 category codes labelled, published at `GET /gateway/categories`.
- `POST /gateway/search_products` and `POST /gateway/check_delivery`.
- Kitchen service: `POST /api/chat`, `PATCH /api/basket/{id}`, `GET /api/basket/{id}`.
- `ProductSummary` gained `allowed_lot_size` (additive).
- `shared/units.py` gained `convert_summary()`, so the kitchen can convert from a
  `ProductSummary` without reading the catalogue, which would have broken the two-service
  split. Verified against `convert()` on all 17,546 products.
- Ranking: plain products before ones carrying qualifiers the request never mentioned
  ("sour", "coffee", "portion"), then full before partial delivery, then price per litre,
  then delivery reliability, then carbon footprint.

### 04:10 - the contracts, before anyone split up

- `docs/CONTRACTS.md`: the shapes passed between tracks, with a worked example.
- `Backend/shared/` (units, money, schemas), `Backend/gateway/`, `Backend/kitchen/`:
  two services from the start, `shared/` importing from neither.
- `shared/money.py`: VAT computed once per line from the line net, with `Decimal` and half a
  cent rounding up. The first version of the hour 0 note multiplied a per-unit price that
  Aimo had already rounded, which was EUR 3.65 out on 1,000 units.
- `shared/units.py`: the size rule, reading the unit and the packaging level, with the
  product name as a fallback and the price-derived size as a last resort.

### 03:40 - the mock API answered for the first time

Full note in `docs/HOUR0_FINDINGS.md`.

- Cloudflare rejects a script-like user agent with 403 and "error code: 1010".
- `comparisonPrice` is already per litre and follows the customer price, so rank on it; it is
  rounded to cents, so never total with it.
- `netPriceFound: false` is not an error: the catalogue price applies, for 24 of 35 products.
- **Only 14 of the 35 lactose-free milks are orderable**; 20 are past their cutoff, including
  both 10 L Novoboxes, and one has no delivery schedule.
- Beat 1's numbers: `464306` at EUR 1.01 per litre, 1,000 units, **EUR 1,010.00 net**,
  EUR 136.35 VAT, EUR 1,146.35 gross, delivery 16 November, cutoff Sunday 17:00.
