# Animica Explorer 2

Explorer2 is a modern, standalone Animica blockchain explorer with a dedicated API and web UI. It can connect directly to an Animica node via RPC or read from a local database.

## Features

### Web UI
- **Home Page**: Chain status, network stats, and latest activity
- **Blocks Page**: Paginated list of recent blocks with auto-refresh
- **Block Detail**: Full block information with transaction list
- **Transaction Detail**: Transaction status with confirmation count
- **Address Page**: Balance and transaction history
- **Mempool Page**: Real-time pending transactions and mempool stats
- **Search**: Unified search for blocks (by height or hash), transactions, and addresses
- **Dark Mode**: Automatic theme switching
- **Copy to Clipboard**: Quick copy for hashes and addresses
- **Error Handling**: Retry buttons for failed requests

### API Endpoints
- `GET /api/health` - Health check
- `GET /api/meta` - Explorer and network metadata
- `GET /api/diagnostics` - Connection mode and database info
- `GET /api/head` - Current chain head and network stats
- `GET /api/blocks?limit=&cursor=` - Paginated block list
- `GET /api/block/:hashOrHeight` - Block details
- `GET /api/tx/:hash` - Transaction details
- `GET /api/address/:addr?limit=&cursor=` - Address balance and history
- `GET /api/mempool?limit=&cursor=` - Mempool entries and stats
- `GET /api/search?q=` - Unified search

## Prerequisites

- Node.js 18.18+
- pnpm 9+
- Running Animica node with RPC endpoint (recommended) OR local `~/.animica` database

## Setup

```bash
pnpm install
```

## Quick Start

The explorer automatically connects to a local node at `http://127.0.0.1:8545/rpc` by default. If you have a node running with the default RPC endpoint, you can start the explorer directly:

```bash
pnpm -C explorer2 dev
```

This will start:
- API server on `http://localhost:8081`
- Web UI on `http://localhost:3001`

Visit `http://localhost:3001/diagnostics` to verify the connection status.

## Configuration

To customize the RPC endpoint or other settings, copy the env example:

```bash
cp explorer2/.env.example explorer2/.env
```

Edit `.env` to customize settings:

```bash
# RPC endpoint (defaults to http://127.0.0.1:8545/rpc if not set)
EXPLORER2_RPC_URL=http://127.0.0.1:8545/rpc

# Optional: WebSocket endpoint for real-time updates
EXPLORER2_WS_URL=

# Port for the API server
EXPLORER2_PORT=8081

# Fallback settings (used only if RPC is unavailable)
EXPLORER2_DATA_ROOT=~/.animica
EXPLORER2_CHAIN_ID=1
```

## How It Works

**Auto-detection behavior:**
1. Explorer tries to connect to the RPC URL (defaults to `http://127.0.0.1:8545/rpc`)
2. If RPC connection succeeds, explorer runs in **RPC mode** (recommended)
3. If RPC connection fails, explorer falls back to **Local DB mode** (limited functionality)

**Connection modes:**

| Mode | Description | Capabilities |
|------|-------------|--------------|
| **RPC** | Direct connection to node | Full features: real-time blocks, mempool, peers, tx status |
| **Local DB** | Reads from `~/.animica/chain-{id}/animica.db` | Basic features: block history, transactions (no mempool/peers) |

Use the **Diagnostics** page in the web UI (`/diagnostics`) to check which mode is active and troubleshoot connection issues.

## Development (API + Web)

Run the full development stack:

```bash
pnpm -C explorer2 dev
```

- API runs on `http://localhost:8081`
- Web runs on `http://localhost:3001` and proxies `/api` to the API service

Run individual services:

```bash
# API only
pnpm -C explorer2/api dev

# Web only (requires API to be running)
pnpm -C explorer2/web dev
```

## Production build

```bash
pnpm -C explorer2 build
pnpm -C explorer2/api start
```

## Docker deployment

The explorer can be deployed using Docker Compose. By default, it will try to connect to an RPC node running on the host machine.

```bash
# Deploy with default settings (connects to host.docker.internal:8545/rpc)
docker compose -f explorer2/docker/docker-compose.explorer2.yml up --build

# Deploy with custom RPC URL
EXPLORER2_RPC_URL=http://your-rpc-node:8545/rpc docker compose -f explorer2/docker/docker-compose.explorer2.yml up --build
```

The web UI will be available at `http://localhost:3001` and the API at `http://localhost:8081`.

**Note**: The Docker deployment uses `host.docker.internal` to access services running on the host machine. If your RPC node is running elsewhere, set the `EXPLORER2_RPC_URL` environment variable to point to it.

## Environment variables

| Variable | Description | Default |
| --- | --- | --- |
| `EXPLORER2_PORT` | API port | `8081` |
| `EXPLORER2_RPC_URL` | **Node RPC endpoint** | `http://127.0.0.1:8545/rpc` |
| `EXPLORER2_WS_URL` | WebSocket endpoint for real-time updates (optional) | unset |
| `EXPLORER2_DATA_ROOT` | Base directory for local chain data (fallback) | `~/.animica` |
| `EXPLORER2_CHAIN_ID` | Chain ID for local data lookup (fallback) | `1` |
| `EXPLORER2_DB_PATH` | Full path to the chain DB (overrides data root + chain id, fallback) | unset |
| `EXPLORER2_CORS_ORIGIN` | CORS allowed origins | `*` |
| `EXPLORER2_LOG_LEVEL` | API log level | `info` |
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

## Usage Examples

### Search for blocks, transactions, or addresses

The `/api/search` endpoint accepts various input formats and returns the appropriate result:

```bash
# Search by block height
curl http://localhost:8081/api/search?q=12345

# Search by block or transaction hash
curl http://localhost:8081/api/search?q=0xabc123...

# Search by address
curl http://localhost:8081/api/search?q=anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq5nvly4
```

### Get explorer metadata

```bash
curl http://localhost:8081/api/meta
```

Returns:
```json
{
  "explorer": {
    "name": "Animica Explorer",
    "version": "0.1.0",
    "mode": "RPC"
  },
  "network": {
    "chainId": 1,
    "rpcUrl": "http://127.0.0.1:8545/rpc"
  },
  "timestamp": "2024-01-28T08:00:00.000Z"
}
```

### Monitor mempool

```bash
# Get mempool stats and transactions
curl http://localhost:8081/api/mempool?limit=50

# Response includes stats and entries
{
  "total": 10,
  "entries": [
    { "hash": "0x..." }
  ],
  "stats": {
    "count": 10,
    "totalBytes": 5420,
    "oldestAgeSec": 15
  }
}
```
