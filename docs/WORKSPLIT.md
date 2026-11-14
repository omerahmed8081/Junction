# Who builds what

Four people. Hour 0 is 03:00 Saturday; it is now about hour 1. Feature freeze at hour 15.
Hour numbers below are from `ARCHITECTURE.md` section 13.

**Updated Saturday 13:55 (hour 11).** The split changed once the gateway was done.

| Person | Track | Owns now |
|---|---|---|
| Omer | **Backend, kitchen side** | Confirm and approve endpoints, mandate issuing, persistence, the running-low trigger |
| Teammate 1 | **Frontend** | Both screens. Build against `Frontend/fixtures/sample_basket.json` |
| Teammate 2 | **Backend, gateway side** | Quote and order service, signing, verification |
| Teammate 3 | **Demo and data** | Seed recipes and sales history, demo numbers, slides, video |

Original split, for reference:

| Person | Track | Owns |
|---|---|---|
| Backend 1 | **Gateway** | Catalogue in Postgres, search, the two endpoints, OpenAPI + MCP, the Aimo client |
| Backend 2 | **Agent** | The state machine, Gemini parsing, ranking, basket assembly, the trace |
| Frontend | **Screens** | Basket component, chat, then the dashboard tabs |
| Pitch | **Demo + data** | Demo script and numbers, seed data, slides, video, the demo control page |

Suggestion: whoever knows `ARCHITECTURE.md` best takes the agent. It is the piece where a
wrong decision costs the most, and the gateway is more mechanical now that
`gateway/aimo_client.py` exists.

## Already built, so nobody starts from zero

| File | Tests |
|---|---|
| `Backend/shared/units.py` - the whole size rule and need-to-quantity conversion | 14 passing |
| `Backend/shared/money.py` - line and basket totals, VAT once per line | 7 passing |
| `Backend/shared/schemas.py` - every shape in `docs/CONTRACTS.md` | compiles |
| `Backend/gateway/aimo_client.py` - the only module that calls Aimo | compiles |
| `Backend/scripts/hour0_check.py` - re-run the real API any time | ran against the live API |

Run `python tests/test_units.py` and `python tests/test_money.py` from `Backend/` first, so
everyone has seen them pass.

## First task each, before anything else

**Backend 1, gateway.** Load `valio_aimo_junction26_product_data.json` into Postgres with
full-text search over the Finnish and English names, plus columns for category, the claim
`id`s, the pack size from `units.pack_size()`, and `variableUnit`. Then `POST
/gateway/search_products`. Checkpoint: searching "lactose free milk" with the claim filter
returns the 35 products in category `10400` and not the chocolate or coffee milks.

**Backend 2, agent.** The state machine from `ARCHITECTURE.md` section 8, with the trace
decorator on every tool call from the first line of code. Gemini parses the request into the
schema; everything else is code. Use `shared.units.convert()` and `shared.money.line_totals()`; never
compute a quantity or a total anywhere else. Checkpoint: "1,000 L lactose-free milk by
Tuesday" produces a `Basket` matching the example in `docs/CONTRACTS.md` section 2.

**Frontend.** The basket component against that same JSON example, with hard-coded data at
first. Every unit and price field, `null` shown as "unknown" and never as 0, flags as chips,
and the status moving draft -> quoted -> pending approval -> ordered. Then the chat box.
Checkpoint: the hour 0 basket renders correctly from a pasted JSON file.

**Pitch.** Three things, in this order:
1. Re-run `hour0_check.py` once more and write the final beat 1 numbers into
   `docs/HOUR0_FINDINGS.md` section 8. They are the numbers that go on the slides.
2. Write `Backend/data/seed/recipes.json`: 5 to 10 dishes for one restaurant, each ingredient
   a real product id from the catalogue with a quantity per portion in base units. Use milk,
   cream and butter ids that the price API answers for. This is data entry, not coding, and
   block 7 cannot start without it.
3. Then slides, with the five beats of section 14 as the spine.

## The honest gap

The plan assumes four people writing code. With two backend developers, blocks 6 and 7
(the second client, and inventory plus the running-low suggestion) have no owner.

**Decide now rather than at hour 14:** aim for blocks 1 to 5 properly built, and ship the
running-low suggestion from seeded data, so pressing "Simulate today's service" produces a
correct draft basket from a pre-seeded ledger rather than a live forecast. The second client
stays in, because it is a 30-line script and covers most of the openness criterion.

That keeps all five demo beats, with beat 3 driven by seeded stock. Nothing in the demo
script changes; what changes is that the ledger starts with the numbers already in it.

## Checkpoints

| Hour | Must be true |
|---|---|
| 7 | A request produces an editable, correctly priced basket. If not, everyone drops everything and fixes it. |
| 8.5 | That basket goes through to a placed order, unsigned. |
| 10 | Mandate, signed approval and single-use quotes work. |
| 13 | Every exception in the demo runs on cue. |
| 15 | Freeze. Anything unfinished is cut, not squeezed in. |

## Sleep

Two backend developers cannot both sleep at once, or the spine stops.

| Person | Sleeps |
|---|---|
| Backend 1 | hours 8-12 |
| Backend 2 | hours 12-16 |
| Frontend | hours 9-13 |
| Pitch | hours 7-11, then owns the deploy and rehearsal hours while the others sleep |

Neither backend developer sleeps before the hour 7 checkpoint.
