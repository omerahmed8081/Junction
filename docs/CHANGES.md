# Changes

Newest first. One line per change that affects somebody else's work: a field name, a
behaviour, a file that moved. Add to the top when you push.

---

## Sunday 15 November

### 04:35 - state that outlived a reset

Three things survived a reset that should not have. All three had the same
shape: a copy somewhere that nothing cleared.

**1. Audit rows held in memory.** When the database is unreachable, both audit
logs keep rows in a deque and `recent()` reads from there. The reset truncated
the table and left the deque, and the process kept running - so the cleared log
came back. This is why the Activity page showed entries for orders the Orders
page said did not exist. Both sides now have `forget_memory()`, called by their
reset, and it clears the de-duplicator too, or the first repeated step after a
reset would be swallowed.

**2. The chat transcript.** It lives in `sessionStorage`, so the kitchen could
be reset from a terminal and the browser heard nothing. `/api/kitchen` now
reports a `generation` - new on process start and on reset - and the shell
drops the transcript, the remembered basket and the query cache when it
changes. `/api/kitchen` was already fetched on every page load, so this costs
no extra request.

The generation is deliberately not stored, so a plain restart also counts as a
new run and clears the transcript, even though the baskets themselves now
survive in Postgres. That errs toward freshness: it can clear something still
valid, but it can never fail to clear something stale. The baskets are intact
in Approvals either way.

**3. The ledger.** The reset cleared the stored services but not the running
`Inventory`, which is seed plus the services logged since - so stock on screen
stayed where the last rehearsal left it. It is rebuilt from seed now.

**Also removed: two browser stores that were written and never read.**
`tta.steps` collected up to 300 agent steps on every turn and `tta.baskets` a
list of basket ids; `useSteps` and `useKnownBaskets` were called nowhere,
because the pages read the server. A write-only store is how a page ends up
showing yesterday's data, so they are gone, along with `logSteps`,
`logSecurityEvent`, `rememberBasket` and `forgetBasket`. Nothing on screen
changes: agent steps are shown from the chat messages that carry them, and
security events come from the server's audit log. Stale copies of both keys are
removed from any browser that still has them, the first time it syncs.

`lib/session.ts` now holds four things and every one is read somewhere.

317 tests. The generation logic was also run outside the app against stubbed
storage, since the frontend has no test runner: a first visit keeps a
just-typed message, the same generation does nothing however often it is seen,
a new one clears, a backend that reports none changes nothing, and the role
survives.

### 03:40 - all of the state is in Postgres now

Some things lived in the database and some in a JSON file, and the two
disagreed. The frontend could show audit logs for orders the page said did not
exist, because the logs were rows and the orders were a file the gateway never
read. Resetting for a demo meant clearing two places and hoping.

Three new tables, applied with `python scripts/apply_sql.py`:

| Table | What was wrong without it |
|---|---|
| `gateway.orders` | the week's and day's spending were counted from memory, so a restart set them back to zero and the mandate's limits quietly started over |
| `gateway.used_quotes` | this is what stops one quote being ordered twice; a restart emptied it, and an already-ordered quote became orderable again |
| `kitchen.state` | baskets, orders, the mandate and the logged services, as one jsonb document |

`kitchen.state` is deliberately one document row rather than a table per
thing. The shapes are Pydantic models that are still changing, and a schema
chasing them tonight would be a second source of bugs.

**Nothing changed for callers.** `storage.save/load/clear` keep their exact
signatures, and `OrderStore`/`QuoteRegistry` keep their methods and their
in-memory dicts as the working copy, so `daily_net` and `weekly_net` are the
same code as before. The tables are what make them survive a restart.

- **No `DATABASE_URL`, no database:** both fall back to memory and the file, which is how every test runs and how a laptop with no `.env` still works.
- **A write that fails does not lose the thing.** Memory is updated first; a failed row write prints and carries on, because an order Aimo has accepted must not vanish because Aiven blinked.
- **State already in `.state/kitchen.json`** is still found on startup and moves into the row on the next save, so a kitchen pointed at Postgres for the first time does not look empty.
- `scripts/clean_slate.py` now clears everything from one list, and asks the running services to reset *before* truncating, so a service holding baskets in memory cannot write them back over the clean slate.

Checked against a real Postgres 16, not just mocks: the DDL applies twice over,
the upserts and the partial unique index on `(customer_id, idempotency_key)`
behave, and jsonb comes back as a dict. 311 tests pass.

### 02:55 - stopping the agent

There was no way to call a mandate back. Issuing a new one replaced the
kitchen's copy, and Aimo went on accepting the old one until it expired - so
the honest answer to "the agent is misbehaving, switch it off" was "wait seven
days".

A mandate is a signed statement that stands on its own, so this cannot be
something the kitchen does to its own copy alone: whoever holds the token could
keep ordering. **Aimo has to be told, and has to remember.**

- `gateway.revoked_mandates` (in `sql/audit.sql`) holds the ids that must no
  longer be accepted. In Postgres rather than memory because a safety control
  that forgets on restart is worse than none: the screen would still say the
  agent was stopped.
