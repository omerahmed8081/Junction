"""Wipe everything this demo has written, so a deployment starts empty.

    python scripts/clean_slate.py                 # show what would go
    python scripts/clean_slate.py --yes           # actually do it

Point it at whichever services are running against this database:

    python scripts/clean_slate.py --yes --kitchen http://YOUR_SERVER_IP \
                                       --gateway http://YOUR_SERVER_IP

Without that it resets the services on localhost, and a deployed kitchen that
nobody told keeps its baskets, orders and mandate in memory and writes them
back into kitchen.state on its next save. The audit rows stay cleared, because
they are written as they happen and never re-saved - which is exactly what
"it clears the logs but not the orders" looks like.

Clears both audit logs, the gateway's orders and used quote ids, the kitchen's
state (baskets, orders, mandate, logged services), the old state file if one is
still lying about, and - if the services are running - what they hold in memory,
which no script can reach from outside.

It counts every table again afterwards and says so if anything is left, because
a reset that reports success without looking is how a demo starts with last
night's data still on screen.

It deliberately does NOT touch:

  * `gateway.products` and `gateway.category_labels`, the catalogue. Reloading
    those means parsing an 84 MB file again, and nothing in them is demo state.
  * `Backend/.keys/`, the signing keys. A new key invalidates every mandate
    already issued and has to be re-registered with the gateway, which is a
    worse surprise than a leftover row.
  * `Backend/Data/seed/`, the seeded restaurant. The kitchen reads it on start.

Standing rules are kept unless you ask: they are a setting, not demo traffic.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv                                  # noqa: E402

load_dotenv(ROOT / ".env")

import httpx                                                    # noqa: E402

# Tables that hold demo traffic. The catalogue is deliberately absent.
# Everything the demo writes. One list, because the point of moving state into
# the database was that there is now one place to clear.
TABLES = [
    "gateway.audit_log",
    "gateway.orders",        # the week's spending is counted from these
    "gateway.used_quotes",   # which quote became which order
    "kitchen.audit_log",
    "kitchen.state",         # baskets, orders, the mandate, logged services
]
# Deliberately not cleared: gateway.revoked_mandates. A mandate called back
# stays called back - clearing it would quietly let a stopped agent order
# again, which is the one thing a revocation is for. Pass --revocations to
# clear it anyway, which is only ever right on a throwaway database.
REVOCATIONS_TABLE = "gateway.revoked_mandates"
RULES_TABLE = "kitchen.rules"

STATE_FILE = Path(os.environ.get("KITCHEN_STATE_FILE") or ROOT / ".state" / "kitchen.json")
KITCHEN_URL = os.environ.get("KITCHEN_BASE_URL", "http://localhost:8002")
GATEWAY_URL = os.environ.get("GATEWAY_BASE_URL", "http://localhost:8001")
AGENT_KEY = os.environ.get("AGENT_API_KEY", "demo-kitchen-key")


def database(apply: bool, rules: bool, revocations: bool = False) -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("  DATABASE_URL is not set, so there is no audit log to clear")
        return 0
    tables = TABLES + ([RULES_TABLE] if rules else []) + (
        [REVOCATIONS_TABLE] if revocations else [])
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=20, autocommit=True) as conn:
            for table in tables:
                schema, name = table.split(".")
                exists = conn.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_name = %s", (schema, name)).fetchone()
                if not exists:
                    print(f"  {table:<25} not there yet")
                    continue
                count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                if not apply:
                    print(f"  {table:<25} {count:>8,} rows would go")
                    continue
                # TRUNCATE rather than DELETE: it also resets the id sequence, so
                # a fresh demo starts at 1 instead of continuing last night's count.
                conn.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY")
                print(f"  {table:<25} {count:>8,} rows cleared")

            if not apply:
                return 0
            # Count again on the same connection - Aiven's free tier powers off
            # when idle and takes about half a minute to wake, so a second
            # connection would mean a second wait.
            print("\nChecking:")
            left = []
            for table in tables:
                schema, name = table.split(".")
                exists = conn.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_name = %s", (schema, name)).fetchone()
                if not exists:
                    continue
                count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                print(f"  {table:<25} {count:>8,} rows")
                if count:
                    left.append(table)
            if left:
                print(f"\n  NOT CLEAN: still holding rows in {', '.join(left)}.")
                print("  A service was probably writing while this ran. Stop both, run again.")
                return 1
    except Exception as exc:                                    # noqa: BLE001
        print(f"  database: FAILED {type(exc).__name__}: {exc}")
        return 1
    return 0


def files(apply: bool) -> int:
    """The old state file. State lives in `kitchen.state` now; this is only
    here so a machine that ran the file version does not quietly keep it and
    load it again the next time the database is unreachable."""
    # The atomic write leaves a .tmp beside it if it was interrupted.
    stray = STATE_FILE.with_suffix(".tmp")
    if stray.exists() and apply:
        try:
            stray.unlink()
            print(f"  {stray.name:<25} removed (interrupted write)")
        except Exception:                                       # noqa: BLE001
            pass
    if not STATE_FILE.exists():
        print(f"  {STATE_FILE.name:<25} already gone")
        return 0
    size = STATE_FILE.stat().st_size
    if not apply:
        print(f"  {STATE_FILE.name:<25} {size:>8,} bytes would go ({STATE_FILE})")
        return 0
    try:
        STATE_FILE.unlink()
        print(f"  {STATE_FILE.name:<25} {size:>8,} bytes removed")
    except Exception as exc:                                    # noqa: BLE001
        print(f"  {STATE_FILE.name}: FAILED {exc}")
        return 1
    return 0


def running_services(apply: bool, kitchen_url: str, gateway_url: str) -> int:
    """Both services hold state in memory that no table can reach.

    The kitchen holds baskets, orders and the mandate; the gateway holds its
    orders, used quote ids and the weekly spend. Truncating the tables under a
    running service does not clear those, and the kitchen writes its memory
    back on the next save, so this asks them to let go first.

    Returns the number that could not be reached, because that is the case
    worth shouting about: a service running somewhere this was not pointed at
    will undo the clean slate a moment later.
    """
    if not apply:
        print(f"  kitchen                  would be asked to reset ({kitchen_url})")
        print(f"  gateway                  would be asked to reset ({gateway_url})")
        return 0
    missed = 0
    for name, url, path, headers in (
        ("kitchen", kitchen_url, "/api/demo/reset", {}),
        ("gateway", gateway_url, "/gateway/demo/reset", {"X-API-Key": AGENT_KEY}),
    ):
        try:
            response = httpx.post(f"{url.rstrip('/')}{path}", headers=headers, timeout=15)
            response.raise_for_status()
            print(f"  {name:<25} reset in place ({url})")
        except Exception as exc:                                # noqa: BLE001
            missed += 1
            print(f"  {name:<25} NO ANSWER at {url} ({type(exc).__name__})")
    return missed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="actually clear, rather than listing")
    ap.add_argument("--revocations", action="store_true",
                    help="also clear revoked mandates (a stopped agent would order again)")
    ap.add_argument("--rules", action="store_true",
                    help="also clear the kitchen's standing rules (a setting, kept by default)")
    ap.add_argument("--kitchen", default=KITCHEN_URL,
                    help=f"where the kitchen is running (default {KITCHEN_URL})")
    ap.add_argument("--gateway", default=GATEWAY_URL,
                    help=f"where the gateway is running (default {GATEWAY_URL})")
    args = ap.parse_args()

    print("A clean slate for the demo." if args.yes else "Dry run. Add --yes to clear.\n")
    # Running services first, then the tables. A running kitchen holds its
    # baskets in memory and writes them back on its next save, so truncating
    # first and asking it to reset second leaves a window where it re-saves
    # what we just cleared. Asking first empties the memory; the truncation
    # then sweeps up anything a service that was down had left behind.
    print("Services:")
    unreachable = running_services(args.yes, args.kitchen, args.gateway)
    bad = 0
    print("Database:")
    bad += database(args.yes, args.rules, args.revocations)
    print("Files:")
    bad += files(args.yes)

    print()
    if not args.yes:
        print("Nothing was changed. Run again with --yes to clear.")
        return 0
    if unreachable:
        print("WARNING: a service did not answer, so whatever it holds in memory was")
        print("not cleared. If it is running against this database somewhere else, it")
        print("will write that memory back and the orders will reappear. Point this at")
        print("it with --kitchen and --gateway, or stop it and run again.")
        print()
    if bad:
        print("Finished with problems; look above.")
        return 1
    print("Clean. Restart both services, then confirm with:")
    print("    python scripts/smoke_test.py")
    print()
    print("The browser clears itself: the kitchen reports a new generation after")
    print("a reset and the page drops its chat transcript when it changes. If a")
    print("tab was already open, reload it once.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
