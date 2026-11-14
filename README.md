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
python tests/test_signing.py && python tests/test_aimo_client.py && python tests/test_delivery.py
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

## Tools

| Script | For |
|---|---|
| `scripts/hour0_check.py` | Call the live API and print prices, cut-offs and totals |
| `scripts/find_products.py` | Find real product ids with their pack size; `--price` marks what is orderable |
| `scripts/sample_basket.py` | A real basket, or a whole order, offline |
| `scripts/generate_seed.py` | Regenerate the seeded restaurant |
| `scripts/load_catalogue.py` | Load the product JSON into Postgres |
| `scripts/label_categories.py` | Name the 349 category codes |

The 84 MB product file is gitignored: download it from the challenge portal into
`Backend/Data/`. Keys are read from the environment and are never committed; the private
signing keys live in `Backend/.keys/`, also gitignored.
