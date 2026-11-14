# Trusted Trade Agents

Valio Aimo challenge, Junction 2026. An open, guarded interface that lets a kitchen's
purchasing agent find, price and order from Aimo, with a human approving every order.

## Where things are

| | |
|---|---|
| **The plan, and what is built** | [`ARCHITECTURE.md`](ARCHITECTURE.md) - frozen, apart from the Status section at the top |
| **What changed, newest first** | [`docs/CHANGES.md`](docs/CHANGES.md) - add a line when you push |
| **The shapes we pass each other** | [`docs/CONTRACTS.md`](docs/CONTRACTS.md) - field names are agreed; raise a change in the group |
| **What the real API does** | [`docs/HOUR0_FINDINGS.md`](docs/HOUR0_FINDINGS.md) |
| **What Valio told us** | [`docs/MENTOR_ANSWERS.md`](docs/MENTOR_ANSWERS.md) |
| **Who is building what** | [`docs/WORKSPLIT.md`](docs/WORKSPLIT.md) |
| Notes that have served their purpose | [`docs/history/`](docs/history) |

## Layout

```
Backend/
  shared/     units, money, schemas, timeparse   imported by both services, imports neither
  gateway/    Aimo's side: catalogue, price and availability, quotes and orders
  kitchen/    the restaurant's side: agent, policy, ledger, trace
  tests/      68 tests, all offline
  scripts/    one-off tools, see below
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

All the tests, with no database, model or network:

```
python tests/test_units.py && python tests/test_money.py && python tests/test_timeparse.py
python tests/test_aimo_client.py && python tests/test_delivery.py && python tests/test_agent.py
```

A real basket, also offline, from the recorded API responses:

```
python scripts/sample_basket.py
python scripts/sample_basket.py --quantity 1200
```

Both services (each in its own terminal, `.env` filled in):

```
uvicorn gateway.main:app --reload --port 8001     # docs at /docs
uvicorn kitchen.main:app --reload --port 8002     # what the frontend calls
```

## Tools

| Script | For |
|---|---|
| `scripts/hour0_check.py` | Call the live API and print prices, cutoffs and totals |
| `scripts/find_products.py` | Find real product ids with their pack size; `--price` marks what is orderable |
| `scripts/sample_basket.py` | Print or export a real basket, offline |
| `scripts/load_catalogue.py` | Load the product JSON into Postgres |
| `scripts/label_categories.py` | Name the 349 category codes |

The 84 MB product file is gitignored: download it from the challenge portal into
`Backend/Data/`. The API key is read from `JUNCTION_API_KEY` and is never committed.