- `POST /gateway/revoke_mandate` verifies the mandate first - a token that does
  not verify is a forgery, not somebody's mandate to call back - and refuses
  unless it names the customer behind the calling key. Revoking twice is not an
  error, because the kitchen may be retrying.
- `place_order` refuses a revoked mandate as `mandate_revoked`, flagged as a
  security event: presenting one is either an agent that should have stopped or
  one that was never meant to have it.
- `DELETE /api/mandate`, owner only, is the button. **Aimo is told first and the
  kitchen lets go only once that lands.** The other order would leave a mandate
  the kitchen has forgotten and Aimo still accepts - the screen saying stopped
  while the token kept working, which is worse than having no stop button.
- `KitchenSigner.stopped` is separate from `mandate is None`. "None" means
  "none yet" and is answered by signing one, which after a revocation would
  hand the agent a fresh mandate a second later. Issuing one clears the stop.
- The rules screen treats the 404 from a stopped agent as an answer rather than
  an error: a banner saying it is stopped, with the issue form still there.
- `clean_slate.py` does **not** clear revocations. Wiping them would quietly let
  a stopped agent order again, which is the one thing a revocation is for.
  `--revocations` does it anyway, for a throwaway database.

Two things the tests pin that the code alone would not say: a database that is
down never turns into a false revocation (answering "revoked" because Postgres
is unreachable would refuse every order in the building), and one customer
cannot call back another's mandate - checked for a 403 specifically, since a
422 would mean the gateway simply could not read the token.

Writing that last test found it was passing for the wrong reason: another test
file points the kitchen's `GATEWAY_URL` at a fake host at import, so the
mandate was addressed somewhere the gateway would not accept and the 403 never
ran. It signs for the right audience now.

`tests/test_revocation.py`, 14 tests. 301 passing, in forward and reverse order.

**Needs `python scripts/apply_sql.py sql/audit.sql` before it works.** Without
the table the list stays in memory and says `degraded`.

### 02:10 - bug report 1, the gateway half

Seven items from the review, each with a test, and the endpoint code itself
finally under test.

- **A1. A seven-day mandate was refused after one day.** The verifier caps a
  token's age at 24 hours, which is right for a quote or an approval and wrong
  for a mandate bounded by its own `exp`. `verify(max_age_seconds=None)` for the
  mandate only; an expired mandate is still refused and an approval keeps the cap.
- **C3. Aimo's answers were cached for ever**, so the re-check at order time got
  the quote's own answer back and `stock_changed` could not happen. Answers now
  live `AIMO_CACHE_SECONDS` (60), and the order-time check passes `fresh=True`.
- **C4. Every caller was priced as one fixed customer.** The caller's customer
  now reaches Aimo and is part of the cache key. It goes through
  `AIMO_CUSTOMER_MAP`, and a customer with no entry is priced on the default
  contract: forwarding our invented `456` would have made the second client fail
  at pricing instead of at the mandate, which is the beat it exists to show.
- **C1. The public key address served the kitchen's key**, so nobody outside
  could verify a quote. `/.well-known/jwks.json` is Aimo's now, and each
  service's key and health check also answer under its own prefix
  (`/gateway/...`, `/api/...`), which is what a shared front door passes through.
- **C9. Quotes were issued by `http://localhost:8001`.** The report's fix was to
  set `GATEWAY_URL` to the public address, which would have broken the site: one
  variable was both the name signed into tokens and the address the kitchen
  calls. `GATEWAY_ISSUER` is the name; `GATEWAY_URL` stays the address. A mandate
  still in force when the name changes is signed again for the new one with its
  limits, categories and expiry untouched, rather than refused on every order.
- **C8. The week was counted in UTC and the day locally**, so an order at 00:30
  on a Monday fell in Monday's budget and last week's. Both are local now.
- **A6. "make it 2,000 L" set the line to two litres.** The edit path had its
  own number reader. There is one now (`need.read_number`), and it also reads
  `1,234.5`, which the old shared one could not.

`tests/test_gateway_http.py` calls the gateway's endpoints over HTTP with the
database and Aimo faked. Until now nothing ran that code before a real request
did, which is how a misspelt attribute returned 500 on every price check.

Writing it found one more: importing `gateway.main` loaded the developer's
`.env` into the test process, and under one test ordering a customer id from it
made later tests refuse every order. `TTA_SKIP_DOTENV` keeps test imports
hermetic. The suite passes in forward and reverse file order.

**Deploying this:** add `GATEWAY_ISSUER=http://<host>/gateway` to the server's
`.env`, run `update.sh` (it reinstalls the Caddyfile), then
`python scripts/smoke_test.py --kitchen http://<host> --gateway http://<host>`.
Baskets confirmed before the restart need confirming again.

Not in this pass: A2, A3, A4, A5 and section B (kitchen files someone else is
in), and section D.

287 passing under pytest, 281 under the script runner.

### 01:30 - the approval note speaks to whoever is looking

A EUR 1,010 basket said "Needs a manager's approval" to the owner too, who can
place it himself. What a basket *needs* is unchanged (`policy.level`); what each
person is *told* now depends on their role:

- Basket screen: within your limit, the panel says "Within your limit as Owner"
  in green, still listing the reasons for the record; above it, the orange
  "Needs a manager's / the owner's approval" as before.
