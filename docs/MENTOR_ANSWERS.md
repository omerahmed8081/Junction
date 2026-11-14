# Valio's answers, and what changed because of them

Asked Saturday 14 November, answered the same day. Five questions, each with what we
changed. The code citations are so nobody re-litigates a decision Valio already settled.

## 1. Is there an order API, and will webshop accounts allow ordering?

> A web shop account is available at the booth but orders cannot be placed (it is
> production). This challenge does not have an order API so it is enough that your solution
> can produce orders for human approval, they don't need to be sent anywhere.

**This settles the biggest open question in `ARCHITECTURE.md` section 17.** The order step is
entirely ours, and an order never leaves our gateway. So:

- Our `place_order` is the real thing for this challenge, not a stand-in for an Aimo call.
- Nothing has to be reversed or cleaned up after a demo run, beyond our own Demo reset.
- "Produce orders for human approval" is exactly the flow we planned: draft, signed price
  confirmation, human approval, order recorded. No change needed, and the pitch can say the
  commit step is designed to sit behind whatever Aimo eventually exposes.

## 2. Is `closingTime` the cut-off, and will the data shift by Sunday?

> closingTime is the cut off for related to availableDate. The mock data will remain the
> same for the weekend, in real life you would see shifting.

**Good news for the demo: the recorded responses stay valid.** So

- The real exception products hold: `462775` keeps its 51 units, `464440` keeps its empty
  schedule, and the 20 past-cutoff products stay past.
- The scenario switch is needed only for `stock_changed`, which no live product will show.
- The cut-off belongs to a delivery date, not to the product as a whole, so when we start
  offering alternative dates (block 3) the cut-off has to be read from the schedule line we
  chose, not from the product.
- The clock still moves even though the data does not: `464306`'s cut-off is Sunday 17:00,
  so a demo after that turns beat 1 into a cut-off exception. We present before then.

## 3. What do 999999 / 666666666 / 9999999999 mean, and what does an empty response mean?

> Availability ge 999999 means that we do not know the availability and our current logic
> allows ordering any quantity. These values typically appear on terminal products that are
> not stocked by us and therefore availability is unknown. Empty = not available.

**We had this half right.** We treated a sentinel as "unlimited", which is a promise Aimo is
not making. Changed:

- The threshold is now **>= 999999** exactly as stated, not our guessed 100,000
  (`AVAILABILITY_UNKNOWN_FROM` in `gateway/aimo_client.py`).
- The delivery answer carries **`availability_known: false`**, and the basket line is flagged
  `availability_unknown`. The quantity is still orderable, because Aimo's own logic allows it.
- The line's reason says "orderable, though Aimo does not confirm stock" rather than claiming
  the full amount is available.
- An empty `scheduleLines` list now answers `none` with the reason `not_available`.
- It is deliberately **not** an approval reason: 10 of the 14 orderable lactose-free milks
  carry a sentinel, so requiring a manager for them would send nearly every basket to
  approval. It is information on the line, not a gate.
- It also explains our own finding that the 20 `Terminal` products are exactly the 20 past
  their cut-off: they are supplier-delivered, not stocked, so both the cut-off and the stock
  figure behave differently.

## 4. Is `allowedLotSize = 0.01` real?

> You should treat 0.01 as 1. There is some technical reason for those values but 0.01
> crates is not possible like you already guessed.

**Confirms what `shared/units.py` already does:** a lot size below 1 means whole sales units,
which is what rounding up to an integer gives. The few hundred products with 1.5, 2, 3 or 5
are honoured as a real step. The docstring now quotes this so nobody "fixes" it later.

## 5. What are the rate limits?

> This will be adjusted as we go but hitting the limit will return 429 and you will be
> blocked for 30 seconds, after which you can continue so please implement something that
> honors 429 responses. A few hundred calls per minute is ok, over 10 seconds not ok.

**This one needed real work; we had nothing.** `gateway/aimo_client.py` now:

- waits out a 429 and retries, twice at most, then raises `AimoRateLimited`
- honours `Retry-After` when sent, and otherwise waits the stated 30 seconds
- spaces calls at least 0.25 s apart, about 240 a minute, inside "a few hundred a minute"
  while never bursting them into ten seconds
- caches each question, so a repeated shortlist costs nothing

Twelve tests in `tests/test_aimo_client.py` cover this with a stubbed HTTP client, so they
run offline and never spend a real call.

**Still worth doing:** the catalogue load and `scripts/find_products.py --price` can both make
many calls. The load script reads the JSON file rather than the API, so it is fine; keep
`--price` to a shortlist.

## What to ask next

- Does `Retry-After` actually appear on a 429, or do we always wait 30 seconds?
- For block 3: when a cut-off has passed, does a later `requestedDate` open a later cut-off
  for the same product? That decides whether the 10 L Novobox can come back into the demo.
