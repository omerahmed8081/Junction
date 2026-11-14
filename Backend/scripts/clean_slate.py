"""Wipe everything this demo has written, so a deployment starts empty.

    python scripts/clean_slate.py                 # show what would go
    python scripts/clean_slate.py --yes           # actually do it

Clears the two audit logs, the kitchen's state file (baskets, orders, mandate,
logged services), and - if the services are running - the in-memory state the
gateway keeps, which no script can reach from outside.

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
TABLES = ["gateway.audit_log", "kitchen.audit_log"]
RULES_TABLE = "kitchen.rules"

STATE_FILE = Path(os.environ.get("KITCHEN_STATE_FILE") or ROOT / ".state" / "kitchen.json")
KITCHEN_URL = os.environ.get("KITCHEN_BASE_URL", "http://localhost:8002")
GATEWAY_URL = os.environ.get("GATEWAY_BASE_URL", "http://localhost:8001")
AGENT_KEY = os.environ.get("AGENT_API_KEY", "demo-kitchen-key")


def database(apply: bool, rules: bool) -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("  DATABASE_URL is not set, so there is no audit log to clear")
        return 0
    tables = TABLES + ([RULES_TABLE] if rules else [])
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=20, autocommit=True) as conn:
            for table in tables:
                schema, name = table.split(".")
                exists = conn.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_name = %s", (schema, name)).fetchone()
                if not exists:
                    print(f"  {table:<22} not there yet")
                    continue
                count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                if not apply:
                    print(f"  {table:<22} {count:>8,} rows would go")
                    continue
                # TRUNCATE rather than DELETE: it also resets the id sequence, so
                # a fresh demo starts at 1 instead of continuing last night's count.
                conn.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY")
                print(f"  {table:<22} {count:>8,} rows cleared")
    except Exception as exc:                                    # noqa: BLE001
        print(f"  database: FAILED {type(exc).__name__}: {exc}")
        return 1
    return 0


def files(apply: bool) -> int:
    if not STATE_FILE.exists():
        print(f"  {STATE_FILE.name:<22} already gone")
        return 0
    size = STATE_FILE.stat().st_size
    if not apply:
        print(f"  {STATE_FILE.name:<22} {size:>8,} bytes would go ({STATE_FILE})")
        return 0
    try:
        STATE_FILE.unlink()
        print(f"  {STATE_FILE.name:<22} {size:>8,} bytes removed")
    except Exception as exc:                                    # noqa: BLE001
        print(f"  {STATE_FILE.name}: FAILED {exc}")
        return 1
    return 0


def running_services(apply: bool) -> int:
    """The gateway keeps its orders, used quote ids and weekly spend in memory.

    Nothing outside the process can clear those, so if it is up we ask it to.
    If it is not up, they are already gone - memory does not survive a stop.
    """
    if not apply:
        print("  running services  would be asked to reset (if they are up)")
        return 0
    for name, url, path, headers in (
        ("kitchen", KITCHEN_URL, "/api/demo/reset", {}),
        ("gateway", GATEWAY_URL, "/gateway/demo/reset", {"X-API-Key": AGENT_KEY}),
    ):
        try:
            response = httpx.post(f"{url}{path}", headers=headers, timeout=15)
            response.raise_for_status()
            print(f"  {name:<22} reset in place")
        except Exception:                                       # noqa: BLE001
            print(f"  {name:<22} not running (nothing in memory to clear)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="actually clear, rather than listing")
    ap.add_argument("--rules", action="store_true",
                    help="also clear the kitchen's standing rules (a setting, kept by default)")
    args = ap.parse_args()

    print("A clean slate for the demo." if args.yes else "Dry run. Add --yes to clear.\n")
    print("Database:")
    bad = database(args.yes, args.rules)
    print("Files:")
    bad += files(args.yes)
    print("Services:")
    bad += running_services(args.yes)

    print()
    if not args.yes:
        print("Nothing was changed. Run again with --yes to clear.")
        return 0
    if bad:
        print("Finished with problems; look above.")
        return 1
    print("Clean. Restart both services, then confirm with:")
    print("    python scripts/smoke_test.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
