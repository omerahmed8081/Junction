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
  tests/      161 tests, all offline
  scripts/    tools, below
  Data/       the product catalogue (gitignored, 84 MB) and the seeded restaurant
  sql/        audit.sql: the audit tables, run once against Aiven
Frontend/     the two screens; fixtures/sample_basket.json is a real basket to build against
docs/
```

Two FastAPI services, two Postgres schemas, talking over HTTP only. That separation is what
makes "kitchen data stays with the kitchen" true in the code rather than only on a slide.

## Running it

**Python 3.11 or newer.** Aimo's timestamps use an offset with no colon, which 3.10 refuses.

```
cd Backend
python -m venv .venv && .venv\Scripts\Activate.ps1      # bash: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                                   # then fill it in
```

Everything offline, with no database, model or network:

```
python tests/test_units.py && python tests/test_money.py && python tests/test_timeparse.py
python tests/test_signing.py && python tests/test_agents.py && python tests/test_audit.py && python tests/test_need.py
python tests/test_edits.py && python tests/test_chat_dates.py
python tests/test_aimo_client.py
python tests/test_delivery.py
python tests/test_agent.py && python tests/test_inventory.py && python tests/test_orders.py
python tests/test_ordering.py

python scripts/sample_basket.py --order                  # a sentence to a placed order
python scripts/sample_basket.py --order --stock-moves 400 # the basket repairing itself
```

Both services, each in its own terminal with `.env` filled in:

```
uvicorn gateway.main:app --reload --port 8001     # docs at /docs
uvicorn kitchen.main:app --reload --port 8002     # what the frontend calls
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
| `AGENT_API_KEY` | kitchen | The key this kitchen presents to the gateway. Defaults to `demo-kitchen-key` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | kitchen | A wrong model name falls back to the rules parser. The chat reply's `parser` field and the kitchen log both say which one ran |
| `GATEWAY_URL` | kitchen | Where the gateway is, server to server |
| `KEY_DIR` | both | Signing keys. Defaults to `Backend/.keys/`. **Must be on a persistent disk** — a new key on restart invalidates every mandate already issued |

**Check it before trusting it:**

```
python scripts/smoke_test.py --kitchen https://your-host --gateway https://your-host/gateway
```

It walks the whole demo path over HTTP and names whatever is broken. Exit code 0 means
sentence to placed order works. Add `--reset` to clear the demo state first.

## Tools

| Script | For |
|---|---|
| `scripts/hour0_check.py` | Call the live API and print prices, cut-offs and totals |
| `scripts/find_products.py` | Find real product ids with their pack size; `--price` marks what is orderable |
| `scripts/sample_basket.py` | A real basket, or a whole order, offline |
| `scripts/check_gemini.py` | Whether the model call works, and which models the key can use |
| `scripts/apply_sql.py` | Apply `sql/audit.sql` to Aiven without needing psql; `--check` to look first |
| `scripts/generate_seed.py` | Regenerate the seeded restaurant |
| `scripts/load_catalogue.py` | Load the product JSON into Postgres |
| `scripts/label_categories.py` | Name the 349 category codes |

The 84 MB product file is gitignored: download it from the challenge portal into
`Backend/Data/`. Keys are read from the environment and are never committed; the private
signing keys live in `Backend/.keys/`, also gitignored.
