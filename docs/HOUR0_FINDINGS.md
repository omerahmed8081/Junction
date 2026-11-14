# Hour 0 findings: the mock API is live

First real calls to `https://junction26.valioaimo.fi`, 2026-11-14 03:40, for the 35
lactose-free products in category `10400`, `requestedDate` 2026-11-17, customer `123`.
Raw responses: `Backend/Data/mock_samples/`. Reproduce with `Backend/scripts/hour0_check.py`.

The architecture document is frozen. These are the one-line notes it told us to keep here.

## 1. Access

- `/api/ping` → `{"status":"ok"}`.
- **Cloudflare blocks requests whose user agent looks like a script** (403, `error code: 1010`).
  Every client we write must send a browser-like `User-Agent`. Without it the failure looks
  like a bad API key.
- In PowerShell use `curl.exe`, not `curl` (an alias for `Invoke-WebRequest`).

## 2. `comparisonPrice` already gives price per litre or kilogram

Every product returned `comparisonPrice` + `comparisonUnit` (`L` or `KG`), and it matches
a hand calculation to rounding. **Rank on `comparisonPrice` directly; do not compute
price per litre ourselves.**

**It follows the customer price, not the list price**, so ranking on it is ranking on what
this kitchen pays. For `464306`: `catalogPrice` 1.01, `comparisonPrice` 1.01, `listPrice`
1.44. Same for `464401` (1.10 / 1.10 / 1.31) and `464073` (1.64 / 1.64 / 1.95).

Two cautions:

- **It is rounded to cents, so never total with it.** The Novobox is EUR 1.57/L, but the line
  total is 50 x EUR 31.31 = EUR 1,565.50, not 1,000 x 1.57 = EUR 1,570. Show the per-litre
  figure as a comparison and total from the sales-unit price. `money.py` has a helper that
  says when the two disagree.
- **Only rank products whose `comparisonUnit` matches the need.** 34 of the 35 report per `L`,
  `472492` reports per `KG`, and comparing those two numbers is meaningless.

It also gives a size: `catalogPrice / comparisonPrice` = size of one sales unit in
comparison units.

| Product | Derived | True size |
|---|---|---|
| `464306` | 1.0 L | 1 L per ST |
| `463038` | 19.94 L | 20 L per KI (2 × 10 L) |
| `464179` | 10.01 L | 10 L per ST |
| `472492` | 4.99 KG | 5 kg per ST |
| `463779` | 3.75 L | 3.75 L per KI (15 × 2.5 dl) |

`comparisonPrice` is rounded to cents, so the derived size carries up to ~0.5% error.
**Use it as a cross-check on the size rule and as a last-resort fallback** (round to a
sensible pack size), never ahead of structured data. It does mean a product whose size is
only in its name is not lost, so section 3's step 3 ("size unknown") will be rarer than feared.

`units.convert()` now does the cross-check itself: pass the derived size and any
disagreement over 2% raises the flag `size_disagrees_with_price` on the line, so a wrong
size is seen rather than ordered.

## 3. `netPriceFound: false` is not an error

`catalogPrice` is present whether or not it is true, so it means "no customer-specific net
price, the catalogue price applies". 24 of the 35 products have it false, including the
10 L Novobox.

**Correction to the plan:** section 10 said to exclude `netPriceFound: false` from ranking.
That would have dropped most of the catalogue. Exclude a product only when `error` is true or
`catalogPrice` is missing.

## 4. Exceptions occur in the real data, with no scenario switch

For a 1,000 L need on 2026-11-17:

| Product | Signal | Covers |
|---|---|---|
| `464440` Valio semi skimmed 0,5 l | `scheduleLines` empty, no `closingTime` | not deliverable |
| `462775` Arla fat free 1 L | 51 ST available | partial (worst case: 5% of the need) |
| `469363` Arla semi skimmed 1 L | 384 ST | partial |
| `463380` Arla Cafe milk 1 L | 698 ST | partial |
| `463203` Arla portion milk | 192 KI | partial |
| 20 of the 35 products | `closingTime` 2026-11-12T15:00, already past | cutoff passed |

So the scenario switch is only needed for `stock_changed` (and `sales_block`, if no
blocked product turns up in a wider search). Build those two and use real IDs for the rest.

## 5. Sentinel amounts mean "unlimited"

`availableAmount` came back as `999999`, `9999999999` and `666666666`. The plan named only
`999999`. **Rule: any amount at or above 100,000 is "unlimited", not a quantity**, and it is
never shown to the agent as a number (the gateway answers full / partial / none).

`logisticModel` is weaker than it looks and cannot replace the threshold:

| logisticModel | unlimited sentinel | a real count |
|---|---|---|
| `Terminal` | 20 | 0 |
| `Stock` | 10 | 4 |

`Terminal` always meant unlimited here, but `Stock` goes either way, so the threshold stays
the primary test and `logisticModel` is context for the trace. Worth asking a mentor what
`666666666` means.

**`logisticModel` does say something important, though: the 20 `Terminal` products are
exactly the 20 whose cutoff has already passed.** Terminal products are supplier-delivered
and their cutoff is days earlier, so for anything urgent they are usually not orderable.
That is why the agent checks the cutoff before ranking, not after.

