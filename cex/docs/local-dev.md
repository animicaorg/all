# Local Development

## Prerequisites
- Docker + Docker Compose
- Node.js >= 20 (optional if running with Docker only)
- pnpm (via Corepack)

## Quick start (Docker)
1. Copy environment file:
   ```bash
   cp ops/env/.env.example ops/env/.env
   ```
2. Start dependencies + services:
   ```bash
   ops/scripts/dev-up.sh
   ```
3. Run migrations + seed:
   ```bash
   ops/scripts/migrate.sh
   ops/scripts/seed.sh
   ```

## Ports
- API Gateway: `3000`
- Admin Service: `3001`
- BitGo Webhook Ingestor: `3002`
- Postgres: `5432`
- Redis: `6379`
- NATS: `4222` (monitoring: `8222`)

## Local Animica node
- Set `ANIMICA_RPC_URL` to your local node RPC endpoint.
- The `wallet-router` and `animica-indexer` will attempt a `ping` request on startup.

## Running without Docker
```bash
pnpm install
pnpm migrate
pnpm seed
pnpm dev
```

If you're running PostgreSQL locally (not via Docker), make sure the configured
database user exists. You can either:

- Update `DB_USER`/`DB_PASSWORD` in `ops/env/.env` to match an existing role
  (for example, `postgres`), **or**
- Create the role/database to match the defaults:

```bash
# Create the role (adjust password as needed)
psql -U postgres -c "CREATE ROLE cex WITH LOGIN PASSWORD 'cex_password';"

# Create the database owned by the role
createdb -U postgres -O cex cex_exchange
```

## Environment variables
See `ops/env/.env.example` for full configuration.