- Chat and confirm replies: `agent.approval_sentence(level, role)`,
  `agent.add_need(..., role=)` and `ordering.confirm(..., role=)` take the role
  from `X-Kitchen-Role`. Both arguments are optional, so other callers are unchanged.
- Test in `tests/test_roles.py`. 269 passing.

### 01:20 - "100,000" is a hundred thousand

The amount fields on Agent rules read "100,000" as 100.000, so a weekly limit
of a hundred thousand came out as EUR 100 and was refused as "lower than the
per-order limit". `parseAmount()` in `lib/format.ts` now reads every way people
type an amount - 100000, 100 000, 100,000, 100.000, 12,50, 1.234,56 - and both
the mandate form and the standing-rules ceiling use it.

### 01:10 - the chat stays with the order until it is placed

Before: "Confirm price with Aimo" cleared the chat and closed the basket to it, so
remembering the cream afterwards meant starting a second order. Now:

- `POST /api/chat` with the `basket_id` of a **quoted** basket adds to that same
  basket. The change voids the signed price (the basket is a draft again, as any
  edit already did) and the reply ends "The price Aimo signed no longer applies
  to the changed basket: confirm it again." Only **ordered** or blocked baskets
  start a new basket.
- Frontend: Confirm no longer clears the chat; it is cleared only when the order
  is placed. `useBasket.ts`, `OrderPage.tsx` (`chatTarget` includes `quoted`).
- If someone else was about to approve that basket, it leaves their Approvals
  list until it is confirmed again: they would otherwise approve a price for a
  basket that no longer exists.
- Test in `tests/test_roles.py`. 268 passing.

### 00:55 - merging the roles branch

Roles and the agent's own permission met in `chat()`. Both belong, and the
order matters: whose basket it is has to be settled before the agent decides
whether it may place it.

Kept from the branch, which is better: `confirm()` deciding the policy again on
Aimo's **signed** total rather than the draft's, the `X-Kitchen-Role` header,
owner-only guards on the mandate and the standing rules, and `created_by` on a
basket. Kept from here: the daily budget, the agent role, the chef-readable
log, ordering by product number.

Two things the merge got wrong on its own, both worth knowing about:

- **`MandateRequest.max_net_per_day` disappeared** with no conflict reported.
  Git took the branch's version of a region that happened to contain a field
  only this side had, and the daily budget silently stopped arriving from the
  rules screen - a 500 on the first mandate anyone tried to sign. A clean
  automatic merge is not the same as a correct one.
- **Three of this side's tests forced `policy.level` by hand**, which the
  branch's `confirm()` now recomputes. They set a real standing rule instead,
  which is what the code actually requires and a better test for it.

`test_roles.py` also gained the `__main__` runner the other files have, so one
loop still runs everything; its two fixture tests stay with pytest.

267 passing under pytest, 262 under the script runner.

### 00:40 - the kitchen names itself

`KITCHEN_NAME` and `GET /api/kitchen`. The title bar asks the backend who it is
rather than carrying the name in two translation files, so the hospital is a
second service with its own `KITCHEN_NAME`, `AGENT_NAME` and seed - and no
frontend change at all.

`Backend/.env` was rewritten while doing it: `AGENT_ID`, `AGENT_NAME` and
`AIMO_CUSTOMER_ID` each appeared twice, and values with spaces were unquoted so
the file could not be sourced by a shell. It is now grouped, commented and
deduplicated, with one key per line.

### 00:25 - the audit reader was breaking the catalogue

`KeyError: 1` from `catalog.resolve_category`, and nothing in the catalogue had
changed. The audit reader did `conn.row_factory = dict_row` on a **pooled**
connection: the pool hands the same connection to everyone, so from then on the
catalogue's own queries got dicts where they expected tuples, and `r[1]` raised.

Intermittent in exactly the way that wastes an evening - it depended on whether
the connection you were handed had been used to read the audit log first, so
search worked until somebody opened the Activity tab.

Both readers now scope the row factory to a cursor, which is where it belonged,
and a test asserts the pooled connection comes back untouched.

Two things worth taking from it: a connection borrowed from a pool is shared
state, and a stack trace from the real thing found in one minute what reading
the code had not.

258 passing.

### 00:05 - the fish, and an Activity panel that follows the order

**"Change the order to 10 kg" gave the right packs against the wrong question.**
Asked for 5 kg of fish in 5.76 kg cases, the chef then asked for 10. Two cases
is correct - 11.52 kg is the smallest whole number of cases covering 10 - but
the line still said *"5 kg -> 2 cases x 5.76 kg = 11.52 kg (+6.52 kg extra)"*,
because the quantity changed and the need behind it did not. It now reads 10 kg
and +1.52 kg extra.

A unit decides whether the need is restated: "10 kg" changes what the kitchen
asked for, "12 units" only changes how many packs are bought. Both are tested.

**The Activity panel follows the order you open.** Expanding an order narrows
the right-hand panel to that order's own story with Aimo's rows merged in, and
a "Show everything" link goes back. The copy that was nested inside the order
card is gone, which is what made it look doubled.

257 passing.

## Saturday 14 November

### 22:40 - a product chosen by number is what was asked for

