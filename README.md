# Trusted Trade Agents

Valio Aimo challenge, Junction 2026. An open, guarded interface that lets a kitchen's
purchasing agent find, price and order from Aimo, with a human approving every order.

## Four documents, and that is all

| | |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | The plan, frozen, with a **Status** section saying what is built, and section 18 on what the live API actually does |
| [`docs/CHANGES.md`](docs/CHANGES.md) | What changed, newest first. Add a line when you push |
| [`docs/CONTRACTS.md`](docs/CONTRACTS.md) | The shapes the services pass each other. Field names are agreed; raise a change in the group |
| this file | Where things are and how to run them |

## Layout

```
Backend/
  shared/     units, money, schemas, timeparse, signing   both services import it, it imports neither
  gateway/    Aimo's side: catalogue, price and availability, quotes, orders
  kitchen/    the kitchen's side: agent, policy, ordering, inventory, suggestions, trace
  tests/      automated backend tests, all offline
  scripts/    tools, below
  Data/       the product catalogue (gitignored, 84 MB) and the seeded restaurant
  sql/        audit.sql: the audit tables, run once against Aiven
Frontend/     the two screens; fixtures/sample_basket.json is a real basket to build against
docs/
```

Two FastAPI services, two Postgres schemas, talking over HTTP only. That separation is what
makes "kitchen data stays with the kitchen" true in the code rather than only on a slide.

## Running it from nothing

**You need four things before you start.** Three of them are not in this repository,
because none of them belong in one.

| | Where it comes from |
|---|---|
| **Python 3.11+** | Aimo's timestamps carry an offset with no colon, which 3.10 refuses to parse |
| **Node 20+** | for the frontend |
| **A Postgres database** | any will do. We used a free Aiven instance. The catalogue and the audit logs live here |
| **`JUNCTION_API_KEY`** | the challenge portal. Without it nothing can be priced, and the demo cannot run |
| `GEMINI_API_KEY` | *optional.* Google AI Studio. Without it the chat falls back to a rules parser that handles the demo sentences; the reply's `parser` field says which one answered |
| **The 84 MB product file** | the challenge portal, saved as `Backend/Data/valio_aimo_junction26_product_data.json`. Gitignored on purpose — it is given data, not ours to redistribute |

### 1. Install

```
cd Backend
python -m venv .venv && .venv\Scripts\Activate.ps1      # bash: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                                   # then fill it in
```

`.env.example` lists the seven variables that matter. Everything else in the table further
down has a working default. Write the file with LF line endings and keep prose behind `#`.

### 2. Set the database up, once

```
python scripts/apply_sql.py                  # the audit, order and state tables
python scripts/load_catalogue.py             # the 84 MB file into gateway.products, a few minutes
python scripts/label_categories.py --from-file   # readable names for the 349 category codes
python scripts/apply_sql.py --check          # what exists now, with row counts
```

`label_categories.py --from-file` reloads the labels we already worked out, committed as
`Data/category_labels.json`, so it costs no model calls. Without the `--from-file` it asks
Gemini again.

### 3. Check it before trusting it

All three are offline — no database, no model, no network — and take seconds:

```
python -m pytest -q                   # the backend suite
python scripts/end_to_end.py          # one order: chat, signed quote, order, inventory
python scripts/check_i18n.py          # every translation key and placeholder lines up
```

### 4. Run it

Three terminals:

```
cd Backend  && uvicorn gateway.main:app --reload --port 8001   # Aimo's side, docs at /docs
cd Backend  && uvicorn kitchen.main:app --reload --port 8002   # the kitchen's side
cd Frontend && npm install && npm run dev                      # http://localhost:5173
```

Open <http://localhost:5173> and type *1,000 L lactose-free milk by Tuesday*.

**If nothing can be ordered**, the catalogue's order cut-offs have passed — they are fixed
timestamps in given data, and they do not move when you ask for a later delivery day:

```
python scripts/cutoff_check.py                 # says which of the two it is
```

If they are past, set `AIMO_CUTOFF_NOW` in `.env` to a moment before them and restart the
gateway. It judges *only* whether a recorded cut-off counts as passed — prices, stock and
every signature keep the real clock — and the gateway prints a banner while it is set. A
demo using it should say so.

Two more offline runs worth knowing, both without a database or a network:

```
python scripts/sample_basket.py --order                   # a sentence to a placed order
python scripts/sample_basket.py --order --stock-moves 400 # the basket repairing itself
```

## Hosting it

**The frontend only uses relative paths** (`/api/...`, `/gateway/...`). Vite's dev server
proxies those to the two services; **a built site does not**, so a hosted frontend with no
proxy in front of it calls itself and every request 404s. Put one origin in front of all
three, with `deploy/Caddyfile` or `deploy/nginx.conf`:

```
/api/*                  -> kitchen, :8002
/gateway/*              -> gateway, :8001
/.well-known/jwks.json  -> kitchen, :8002
everything else         -> Frontend/dist
```

