# Animica Explorer 2

Explorer2 is a modern, standalone Animica blockchain explorer with a dedicated API and web UI. It can connect directly to an Animica node via RPC or read from a local database.

## Prerequisites

- Node.js 18.18+
- pnpm 9+
- Running Animica node with RPC endpoint (recommended) OR local `~/.animica` database

## Setup

```bash
pnpm install
```

Copy the env example:

```bash
cp explorer2/.env.example explorer2/.env
```

Edit `.env` and set `EXPLORER2_RPC_URL` to your node's RPC endpoint:

```bash
EXPLORER2_RPC_URL=http://127.0.0.1:8545/rpc
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
| `EXPLORER2_RPC_URL` | **Node RPC endpoint (recommended)** | unset |
| `EXPLORER2_WS_URL` | WebSocket endpoint for real-time updates (optional) | unset |
| `EXPLORER2_DATA_ROOT` | Base directory for local chain data (fallback) | `~/.animica` |
| `EXPLORER2_CHAIN_ID` | Chain ID for local data lookup (fallback) | `1` |
| `EXPLORER2_DB_PATH` | Full path to the chain DB (overrides data root + chain id, fallback) | unset |
| `EXPLORER2_CORS_ORIGIN` | CORS allowed origins | `*` |
| `EXPLORER2_LOG_LEVEL` | API log level | `info` |
| `EXPLORER2_CACHE_HEAD_TTL_MS` | Cache TTL for head endpoint | `5000` |
| `EXPLORER2_CACHE_BLOCKS_TTL_MS` | Cache TTL for blocks | `8000` |
| `EXPLORER2_CACHE_TX_TTL_MS` | Cache TTL for transactions | `20000` |
| `EXPLORER2_CACHE_PERSIST_PATH` | Cache persistence file path (enables warm-start cache) | unset |
| `EXPLORER2_RPC_TIMEOUT_MS` | RPC request timeout | `30000` |
| `EXPLORER2_RPC_MAX_RETRIES` | Max retry attempts for RPC calls | `3` |

## Connection Modes

### RPC Mode (Recommended)

Set `EXPLORER2_RPC_URL` to connect directly to your Animica node:

```bash
EXPLORER2_RPC_URL=http://127.0.0.1:8545/rpc
```

**Benefits:**
- Real-time data (mempool, peers, sync status)
- No need for local database
- Works with remote nodes
- Proper error handling and retries

**Required RPC methods:**
- `chain.getHead`
- `chain.getBlockByNumber` / `chain.getBlockByHash`
- `tx.getTransaction` (optional)
- `receipt.getReceipt` (optional)
- `state.getBalance` (optional)
- `mempool.getPending` / `mempool.getStats` (optional)
- `p2p.getPeers` (optional)

### Local DB Mode (Fallback)

If `EXPLORER2_RPC_URL` is not set, the explorer reads from local `~/.animica` database:

```bash
EXPLORER2_DATA_ROOT=~/.animica
EXPLORER2_CHAIN_ID=1
```

**Limitations:**
- No real-time mempool/peer data
- Requires local node database access
- Address history is best-effort

## Notes

- API and web are reverse-proxy friendly: `/` serves the UI, `/api` serves the API
- Capabilities detection gracefully handles missing RPC methods
- Request coalescing prevents duplicate requests
