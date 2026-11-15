# Timing test: one order of 100 ingredients

Goal: measure how long it takes to go from "here is my shopping list" to a placed
order of 100 lines, and compare it with doing the same in the Aimo webshop.

The list is in `docs/test100_batches.txt`: 100 everyday restaurant items in 4
messages of 25 (dairy, meat and fish, vegetables and frozen, dry goods).

---

## 1. Before you start (5 minutes, not timed)

1. Open the deployed site in Chrome and press **Ctrl+Shift+R**.
2. **Acting as: Owner** (header). The owner places orders up to the agent's limit,
   so the test is not interrupted by approvals.
3. **Agent rules** -> raise the agent's limit for the test, because 100 lines will
   likely cost more than the EUR 2,000 default:
   - Most per order: **50000**, Most per week: **100000**, Valid for **1 day** -> Sign.
   - Orders stay in our gateway; nothing is sent to Valio as a real purchase.
4. **Order** -> **Start a new basket**, so the test starts from an empty basket.
5. Open DevTools (**F12**) -> **Network** tab -> type `api/` in the filter box ->
   tick **Preserve log**. Every request then shows its exact duration in the
   **Time** column.
6. Have a stopwatch (phone) ready, and `test100_batches.txt` open for copying.

## 2. The timed run

Start the stopwatch when you paste the first batch. Do not stop it until the
order is placed: the whole point is the total, including your own reading.

| Step | What to do | Write down |
|---|---|---|
| A | Paste **Batch 1** into the chat, press Send | time when the agent's reply appears; lines in the basket |
| B | Paste **Batch 2**, Send | same |
| C | Paste **Batch 3**, Send | same |
| D | Paste **Batch 4**, Send | same |
| E | Scroll the basket. Fix anything wrong (Remove, quantity, Alternatives) | how many lines you touched, how long it took |
| F | **Confirm price with Aimo** | time until "Aimo signed this price" |
| G | **Approve and order** | time until "Order ... placed" |

Batches 2 to 4 have no date on purpose: they keep the basket's date (Friday)
from batch 1. The basket stays the same one while it is a draft.

For each Send, the **Time** column of the `chat` request in DevTools is the exact
server time. The stopwatch also includes your copying and reading, which is real
time too.

## 3. Results sheet

| | Agent (this system) | Webshop (by hand) |
|---|---|---|
| Batch 1 (25 items) | | |
| Batch 2 (25 items) | | |
| Batch 3 (25 items) | | |
| Batch 4 (25 items) | | |
| Review and fixes | | |
| Confirm price | | |
| Place order | | |
| **Total, stopwatch** | | |
| Lines found, of 100 | | 100 |
| Lines you had to fix | | |
| Items not found at all | | |

**Webshop baseline:** have one person order the same list in the Aimo webshop
(search, pick, quantity, add to cart, checkout) with a stopwatch. If you cannot
use the webshop, time 10 items there and multiply by 10, and say so in the pitch.

## 4. What to look at, not only the clock

- **Lines found of 100.** An item the agent could not match is time the customer
  spends afterwards. The reply lists what it could not find.
- **Wrong matches.** Lines with "Not exactly what you asked for", or a product
  you would not have picked. Count them: each one is a fix in step E.
- **Stock not confirmed by Aimo.** Expect many; it is Aimo's own answer, not an
  error (see the chat reply).
- **Slow batch.** If a Send takes over a minute, Aimo is rate-limiting us (a
  "still working" note appears after 8 seconds). That is part of the result.

## 5. If something goes wrong

- **Only one item is added from a batch:** the language model did not answer and
  the simple fallback reader took over, which reads one item per message. Send the
  batch again; if it repeats, check `GEMINI_API_KEY` in the server's `.env`.
- **A batch fails with "Aimo did not answer":** send the same batch again. Items
  already in the basket are kept.
- **"Refused: over the mandate":** step 1.3 was skipped. Raise the limit as Owner,
  then Confirm again.
- **Reset afterwards:** Agent rules -> set the limit back (2000 / 5000, 7 days),
  then **Start a new basket**.

## 6. Variations worth one extra run

- **One message with all 100 items** instead of 4 batches: is it faster, or does
  it time out? (It is one long request; a failure here is a useful finding.)
- **As Chef** instead of Owner: the same order then waits for approval. Time how
  long the manager needs from the Approvals page.
- **In Finnish:** translate batch 1 and compare lines found.