"20 L 463380" picks Arla Cafe milk outright, but the word "cafe" in its name still
marked the line `differs_from_request` ("Not exactly what you asked for"), which
sent the basket to a manager. A product named by its number is now never flagged
as different (`kitchen/agent.py`, `add_need`). Test in `tests/test_roles.py`.

### 22:30 - three roles: chef, manager, owner

The screen now acts as one of three people, picked in the header ("Acting as").
There is no sign-in yet: the browser remembers the choice and sends it on every
request as the header `X-Kitchen-Role`. A real deployment would read it from a login.

Who may place what (net EUR, Aimo's signed total):

| | places it alone up to | above that |
|---|---|---|
| Chef | `CHEF_LIMIT_EUR` (500) | a manager or the owner approves |
| Manager | `MANAGER_LIMIT_EUR` (1500, new) | the owner approves |
| Owner | the mandate's per-order limit | Aimo refuses; only the owner can raise the mandate |

**Backend**
- `PolicyDecision.level` can now be `"owner"` (over the manager limit). Partial
  delivery and the other warnings still need at least a manager.
- The policy is decided again at **confirm**, on Aimo's signed total, not the draft's.
- `Basket.created_by`: `"chef" | "manager" | "owner"`, from the header of the request
  that started the basket (chat or running-low draft). Adding to someone else's draft
  does not change it.
- `GET /api/baskets?status=quoted` (new): every basket waiting for approval, so the
  Approvals list works across devices instead of per browser.
- `POST /api/mandate`, `POST /api/rules` and `DELETE /api/rules/{id}` answer **403**
  unless the header says `owner`. Standing rules let orders through without
  approval, so they are a spending decision like the mandate.
- Standing rules still win: a basket a rule clears is `auto_ok`, anyone can place
  it, and `approver_role: "agent"` is still accepted for it and only for it.
- `GET /api/mandate` gains `role_limits: {"chef": 500, "manager": 1500}`.
- Approve was already checked by rank (`ROLE_RANK`); unchanged, but now the frontend
  sends the real role instead of always `owner`.
- Chat replies say who has to approve. New tests: `tests/test_roles.py` (8). 251 pass.

**Frontend**
- `lib/session.ts`: `useRole`, `setRole`, `canApprove`. `api/client.ts` sends
  `X-Kitchen-Role` on every request and signs approvals as the current role.
- Order page: above your limit, "Approve and order" is replaced by "Waiting for a
  manager's / the owner's approval" with a link to Approvals.
- Approvals: "You can approve" and "Waiting for someone else", each card says who
  started it and whose approval it needs. The nav badge counts only what you can approve.
- Agent rules: "Who can place what" card; the mandate form and the standing-rules
  form are read-only unless Owner. A basket a rule clears shows "No approval needed".
- `api/schema.d.ts` regenerated (it was also behind on `AgentStep.facts` / `for_chef`).

### 23:40 - the Activity tab reads the backend, not the browser

The steps panel kept its rows in the browser, which is why they survived a
backend restart, why it needed a "Clear steps" button, why the demo had to run
in one session - and, once an order could be expanded, why the same rows
appeared twice.

It now reads `GET /api/audit`. The "What Aimo sees" toggle maps to
`crossed_only`, the rows survive a reload, they are the same on any machine,
and a demo reset clears them along with the orders they belong to.

Checked in a real browser rather than assumed, which turned up two things the
code review would not have:

- **The rank step printed a Python dict.** `f"{reasons}"` put
  `{past_cutoff: 18, sold by weight, not volume: 1}` into a line meant for a
  chef. Written out in words now, commonest first.
- **`parse_need` appeared twice**, once from the chat and once from the agent.
  The chat's row is now `message` and carries what was actually typed, which is
  the thing an audit log should have and the agent's row does not.
- And the text tidier turned **"1,000 L" into "1, 000 L"** while normalising
  commas. It leaves commas inside numbers alone now.

255 passing.

### 23:20 - "with lactose"

A chef looking at a basket of lactose-free milk typed *"with lactose"* and got
back "I could not find a quantity and unit". It is a perfectly ordinary thing
to say and it parses as no request at all, so it fell through to the new-item
path and failed there.

That is a third kind of edit: changing what the product on a line **is**, as
opposed to how much of it. `agent.respec()` re-runs the line's original need
with the requirement changed and swaps the line to whatever now ranks first, so
the quantity, the delivery date and the unit conversion all stay where they
were.

- *"with lactose"*, *"normal gluten"* drop a claim; *"lactose free"*,
  *"make it gluten free"*, *"vegan"* add one.
- *"something cheaper"*, *"a different one"* re-rank and exclude the product
  already in the basket, because "something else" means something else.
- An amount in the message makes it a new request instead: *"1,000 L
  lactose-free milk"* asks for something, *"lactose free"* changes what is
  already there. The amount is the whole difference, and a test pins both.

Found on the way: the amount check was not case-insensitive, so "1,000 L" did
not read as an amount and a new request was being treated as an edit.

4 more tests, 255 passing.

### 23:00 - the agent can finish the job

A standing rule used to mark a basket `auto_ok` and then leave it sitting there
waiting for the click it had just said was unnecessary. Now the chat puts it
through.

**Two separate permissions have to line up**, and either one missing does
nothing: a standing rule the owner set covers the basket, and the mandate in
force does not require Aimo to be shown a human approval. The first is the
kitchen deciding nobody needs to look; the second is Aimo agreeing not to
demand proof that somebody did.

- The order is placed with `approver_role: "agent"`, which the backend accepts
  **only** for an `auto_ok` basket. A test places a `chef` and a `manager`
  basket as the agent and checks both are refused, because that is the guard
  that matters - the agent can always sign a perfectly valid approval, so the
  level is the only thing stopping it.
- A rule says nobody *has* to look, not that nobody may: a person can still
  approve an `auto_ok` basket, and that is tested too.
- Anything that goes wrong leaves the basket a draft for a person, and says so
  in the chat. An order that quietly fails to go through is worse than one that
  waits.
- `AUTO_ORDER=false` turns it off without touching the rules.

Found while wiring it: the approval check compared the approver's role against
`ROLE_RANK`, which holds roles, and `auto_ok` is a *level* nobody holds. It fell
through to the default and the agent failed the very check it had just passed.
Levels now have their own ranking.

3 more tests, 251 passing.

### 22:40 - a daily budget

`MandateLimits` gained `max_net_per_day`, default **EUR 2,000** against the
week's 5,000 (`MANDATE_PER_DAY_EUR`). Aimo refuses an order that would take the
day over it, before it looks at the week: the day is the tighter limit and the
one a chef recognises when an order is turned down. The refusal reads "it is
over your budget for today".

**The day is the kitchen's own day, not UTC.** A Helsinki kitchen's Monday
starts at their midnight; counting in UTC would roll the day over at 02:00
local and put an evening order into tomorrow's budget. `LOCAL_UTC_OFFSET_HOURS`
(default 2) sets it, and a test pins the behaviour either side of local
midnight.

The limit is optional, so a mandate signed before today keeps working and
simply has no daily ceiling. The rules screen shows it, and leaving the box
empty is a real choice rather than a mistake.

5 more tests, 248 passing.

### 22:20 - a clean slate before deploying

`scripts/clean_slate.py` clears everything the demo has written: both audit
logs (TRUNCATE ... RESTART IDENTITY, so a fresh demo starts at row 1), the
kitchen's state file, and - if the services happen to be up - the gateway's
in-memory orders, used quote ids and weekly spend, which nothing outside that
process can reach.

**Dry run by default.** It lists what would go and changes nothing until `--yes`.

What it will not touch, on purpose: the catalogue (`gateway.products`, 17,546
rows and an 84 MB reload), the signing keys in `Backend/.keys/` (a new key
invalidates every mandate already issued and has to be re-registered), and the
seeded restaurant. Standing rules are kept too - they are a setting rather than
demo traffic - unless you pass `--rules`.

### 22:00 - approval is the kitchen's choice, and the rules screen is simpler

- **`MANDATE_APPROVAL_REQUIRED`, off by default.** A fresh mandate no longer
  demands that Aimo be shown a signed human approval, so the box on the rules
  screen starts unticked. A person is still in the loop either way: the
  kitchen's own policy sends every basket to someone unless a standing rule
  clears it. The flag only says whether *Aimo* refuses an order that arrives
  without an approval.
- **A sent approval is now always verified and recorded**, not only when the
  mandate demanded one. Found by a test: with the flag off, the gateway skipped
  verification entirely and `approver_role` fell out of the order record. The
  kitchen usually does have a person approve, and throwing that away would lose
  who signed off from both the record and the audit trail.
- The "What triggers an extra check" card is gone from the rules screen. The
  standing-rules card now carries the one thing that needed saying: what a rule
  cannot do.

243 passing.

### 21:40 - standing rules, and one order's whole story

**`GET /api/orders/{id}/audit`** - one call for the Activity tab. Click an
order, get both halves of its story in one list, oldest first, each row tagged
`side: "kitchen"` or `side: "aimo"`. The kitchen's rows come from its own log;
Aimo's come from the gateway as an ordinary agent request, so it can only ever
return rows for the customer behind our key. Only the approval row carries the
order id, so the basket that became the order is pulled in too - that is most
of the story.

**Standing rules** (`kitchen/rules.py`, `kitchen.rules`). "Milk under 200 euro,
do not ask me." A rule says in advance that a kind of order is routine enough
that nobody needs to look.

- Matches on `any`, `category`, `product`, `claim` or `name`, with an optional
  ceiling. `GET/POST/DELETE /api/rules`.
- A rule **only relaxes**, never blocks, and the signed mandate still sits above
  it, so the worst a wrong rule can do is skip a click.
- **Every line has to match.** A rule about milk does not wave through a basket
  that also holds rice - nobody said anything about the rice.
- **It never clears a reason about the order being wrong.** Partial delivery, a
  product that differs from what was asked for, a pack size that disagrees with
  the price: a person still looks, whatever the rules say. A rule clears reasons
  about size, which is what it is for.
- Rules are switched off, never deleted: one in force when an order went through
  has to stay readable afterwards.
- A cleared basket gets `policy.level = "auto_ok"` with the rule quoted back as
  the reason, and `approver_role: "agent"` is accepted for it - and only for it.
  The agent cannot sign off on anything a person would have had to look at.

These stay in the kitchen's schema and Aimo never reads them. What Aimo enforces
is the mandate, signed. Shipping the rule list to the wholesaler would hand them
the customer's internal approval policy, which is the one thing this design is
trying not to do.

`tests/test_rules.py`, 15 tests, most of them pinning what a rule may not do.
242 passing.

Also: `AGENT_NAME` is a per-kitchen environment variable and `MandateView` now
carries `agent_name`, so the rules screen can show "Saaristo kitchen agent"
while the signature stays made out to `agent:saaristo-kitchen`. The gateway's
demo agent entry reads `AGENT_ID` and `AIMO_CUSTOMER_ID` from the same
environment the kitchen does, so the customer a mandate is signed for and the
customer a key maps to cannot drift apart - they did once, and the symptom was
search and quoting working while every order was refused.

### 21:05 - changing a basket by saying so

"make it 20", "drop the rice", "only 10 kg of butter". The basket's buttons
already did all three; `kitchen/edits.py` reads the same intentions out of a
sentence and turns them into the `LineEdit` the basket already understands, so
there is one set of rules for changing a basket rather than two.

Checked **before** the message is read as a new product, because "only 10 kg of
butter" parses as a perfectly good new request and would otherwise add a second
butter line.

Finding which line is meant is the fiddly half, and the risk is not failing to
understand - it is understanding wrongly and silently changing the wrong line of
somebody's order. So:

- A line is matched on the words of its name, on a six-digit product number, or
  on "it" when the basket holds exactly one line and nothing else is meant.
- **Two lines matching equally asks which.** A basket with whipping cream and
  cooking cream in it answers "Did you mean Valio whipping cream and Valio
  cooking cream?" rather than picking one.
- **Nothing is changed when no line is named.** Two lines and "make it 20"
  points at neither, so it does nothing.
- A spoken amount goes back through the pack size and lands on whole packs -
  7 L of a 2 L pack is four packs - and the reply says so.
- A six-digit number is a product, never an amount.

Most of the 14 tests pin what is *not* an edit: a new request, a date follow-up,
an empty basket, a zero amount.

227 passing.

### 20:00 - frontend restyle: class names only, no behaviour change

White page, soft grey cards with large corners, black pill buttons, one lilac
button for the main action (confirm, approve, sign mandate), heavier headings.

- All colours and radii are in the `@theme` block of `Frontend/src/index.css`.
  New tokens: `card`, `accent`, `accent-hover`, `link`. `rounded-lg` is now 1rem.
- `Button` gained `variant="accent"`. `Card` is grey and borderless; a card
  inside a card turns white (`.tta-card .tta-card`).
- Not touched: `api/`, `lib/`, `i18n/`, `useBasket.ts`, `useWaitingBaskets.ts`,
  the fixtures. No new strings, no new dependencies. `npm run build` passes.

### 19:45 - the chat keeps working on one order until its price is signed

Behaviour the chef asked for, with two small backend changes and one fix.

**`POST /api/chat`** (`kitchen/main.py`)
- With `basket_id` set, every message adds to that basket **while it is a draft**.
  A basket that is `quoted` or later is closed to the chat: the message starts a new
  basket instead of quietly voiding the signed price.
- A follow-up that names **no date** keeps the basket's date. It used to move the whole
  basket to the parser's default (today + 3). New `need.mentioned_date()` tells "named a
  day" from "the parser filled one in"; it reads English and Finnish weekdays,
  tomorrow/huomenna, ISO dates and `18.11.`, and never a decimal like `3.5 kg`.
- A message that names a **different date** moves the basket and re-checks every line
  for that day with `agent.reprice`. A message that is only a date ("deliver on
  Wednesday instead", "toimitus torstaina") does just that.

**`PATCH /api/basket/{id}`** with only `requested_date` now re-checks every line
(`reprice`) instead of relabelling the date. **New `DELETE /api/basket/{id}`**: drafts
only, `409` for anything signed or ordered, recorded in the audit log.

**Fix:** `gateway/main.py` `check_delivery` audit line read `r.can_fulfil` and
`r.price_per_sales_unit`; they are `r.delivery.can_fulfil` and
`r.price.price_per_sales_unit`. Every price check returned 500 since the audit-log commit.

**Frontend:** chat adds to the open draft; after "Confirm price" the chat clears and
says the next message starts a new order; "Delete order" (with a confirm step) on drafts.

`tests/test_chat_dates.py`, 5 tests. 200 passing.

### 20:40 - the same ground, reached twice

Two of us built delivery-date follow-ups at the same time, on different
branches. What survived the merge, and why:

- **`mentioned_date()` is the one date detector** and `date_only_change()` now
  calls it. It reads English and Finnish weekdays, tomorrow/huomenna, ISO dates
  and `18.11.`, and never mistakes `3.5 kg` for a date. The English-only version
  written on main was dropped.
- **What was kept from the other side** is the second half of the question:
  whether a message points at the basket at all. "move it to monday" does;
  "20 kg butter on friday" is a new request that happens to name a day. A
  message that is nothing but a day counts as pointing at it.
- **`chat()` was taken whole from the branch.** It closes a quoted basket to the
  chat rather than quietly voiding a signed price, and keeps the basket's date
  when a follow-up names none - both of which the version on main got wrong.

Carried over from main, unchanged by the merge:

- **Two weekdays in one sentence picked the wrong one.** "by friday not
  thursday" gave Thursday, because the parser walked Monday to Sunday and
  returned the first name it found in the week rather than the first one said.
  The day said first now wins. This was in both branches and affected ordinary
  requests, not only follow-ups.
- **Ordering by product number**: "40 units of 464306", "#464306 x 40". A
  product named by number is ordered, not ranked against.


### 19:20 - several items in one message, and a log a chef can read

Two things the screen made obvious.

**One message, every item in it.** "40 L milk and 5 kg rice by Tuesday" drafted
only the milk, because the model was asked for a single need per message - and
nobody shops one item at a time. The prompt now asks for a list, `parse_needs()`
returns every product asked for in the order asked, and the chat adds them all to
the same basket. A date or a dietary word that applies to the whole message
applies to every item. If the gateway cannot answer for one product the others
still land, and the reply names the one that did not.

The rules fallback still finds only one, which is a fair illustration of what the
model is actually for.

`kitchen/need.py` gained `needs_from_reply()`, split out so the list handling is
testable without calling Gemini: a bad entry among good ones is dropped rather
than losing the message, an invented claim is ignored, a lowercase unit is
accepted, a bare object still parses, and a runaway list is capped at ten.

**The kitchen's log is written for a chef now.** It read like a stack trace:
`policy_binding`, `verify_quote`, `464411 x 40 for 2026-11-20`. A chef does not
know what a signature or a mandate is and should not have to.

- `kitchen/narrate.py` turns each step into a sentence in kitchen words:
  *"Aimo locked in the price, so it cannot change while you decide."*
  *"Confirmed the price really came from Aimo and had not been altered."*
- Machine codes are translated on the way out - `size_in_KG_but_need_in_L`
  becomes "sold by weight, not volume", `2026-11-16` becomes "Monday 16
  November", `EUR` becomes `€`, and a quote id becomes "this basket".
- `for_chef` marks the rows that belong in the short version. The engineering
  steps are still recorded, just not in the way, and every step keeps its
  original terse line in `facts` - nothing is lost, it is one toggle away.

Aimo's side is deliberately the opposite and unchanged: full requests, responses,
timings and token ids.

10 more tests, 195 passing.

### 18:50 - the kitchen's half of the audit log

Both halves now exist, and the tables are live on Aiven.

- `kitchen/audit.py` writes to `kitchen.audit_log`: the sentence somebody typed,
  which parser read it and what it made of it, every step the agent took, the
  quote the kitchen accepted and what Aimo's signature said it cost, and who
  approved it with what outcome. A refusal or an adjustment is recorded with the
  reason in a sentence a person can read a week later.
- `GET /api/audit`, newest first, with `?basket_id=`, `?order_id=` and
  `?crossed_only=true`. That last one is the "what Aimo sees" toggle, and the
  point of it is how few rows come back: the message, the parse and the unit
  conversion never leave the building.
- The kitchen opens its **own** small pool (max 2) for its own schema only. It
  never reads the gateway's. Without `DATABASE_URL` it keeps rows in memory and
  says so.
- The two logs join on `quote_jti` and `order_id`, so a dispute can line up both
  halves without either side holding the other's data.

Caught while wiring it: the first version imported `token_summary` from
`gateway.audit` into the kitchen, which would have made the kitchen depend on
the gateway's code - the separation is the architecture, not a diagram. The
kitchen has its own `quote_id_of` now, and a grep proves neither `kitchen/` nor
`shared/` imports `gateway/`.

6 more tests, 185 passing.

### 18:20 - the gateway's audit log

The audit trail Valio would read. Append-only, in Postgres, one row per call the
gateway served - including every refusal, which is the row that matters most.

- `sql/audit.sql` creates **two** tables: `gateway.audit_log` and
  `kitchen.audit_log`. Two, not one, because a shared table would put the
  kitchen's menu, stock and staff inside Aimo's system. They join on `quote_jti`
  and `order_id` when a dispute needs both halves. Run it once against Aiven.
- `gateway/audit.py` writes a row for every call: who (agent, customer, and a
  12-character hash of the key - never the key), what was asked, what came back,
  how long it took, the outcome, the refusal reason in the gateway's own words,
  and the quote and order it belongs to. A signed token is recorded by its id,
  issuer and expiry, never its bytes.
- `security_event` flags the rows somebody should actually look at: a signature
  that did not verify, a mandate for another customer, a quote used twice, a
  request naming an agent the key does not belong to.
- `GET /gateway/audit` reads it back, newest first, scoped to the caller's own
  customer, with `?order_id=`, `?quote_jti=` and `?security_only=true`.
- If the database is unreachable the log keeps the last 2,000 rows in memory and
  says `degraded: true` with a note, because a silent gap in an audit trail is
  worse than a loud one.

Also fixed while wiring it: `create_quote` checked whether the request named a
different agent than the key **after** doing the database and Aimo work for it.
Now first, before anything is spent on a caller who is about to be refused.

`tests/test_audit.py`, 11 tests, no database needed. 179 passing.

**Still to do on this:** the kitchen's half. `kitchen.audit_log` has its table but
nothing writes to it yet, so "why did my agent buy that" is still only the steps
the browser keeps, and they vanish on refresh.

### 17:25 - the gateway now knows who is calling

Aimo prices by contract, so the same product is not the same price for every
customer. That makes the caller's identity part of a correct answer, and until now
the gateway had none: no authentication at all, the customer read from a single
`AIMO_CUSTOMER_ID` environment variable, and the agent id simply asserted in the
request body. The architecture claimed "every agent is identified by its API key,
there is no anonymous mode"; that is now true rather than aspirational.

- `gateway/agents.py` maps an `X-API-Key` to one agent and one customer. Keys come
  from `AGENT_KEYS` (`key|agent_id|customer_id|label`, comma separated) and are read
  on each call, so a key added to the environment needs no restart. Two demo agents
  are the default: the kitchen (customer 123) and the second client (customer 456).
- Every gateway endpoint that touches catalogue, price or order data requires a key
  and answers 401 without one. `/health` and the JWKS stay public, because an agent
  must be able to fetch the public key before it has anything else.
- A quote is now issued for **the customer behind the key**, and the request body may
  no longer name a different agent than the key does - that was the way one agent
  could have had a quote issued to another and ridden its mandate.
- `place_order` refuses a quote issued to a different agent than the caller, and
  `GET /gateway/orders/{id}` answers 404 rather than 403 for another customer's
  order, because 403 would confirm the id exists.
- `/gateway/demo/reset` is the one endpoint that still works without a key: the
  reset button is in the frontend, which holds none. With a key it resets that
  caller's customer, without one the demo kitchen's.
- The kitchen sends `AGENT_API_KEY` (default `demo-kitchen-key`) on every gateway
  call.

This also sharpens the second client: it now has its own key and its own customer,
so it is visibly a different tenant rather than the same one wearing a hat. It still
cannot order, for the same reason as before - no mandate.

`tests/test_agents.py`, 7 tests. 168 passing.

Also: `ChatResponse` now carries `parser` ("gemini" or "rules"). The fallback is
silent by design - the basket is still correct - so this was the only thing missing
to tell from outside which one answered, and the smoke test checks it. New
`scripts/check_gemini.py` calls the model directly and, when it fails, prints
Google's error and lists the models the key can actually use.

### 17:05 - first time either service was actually started

The 161 tests all run offline with stubs, so no endpoint had ever been called. Booting
both services found six real problems, five of them things that would have broken on the
hosted build rather than locally.

- **`Backend/.env` was malformed.** Two prose lines with no `#`, `GEMINI_API_KEY` defined
  twice (an empty one first), and CRLF endings putting a trailing `\r` in every value.
  `python-dotenv` tolerates that; Docker's `--env-file` does not, and the API key would
  have arrived at Aimo with a carriage return on the end. Rewritten; `.env.backup-before-fix`
  is beside it.
- **The gateway trusted no kitchen by default.** With `KITCHEN_JWKS` unset the gateway
  registered zero kitchen public keys, so search, price and quote all worked and **every
  order was refused** as `mandate_invalid` - a failure that only appears at the last step
  of the demo. There is now a same-host default, and a loud startup warning when no key
  gets registered.
- **An empty kitchen verifier was cached forever.** If the kitchen was not up when the
  first order arrived, every later order was refused until the gateway was restarted. It
  now re-fetches until it actually holds a key.
- **`/health` hung on the database.** Aiven's free tier powers off when idle and takes
  about 30 s to wake; a health check that blocks that long gets the container killed and
  restarted in a loop. It now answers in at most 2 s and reports `degraded` with the
  reason, still as a 200 so a host waits instead of restarting.
- **The gateway had no CORS.** The kitchen had it; the gateway did not, so the demo reset
  button and any browser-based second client would have been blocked.
- **Two tests depended on the real clock.** `test_ordering` pinned `NOW` to 14:00 UTC but
  built an expiry and a mandate `iat` from `datetime.now()`. They passed all morning and
  began failing at 16:00 local, which is exactly the kind of thing that fails on stage.
  Both now derive from `NOW`.

Also found and fixed: the rules parser turned *"we're low on cream"* into the product
`"re low on cream"`, because the filler list matched `we` inside the contraction and the
apostrophe was stripped afterwards. Contractions are now removed first and the filler
list covers how people actually ask ("running out of", "low on", "another"). And a Gemini
failure was swallowed silently - a wrong `GEMINI_MODEL` would have fallen back to the
rules on every message with nothing said. It now logs, and the reply already carries
`parser`.

New: `scripts/smoke_test.py` walks the whole path over HTTP against a hosted pair and
names what is broken, with timings. `deploy/Caddyfile` and `deploy/nginx.conf` put one
origin in front of the frontend and both services - needed because the frontend uses only
relative paths and Vite's proxy does not exist in a built site. README has a hosting
section with the environment variables that matter.

Not verifiable from this sandbox (HTTPS only, no Postgres port and no Gemini host): the
gateway's catalogue queries, and one live Gemini call. Both need running on a machine that
can reach Aiven and Google.

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
