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

## Environment variables
See `ops/env/.env.example` for full configuration.