## 6. Dates and cutoffs

- `availableDate` is **2026-11-16 (Monday)** for everything, although we asked for the 17th.
  Earliest delivery is Monday, so "by Tuesday" is satisfiable.
- The usable products' cutoff is **2026-11-15T17:00+02:00, Sunday 17:00**. If we demo after
  that, beat 1 turns into a cutoff exception. **Check the pitch time against it** and re-run
  the script on the day.
- VAT is 13.5% on all of these.

## 7. No deposits in the price response

The price response has no deposit field for any of the 35 products. So the basket shows the
deposit unit count from the product file and "deposit charged separately". One mentor
question answered.

## 8. Demo numbers (beat 1)

Plain request, "1,000 L lactose-free milk by Tuesday":

| | |
|---|---|
| Winner | `464306` Valio Hyvä suomalainen Arki Eila semi skimmed 1 l, lactose-free |
| Price | EUR 1.01 per L (cheapest of the 35), EUR 1.01 per ST |
| Quantity | 1,000 ST (1 L per ST) |
| **Net** | **EUR 1,010.00** |
| VAT 13.5% | EUR 136.35 |
| Gross | EUR 1,146.35 |
| Delivery | 2026-11-16, cutoff Sunday 17:00 |
| `netPriceFound` | true (a real customer price) |

**Only 14 of the 35 are orderable at all.** The other 20 have a cutoff of 2026-11-12, already
past, and `464440` has no delivery schedule. The agent must filter on the cutoff before it
ranks, or it will recommend something it cannot buy.

The alternative on the line is **`464401` Valio Arki Eila skimmed 1 l at EUR 1.10/L**
(1,000 ST, EUR 1,100.00 net): same Monday delivery, same Sunday cutoff, EUR 90 more.

**Do not build beat 1 on the 10 L Novobox.** Both Novoboxes, Valio `463038` (EUR 1.57/L) and
Arla `464179` (EUR 2.72/L), are `Terminal` products whose cutoff passed on 2026-11-12. Asking
"in 10 L packs" returns products that cannot be delivered, and swapping to one on stage turns
beat 1 into a cutoff exception nobody planned.

**Recommendation:** ask plainly, as the brief does. The agent picks `464306` at EUR 1,010.00
and offers `464401` as the alternative. Then use the Novobox deliberately, as a second
alternative that is **flagged `past_closing_time` with the reason "cheaper per litre at
EUR 1.57, but its order cutoff passed on 12 November, so it cannot arrive by Tuesday"**. That
is real data, it shows the agent catching something a human reading the webshop would miss,
and it needs no scenario switch.

Note for the slides: among the 14 orderable milks every one is sold as 1 L per unit, so the
quantity is 1,000 units and the case arithmetic does not appear in beat 1. Show that in beat 3
instead, where the running-low suggestion orders cream or butter by the case.

Both options clear the EUR 500 chef limit and stay under the EUR 2,000 mandate limit, so the
limits in the plan need no change. Weekly limit EUR 5,000 covers three runs of either; press
Demo reset before the video and the pitch.

## 9. Still to do today

| What | Why | Who |
|---|---|---|
| Re-run `hour0_check.py` with several dates: `2026-11-17,2026-11-18,2026-11-19,2026-11-20` | Nice to have now that the slot is before Sunday 17:00. Worth 2 minutes to know whether a later delivery date would unlock the `Terminal` products, which would let the Novobox back into the demo. | demo-script owner |
| ~~Find out when we actually present~~ | **Settled: before Sunday 17:00**, so beat 1 is a clean order. If the slot is Sunday afternoon the basket can even say "order before 17:00 today for Monday delivery". | done |
| Call the mock from the deployed host, not a laptop | Cloudflare already blocked one user agent; a cloud address may be treated differently. Test as soon as the empty services are up. | track A |
| Run the script twice a few minutes apart | If stock moves between calls, the real-ID exceptions won't hold on the day | track A |
| Make the scenario switch replay the saved responses in `mock_samples/` | Cheaper than hand-written scenarios, and it gives `partial` back for free if the live data changes | track A, block 3 |
| Make the agent work from "earliest deliverable date" rather than a fixed Tuesday | Every product here offers Monday 2026-11-16 whatever we ask for | track B |

## 10. Corrected after review

- **VAT and gross were wrong in the first version of this note.** They multiplied the API's
  per-unit `valueWithTax`, which is already rounded to cents: `464306` came out at EUR 1,150.00
  instead of EUR 1,146.35, EUR 3.65 too high. VAT is computed once per line from the line's
  net value, and `shared/money.py` is now the only place it happens, with the hour 0 figures as
  its tests.
- The API key is not in the repository: it is read from `JUNCTION_API_KEY`, and the saved
  responses contain only `customerId`. A grep for the key across the repo returns nothing.
- **The first version recommended the 10 L Novobox as beat 1's alternative.** Its cutoff had
  already passed, which would have broken the swap on stage. Caught in review; section 8 now
  uses `464401`, and the Novobox appears as a deliberately flagged example.
