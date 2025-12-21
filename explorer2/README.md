# Animica Explorer 2 (MVP)

Explorer2 is a new, standalone Animica blockchain explorer with a dedicated API and web UI. It reads chain data from `~/.animica` by default and only falls back to JSON-RPC for data that is not available locally (mempool/peer stats, pending balances).

## Prerequisites

- Node.js 18.18+
- pnpm 9+
- Running Animica node that is writing to `~/.animica` (or the data root you configure)

## Setup

```bash
pnpm install
```

Copy the env example:

```bash
cp explorer2/.env.example explorer2/.env
```

## Development (API + Web)

```bash
pnpm -C explorer2 dev
```

- API runs on `http://localhost:8081`
- Web runs on `http://localhost:3001` and proxies `/api` to the API service

## Production build

```bash
pnpm -C explorer2 build
pnpm -C explorer2/api start
```

## Docker deployment

```bash
docker compose -f explorer2/docker/docker-compose.explorer2.yml up --build
```

## Environment variables

| Variable | Description | Default |
| --- | --- | --- |
| `EXPLORER2_PORT` | API port | `8081` |
| `EXPLORER2_RPC_URL` | Animica JSON-RPC endpoint | `http://127.0.0.1:8545/rpc` |
| `EXPLORER2_DATA_ROOT` | Base directory for local chain data | `~/.animica` |
| `EXPLORER2_CHAIN_ID` | Chain ID for local data lookup | `1` |
| `EXPLORER2_DB_PATH` | Full path to the chain DB (overrides data root + chain id) | unset |
| `EXPLORER2_CORS_ORIGIN` | CORS allowed origins | `*` |
| `EXPLORER2_LOG_LEVEL` | API log level | `info` |
| `EXPLORER2_CACHE_HEAD_TTL_MS` | Cache TTL for head endpoint | `5000` |
| `EXPLORER2_CACHE_BLOCKS_TTL_MS` | Cache TTL for blocks | `8000` |
| `EXPLORER2_CACHE_TX_TTL_MS` | Cache TTL for transactions | `20000` |
| `EXPLORER2_CACHE_PERSIST_PATH` | Cache persistence file path (enables warm-start cache) | unset |

## Notes

- Address history is best-effort in MVP mode and scans recent blocks.
- API and web are reverse-proxy friendly: `/` serves the UI, `/api` serves the API.