Then `npm run build` in `Frontend/`, start both services, and run the proxy.

**Environment, per service.** Both read `Backend/.env`. Write it with LF line endings and no
prose outside `#` comments — Docker's `--env-file` keeps a trailing `\r` in every value, which
silently corrupts the API key.

| Variable | Service | Why it matters |
|---|---|---|
| `JUNCTION_API_KEY` | gateway | Aimo's mock API. Without it nothing can be priced |
| `AIMO_BASE_URL`, `AIMO_CUSTOMER_ID` | gateway | |
| `DATABASE_URL` | gateway | The product catalogue. Search and quotes fail without it; `/health` reports `degraded` |
| **`KITCHEN_JWKS`** | gateway | The kitchen's JWKS URL. **Unset and every order is refused** while search, price and quote still work. Defaults to the kitchen on the same host |
| `AGENT_KEYS` | gateway | `key\|agent_id\|customer_id\|label`, comma separated. Each key is one customer, and Aimo prices by customer. Defaults to the two demo agents |
| `AUTO_ORDER` | kitchen | Whether the agent places a basket its own standing rules cleared. Default on; needs a rule *and* a mandate that does not require approval |
| `MANDATE_PER_DAY_EUR` | kitchen | The daily budget a new mandate is signed with. Default 2000; the week's default is 5000 |
| `LOCAL_UTC_OFFSET_HOURS` | gateway | Which day "today" means for the daily budget. Default 2, Finland in winter |
| `KITCHEN_NAME` | kitchen | What the title bar says. The frontend asks `GET /api/kitchen`, so a second tenant needs no frontend change |
| `AGENT_API_KEY` | kitchen | The key this kitchen presents to the gateway. Defaults to `demo-kitchen-key` |
| **`GATEWAY_ISSUER`** | both | The gateway's public name, signed into every quote and written on every mandate, e.g. `http://your-host/gateway`. **Not** `GATEWAY_URL`, which is where the kitchen reaches the gateway and must stay `http://localhost:8001`. Unset, quotes are issued by `localhost` |
| `AIMO_CACHE_SECONDS` | gateway | How long an answer from Aimo is reused. Default 60. The order-time re-check never uses the cache |
| `AIMO_CUTOFF_NOW` | gateway | Demo-only ISO timestamp with an offset. Judges Aimo's recorded `closingTime` at that stated moment; leave unset for the real clock. The gateway prints a warning when this is enabled, and the demo should disclose it |
| `AIMO_CUSTOMER_MAP` | gateway | `ours=theirs,...`: which Aimo customer each gateway customer is priced as. One with no entry is priced on `AIMO_CUSTOMER_ID` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | kitchen | A wrong model name falls back to the rules parser. The chat reply's `parser` field and the kitchen log both say which one ran |
| `GATEWAY_URL` | kitchen | Where the gateway is, server to server |
| `KEY_DIR` | both | Signing keys. Defaults to `Backend/.keys/`. **Must be on a persistent disk** — a new key on restart invalidates every mandate already issued |

**Check it before trusting it:**

```
python scripts/smoke_test.py --kitchen http://your-host --gateway http://your-host
```

It walks the whole demo path over HTTP and names whatever is broken. Exit code 0 means
sentence to placed order works. Add `--reset` to clear the demo state first.

## Tools

| Script | For |
|---|---|
| `scripts/hour0_check.py` | Call the live API and print prices, cut-offs and totals |
| `scripts/cutoff_check.py` | Compare live delivery cut-offs across several dates; read-only |
| `scripts/find_products.py` | Find real product ids with their pack size; `--price` marks what is orderable |
| `scripts/end_to_end.py` | Exercise chat, signed quote, order and inventory together, offline |
| `scripts/sample_basket.py` | A real basket, or a whole order, offline |
| `scripts/check_gemini.py` | Whether the model call works, and which models the key can use |
| `scripts/apply_sql.py` | Apply `sql/audit.sql` to Aiven without needing psql; `--check` to look first |
| `scripts/apply_sql.py --check` | What tables exist, with row counts |
| `scripts/clean_slate.py` | Clear every table the demo writes, the old state file, and what the running services hold in memory. Dry run by default; `--yes` to do it, and `--kitchen`/`--gateway` to reach services that are not on localhost |
| `scripts/check_i18n.py` | Every translation key and placeholder, checked against the call sites. TypeScript cannot see inside a translation string |
| `scripts/smoke_test.py` | The whole demo path over HTTP against running services |
| `scripts/generate_seed.py` | Regenerate the seeded restaurant |
| `scripts/load_catalogue.py` | Load the product JSON into Postgres |
| `scripts/label_categories.py` | Name the 349 category codes |

The 84 MB product file is gitignored: download it from the challenge portal into
`Backend/Data/`. Keys are read from the environment and are never committed; the private
signing keys live in `Backend/.keys/`, also gitignored.
