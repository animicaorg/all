#!/usr/bin/env python3
"""
Generate and store an API key for studio-services, then print it.

By default, this script writes to the same SQLite DB used by the app:
  ${STORAGE_DIR:-./.storage}/studio_services.sqlite

Usage:
  python scripts/gen_api_key.py \
      [--db /path/to/db.sqlite] \
      [--label "Local Dev Key"] \
      [--prefix ssk_] \
      [--bytes 32] \
      [--json]

Notes:
- The database schema must already be applied (see scripts/migrate.sh).
- Keys are stored in plaintext in table 'api_keys' (for simplicity).
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


DEFAULT_DB = Path(os.getenv("STORAGE_DIR", "./.storage")) / "studio_services.sqlite"
DEFAULT_PREFIX = "ssk_"  # studio-services key
DEFAULT_NBYTES = 32      # -> 64 hex chars


@dataclass
class ApiKeyRecord:
    key: str
    label: Optional[str]
    enabled: int
    created_at: str


def fail(msg: str, code: int = 1) -> "NoReturn":  # type: ignore[name-defined]
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)
    )
    return cur.fetchone() is not None


def generate_key(prefix: str, nbytes: int) -> str:
    # hex-only to avoid URL/header encoding issues; human-friendly prefix
    return f"{prefix}{secrets.token_hex(nbytes)}"


def insert_key(conn: sqlite3.Connection, key: str, label: Optional[str]) -> ApiKeyRecord:
    # Try insert; if collision ( astronomically unlikely ), regenerate once.
    try:
        conn.execute(
            "INSERT INTO api_keys (key, label, enabled, created_at) "
            "VALUES (?, ?, 1, CURRENT_TIMESTAMP)",
            (key, label),
        )
    except sqlite3.OperationalError as e:
        # Most likely the table is missing → guide the user.
        if "no such table" in str(e).lower():
            fail(
                "Table 'api_keys' not found. Did you run migrations? "
                "Try: ./scripts/migrate.sh"
            )
        raise
    except sqlite3.IntegrityError:
        # Regenerate and retry once
        key = generate_key(DEFAULT_PREFIX, DEFAULT_NBYTES)
        conn.execute(
            "INSERT INTO api_keys (key, label, enabled, created_at) "
            "VALUES (?, ?, 1, CURRENT_TIMESTAMP)",
            (key, label),
        )
    conn.commit()

    cur = conn.execute(
        "SELECT key, label, enabled, created_at FROM api_keys WHERE key=?",
        (key,),
    )
    row = cur.fetchone()
    assert row, "inserted key not found (unexpected)"
    return ApiKeyRecord(
        key=row[0],
        label=row[1],
        enabled=int(row[2]),
        created_at=row[3],
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Create & print a studio-services API key")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"Path to SQLite DB (default: {DEFAULT_DB})")
    ap.add_argument("--label", type=str, default=None, help="Optional label to help identify this key")
    ap.add_argument("--prefix", type=str, default=DEFAULT_PREFIX, help=f"Key prefix (default: {DEFAULT_PREFIX})")
    ap.add_argument("--bytes", type=int, default=DEFAULT_NBYTES, dest="nbytes",
                    help=f"Random bytes for key body (default: {DEFAULT_NBYTES} → 64 hex chars)")
    ap.add_argument("--json", action="store_true", help="Print JSON payload instead of a plain key")
    args = ap.parse_args()

    db_path: Path = args.db
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Connect
    conn = sqlite3.connect(str(db_path))
    try:
        # Quick sanity: api_keys table must exist
        if not table_exists(conn, "api_keys"):
            fail(
                f"'api_keys' table is missing in DB {db_path}. "
                "Run migrations first: ./scripts/migrate.sh"
            )

        key = generate_key(args.prefix, args.nbytes)
        record = insert_key(conn, key, args.label)

        if args.json:
            out = asdict(record)
            # Do not expose internal flags as numbers in JSON; make it friendly
            out["enabled"] = bool(out["enabled"])
            print(json.dumps(out, indent=2))
        else:
            # Print the key alone for easy capture
            print(record.key)
            # Helpful usage hints
            print("\n# Example usage:\n"
                  "export STUDIO_API_KEY=\"%s\"\n"
                  "curl -H \"Authorization: Bearer $STUDIO_API_KEY\" \\\n"
                  "     http://localhost:8080/healthz" % record.key)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
