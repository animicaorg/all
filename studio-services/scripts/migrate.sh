#!/usr/bin/env bash
# Apply SQLite migrations using studio-services/studio_services/storage/schema.sql
# Creates the DB file if it doesn't exist and executes the schema idempotently.
# Usage:
#   ./scripts/migrate.sh [--db /path/to/db.sqlite] [--schema /path/to/schema.sql] [--reset]
#
# Defaults:
#   DB PATH : ${STORAGE_DIR:-./.storage}/studio_services.sqlite
#   SCHEMA  : studio_services/storage/schema.sql

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
APP_ROOT="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"

DB_DEFAULT_DIR="${STORAGE_DIR:-${APP_ROOT}/.storage}"
DB_DEFAULT_PATH="${DB_DEFAULT_DIR}/studio_services.sqlite"
SCHEMA_DEFAULT_PATH="${APP_ROOT}/studio_services/storage/schema.sql"

DB_PATH="${DB_DEFAULT_PATH}"
SCHEMA_PATH="${SCHEMA_DEFAULT_PATH}"
RESET_DB="false"

usage() {
  cat <<USAGE
Apply SQLite migrations for studio-services.

Options:
  --db <path>       Path to SQLite DB file (default: ${DB_DEFAULT_PATH})
  --schema <path>   Path to schema.sql (default: ${SCHEMA_DEFAULT_PATH})
  --reset           Remove existing DB file before applying schema
  -h, --help        Show this help

Environment:
  STORAGE_DIR       Default base directory for the DB path when --db not provided
USAGE
}

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --db) DB_PATH="$2"; shift 2 ;;
    --schema) SCHEMA_PATH="$2"; shift 2 ;;
    --reset) RESET_DB="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 1 ;;
  esac
done

# Sanity checks
if [[ ! -f "${SCHEMA_PATH}" ]]; then
  echo "ERROR: schema file not found: ${SCHEMA_PATH}" >&2
  exit 1
fi

mkdir -p "$(dirname "${DB_PATH}")"

if [[ "${RESET_DB}" == "true" && -f "${DB_PATH}" ]]; then
  echo "→ Removing existing DB: ${DB_PATH}"
  rm -f "${DB_PATH}"
fi

echo "======================================================================"
echo " studio-services DB migration"
echo "----------------------------------------------------------------------"
echo "  DB PATH : ${DB_PATH}"
echo "  SCHEMA  : ${SCHEMA_PATH}"
echo "  RESET   : ${RESET_DB}"
echo "======================================================================"

# Apply schema via Python (portable; no sqlite3 CLI dependency required)
python - <<PYAPPLY
import sqlite3, sys, pathlib

db_path = pathlib.Path(r"""${DB_PATH}""")
schema_path = pathlib.Path(r"""${SCHEMA_PATH}""")

sql = schema_path.read_text(encoding="utf-8")
conn = sqlite3.connect(str(db_path))
try:
    conn.executescript(sql)
    conn.commit()
    # Introspection summary
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    print("✔ Migration applied successfully.")
    print("  Tables:")
    for t in tables:
        print("   -", t)
finally:
    conn.close()
PYAPPLY

echo "Done."
