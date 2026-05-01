#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

echo "Bootstrapping dev auth user..."

EMAIL="${EMAIL:-epic2epic@gmail.com}"
PASSWORD="${PASSWORD:-Gl455rock1212!}"
export PASSWORD
DB="${DB:-cex_exchange}"
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-cex}"
DB_PASSWORD="${DB_PASSWORD:-cex_dev_password}"
AUTH_URL="${AUTH_URL:-http://localhost:4001/auth/login}"

export PGPASSWORD="$DB_PASSWORD"

for cmd in psql createdb node curl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd"
    exit 1
  fi
done

echo "Email: $EMAIL"
echo "DB: $DB"
echo "DB host: $DB_HOST:$DB_PORT"

echo "Checking database exists..."
DB_EXISTS=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${DB}'" || true)

if [[ "$DB_EXISTS" != "1" ]]; then
  echo "Creating database ${DB}..."
  createdb -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB"
fi

echo "Generating argon2 password hash..."
HASH=$(node - <<'NODE'
const argon2 = require('argon2');

(async () => {
  const hash = await argon2.hash(process.env.PASSWORD, {
    type: argon2.argon2id,
  });
  console.log(hash);
})();
NODE
)

echo "Inserting user into Postgres..."
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB" \
  -v email="$EMAIL" \
  -v hash="$HASH" <<'SQL'
INSERT INTO users (email, password_hash, active, email_verified)
VALUES (:'email', :'hash', true, true)
ON CONFLICT (email)
DO UPDATE SET
  password_hash = EXCLUDED.password_hash,
  active = true;
SQL

echo "User inserted/updated"

echo "Testing login..."
RESPONSE=$(curl -s -X POST "$AUTH_URL" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")

echo "Response:"
echo "$RESPONSE"

echo "Done"
