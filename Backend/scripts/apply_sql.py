"""Run a .sql file against the database in DATABASE_URL.

Saves needing psql installed: everything here comes from the venv.

    python scripts/apply_sql.py sql/audit.sql
    python scripts/apply_sql.py sql/audit.sql --check

Safe to run more than once - every statement in audit.sql says IF NOT EXISTS,
so it creates what is missing and leaves what is already there alone. Nothing
here drops or empties a table.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv                                  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import psycopg                                                  # noqa: E402

TABLES = [("gateway", "audit_log"), ("kitchen", "audit_log"), ("kitchen", "rules")]


def show_all(conn) -> None:
    """Every table in the two schemas, with its row count."""
    rows = conn.execute(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema IN ('gateway', 'kitchen') ORDER BY table_schema, table_name"
    ).fetchall()
    if not rows:
        print("\nNo tables in the gateway or kitchen schemas.")
        return
    print("\nEverything in the database:")
    for schema, table in rows:
        count = conn.execute(f"SELECT count(*) FROM {schema}.{table}").fetchone()[0]
        print(f"    {schema}.{table:<16} {count:>8,} rows")


def show(conn) -> None:
    """What exists now, so you can see the file did something."""
    print("\nTables:")
    for schema, table in TABLES:
        row = conn.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = %s", (schema, table)).fetchone()
        if not row or not row[0]:
            print(f"    {schema}.{table:<12} MISSING")
            continue
        count = conn.execute(f"SELECT count(*) FROM {schema}.{table}").fetchone()[0]
        columns = conn.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s", (schema, table)).fetchone()[0]
        print(f"    {schema}.{table:<12} ok, {columns} columns, {count} rows")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default="sql/audit.sql")
    ap.add_argument("--check", action="store_true", help="only report what exists")
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set. It belongs in Backend/.env")
        return 1
    # Never print the password.
    where = url.split("@")[-1].split("/")[0] if "@" in url else "the database"
    print(f"database: {where}")

    try:
        with psycopg.connect(url, connect_timeout=20, autocommit=True) as conn:
            if args.check:
                show(conn)
                show_all(conn)
                return 0

            path = Path(args.path)
            if not path.is_absolute():
                path = Path(__file__).resolve().parent.parent / path
            if not path.exists():
                print(f"no such file: {path}")
                return 1
            print(f"applying:  {path.name}")
            conn.execute(path.read_text(encoding="utf-8"))
            print("applied.")
            show(conn)
    except Exception as exc:                                    # noqa: BLE001
        print(f"\nFAILED: {type(exc).__name__}: {exc}")
        if "permission denied" in str(exc).lower():
            print("The database user may not be allowed to create schemas. "
                  "Aiven's default user normally is; check which user the URL names.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
