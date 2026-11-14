"""Hour 0 check: ping the Aimo mock, then price + availability for the 35
lactose-free products in category 10400. Saves the raw responses and prints
one line per product. No dependencies beyond Python 3.

Usage (PowerShell):
    $env:JUNCTION_API_KEY = "<api key>"
    python Backend/scripts/hour0_check.py 2026-11-17
    python Backend/scripts/hour0_check.py 2026-11-17,2026-11-18,2026-11-19,2026-11-20

Pass several dates to see whether closingTime moves with the requested date. That
answers the question that decides the demo: if we pitch after Sunday 17:00, does the
hero request still work on a later delivery date?
"""
import json, math, os, sys, urllib.request, urllib.error
from pathlib import Path

BASE = "https://junction26.valioaimo.fi"
KEY = os.environ.get("JUNCTION_API_KEY")
DATES = (sys.argv[1] if len(sys.argv) > 1 else "2026-11-17").split(",")
CUSTOMER = sys.argv[2] if len(sys.argv) > 2 else "123"
OUT = Path(__file__).resolve().parent.parent / "Data" / "mock_samples"

# id: (sales unit, base units per sales unit, litres per base unit or None, name)
MILKS = {
 "463038": ("KI", 2, 10, "Valio Eila semi skimmed 10 l novobox"),
 "464179": ("ST", 1, 10, "Arla 10 L Novobox semi skimmed"),
 "464201": ("ST", 1, 1, "Valio semi skimmed 1 l UHT"),
 "464398": ("ST", 1, 1, "Valio Eila semi skimmed 1 l UHT"),
 "464306": ("ST", 1, 1, "Valio Arki Eila semi skimmed 1 l"),
 "464401": ("ST", 1, 1, "Valio Arki Eila skimmed 1 l"),
 "463335": ("ST", 1, 1, "Valio Eila 3% 1 l"),
 "464073": ("ST", 1, 1, "Valio whole milk 1 l UHT"),
 "469363": ("ST", 1, 1, "Arla semi skimmed 1 L UHT"),
 "462775": ("ST", 1, 1, "Arla fat free 1 L UHT"),
 "464119": ("KI", 10, 1, "Valio skimmed 1 l UHT"),
 "465039": ("KI", 5, 1, "Valio Eila semi skimmed 1 l"),
 "462921": ("KI", 5, 1, "Valio Eila Luomu semi skimmed 1 l"),
 "476240": ("KI", 5, 1, "Valio Eila skimmed 1 l"),
 "469330": ("KI", 5, 1, "5 x Arla semi skimmed 1 L ESL"),
 "469386": ("KI", 5, 1, "5 x Arla fat free 1 L ESL"),
 "464768": ("KI", 12, 1, "12 x Arla whole 1 L UHT"),
 "469119": ("ST", 1, 1.75, "Valio Eila skimmed 1,75 l"),
 "469287": ("KI", 12, 1.75, "Valio Eila semi skimmed 1,75 l"),
 "464440": ("ST", 1, 0.5, "Valio semi skimmed 0,5 l UHT"),
 "464198": ("KI", 5, 0.5, "Valio Eila semi skimmed 5 dl"),
 "465149": ("KI", 6, 0.5, "6 x Arla low fat 5 dl ESL"),
 "463779": ("KI", 15, 0.25, "Valio Eila semi skimmed 2,5 dl UHT"),
 "466288": ("KI", 15, 0.25, "Valio semi skimmed 2,5 dl UHT"),
 "464399": ("KI", 14, 0.2, "14 x Arla low fat 2 dl UHT"),
 "463203": ("KI", 1, 2, "Arla portion milk 100x2 cl"),
 # not plain milk (coffee milk, sour milk, latte): listed to see them, not to pick them
 "462626": ("KI", 10, 0.5, "[coffee] Valio coffee milk 5 dl"),
 "463380": ("ST", 1, 1, "[coffee] Arla Cafe milk 1 L"),
 "464237": ("ST", 1, 1, "[coffee] Valio coffee milk 1 l"),
 "473142": ("KI", 10, 1, "[coffee] Valio coffee milk vanilla 1 l"),
 "469266": ("KI", 5, 1, "[coffee] Valio Latte 1 l"),
 "469331": ("KI", 5, 1, "[sour] 5 x Arla AB sour milk 1 L"),
 "476957": ("ST", 1, 1, "[sour] Valio Gefilus AB sour milk 1 l"),
 "477050": ("ST", 1, 1, "[sour] Valio Eila skimmed sour milk 1 l"),
 "472492": ("ST", 1, None, "[sour] Valio skimmed sour milk 5 kg"),
}

