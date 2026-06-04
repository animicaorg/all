#!/usr/bin/env bash
# Provision the pool_rental Postgres container (mirrors the other Animica
# per-app Postgres containers; binds to 127.0.0.1:5438 only).
set -euo pipefail

NAME=pool-rental-postgres
PORT=5438
DB=pool_rental
USER=pool_rental
PASS="${POOL_RENTAL_DB_PASSWORD:?set POOL_RENTAL_DB_PASSWORD}"

if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  echo "container $NAME already exists"
else
  docker run -d --name "$NAME" --restart unless-stopped \
    -e POSTGRES_DB="$DB" -e POSTGRES_USER="$USER" -e POSTGRES_PASSWORD="$PASS" \
    -p 127.0.0.1:${PORT}:5432 \
    -v pool_rental_pgdata:/var/lib/postgresql/data \
    postgres:16
  echo "started $NAME on 127.0.0.1:${PORT}"
fi

echo "DATABASE_URL=postgresql://${USER}:${PASS}@127.0.0.1:${PORT}/${DB}?schema=public"