# Cloudflare sits in front of the mock and rejects Python's default user agent
# with "error code: 1010", so send a browser-like one.
HEADERS = {
    "X-API-Key": KEY or "",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}


def call(path, body=None):
    req = urllib.request.Request(BASE + path, headers=HEADERS,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")

def totals(net_unit_price, qty, tax_rate):
    """Net, VAT and gross. VAT once, from the line net, never from a rounded
    per-unit gross: 1,000 x 1.15 is EUR 3.65 more than 1,010.00 + 13.5%."""
    net = round(net_unit_price * qty, 2)
    vat = round(net * (tax_rate or 0) / 100, 2)
    return net, vat, round(net + vat, 2)


def run_one(requested_date):
    req = {"customer": CUSTOMER, "requestedDate": requested_date, "products": list(MILKS)}
    ps, price = call("/api/price", req)
    as_, avail = call("/api/availability", req)
    suffix = requested_date.replace("-", "")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"price_milk_{suffix}.json").write_text(json.dumps(price, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / f"availability_milk_{suffix}.json").write_text(json.dumps(avail, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n=== requestedDate {requested_date} | price {ps} | availability {as_} | saved to {OUT}")
    if ps != 200 or as_ != 200:
        print("    a call failed; open the saved files for the error body")
        return

    prices = {p["productId"]: p for p in price.get("products", [])}
    avails = {p["productId"]: p for p in avail.get("products", [])}
    rows = []
    for pid, (su, per_su, litres, name) in MILKS.items():
        p, a = prices.get(pid, {}), avails.get(pid, {})
        cat = (p.get("catalogPrice") or {}).get("value")
        cmp_ = (p.get("comparisonPrice") or {}).get("value")
        rate = (p.get("tax") or {}).get("taxRate")
        litres_per_su = (per_su * litres) if litres else None
        qty = math.ceil(1000 / litres_per_su) if litres_per_su else None
        net = vat = gross = None
        if cat and qty:
            net, vat, gross = totals(cat, qty, rate)
        lines = [(l.get("availableDate"), l.get("availableAmount"), l.get("salesUnit")) for l in a.get("scheduleLines", [])]
        unlimited = any(x[1] is not None and x[1] >= 100000 for x in lines)
        rows.append((cmp_ if cmp_ is not None else 9e9, pid, su, cat, cmp_, p.get("comparisonUnit"),
                     qty, net, vat, gross, p.get("netPriceFound"), p.get("error"),
                     a.get("salesBlock"), a.get("logisticModel"), a.get("closingTime"), lines, unlimited, name))
    rows.sort()
    print("   sorted by Aimo's comparisonPrice (per L or KG), cheapest first")
    for (_, pid, su, cat, cmp_, cu, qty, net, vat, gross, npf, err, sb, lm, ct, lines, unlimited, name) in rows:
        print(f"   {pid} {name}")
        print(f"      per {su} net={cat} | {cmp_}/{cu} | for 1,000 L: qty={qty} net={net} vat={vat} gross={gross}")
        print(f"      netPriceFound={npf} error={err} salesBlock={sb} logisticModel={lm} closingTime={ct}")
        print(f"      scheduleLines={lines}{' (unlimited sentinel)' if unlimited else ''}")
    return rows


def main():
    if not KEY:
        sys.exit("Set JUNCTION_API_KEY first (see the usage note at the top of this file).")
    status, body = call("/api/ping")
    print("ping:", status, body)
    if status != 200:
        sys.exit("Ping failed. A 403 with 'error code: 1010' is Cloudflare rejecting the "
                 "user agent, not a bad key.")

    cutoffs = {}
    for requested_date in DATES:
        rows = run_one(requested_date.strip())
        if rows:
            cutoffs[requested_date.strip()] = sorted({r[14] for r in rows if r[14]})

    if len(cutoffs) > 1:
        print("\n=== does closingTime move with the requested date?")
        for d, cs in cutoffs.items():
            print(f"   {d}: {cs}")
        print("   If the cutoffs are the same for every date, a later delivery date does not "
              "buy us a later cutoff, and the demo must run before the earliest one.")

    print("\nNext: note the cheapest plain milk and its net total, any salesBlock or partial "
          "products (real exception candidates), and the closingTime against the pitch time.")
if __name__ == "__main__":
    main()
