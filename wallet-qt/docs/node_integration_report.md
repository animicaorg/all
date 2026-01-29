# Node Integration Report

## Executive Summary

This document describes the canonical Animica node entrypoints, build procedures, and configuration options relevant to embedding the node within the Qt wallet application.

## Node Entrypoints

### Primary Entrypoint: Python CLI + Docker Compose

The canonical way to run an Animica node is via the Python CLI wrapper around Docker Compose:

**File Path**: `python/animica/cli/node.py` and `python/animica/__main__.py`

**Command**:
```bash
# Via installed CLI script (created by setup.sh)
animica node up

# Or via Python module
python -m animica node up

# Or via shell wrapper
./animica node up
```

**What it does**: 
- Launches Docker Compose with the appropriate network configuration
- Uses compose files at `tests/devnet/docker-compose.yml` (and similar for other networks)
- Manages data directories per network
- Handles environment variable configuration

### Alternative Entrypoint: Direct RPC Server (Standalone Python)

For development/testing, the RPC server can run standalone without Docker:

**File Path**: `rpc/__main__.py` and `rpc/server.py`

**Command**:
```bash
# Direct Python invocation
python -m rpc

# With environment configuration
ANIMICA_RPC_HOST=127.0.0.1 ANIMICA_RPC_PORT=8545 python -m rpc
```

**What it does**:
- Starts a FastAPI/uvicorn HTTP server
- Serves JSON-RPC at `/rpc` endpoint
- Serves WebSocket at `/ws` endpoint
- Default bind: `127.0.0.1:8545`

### Shell Wrapper

**File Path**: `./animica` (root directory)

**Purpose**: Delegates to `.venv/bin/animica` if present, otherwise tries system-installed `animica`

## RPC Server Configuration

### Network Binding

**Default Host**: `127.0.0.1` (localhost-only)  
**Default Port**: `8545`  
**WebSocket Path**: `/ws`

**Configuration File**: `rpc/config.py`

**Key Environment Variables**:
```bash
ANIMICA_RPC_HOST=127.0.0.1        # Bind address (use 127.0.0.1 for local-only)
ANIMICA_RPC_PORT=8545              # HTTP/WS port
ANIMICA_RPC_WS_PATH=/ws            # WebSocket endpoint path
```

### Data Directory Configuration

**Environment Variables**:
```bash
ANIMICA_DATA_DIR=~/.animica        # Base data directory
ANIMICA_NETWORK=devnet             # Network: mainnet|testnet|devnet|local-devnet
ANIMICA_CHAIN_ID=1337              # Chain ID (1=mainnet, 2=testnet, 1337=devnet)
```

**Directory Structure** (per network):
```
~/.animica/
  └── chain-{CHAIN_ID}/           # e.g., chain-1337 for devnet
      ├── animica.db               # State DB (SQLite by default)
      ├── blocks.db                # Block storage
      └── p2p/                     # P2P node data
          ├── peer_store.db
          └── node_key
```

**Database URI Configuration**:
```bash
ANIMICA_RPC_DB_URI=sqlite:///~/.animica/chain-1337/animica.db
```

### Logging Configuration

**Environment Variables**:
```bash
ANIMICA_LOG_LEVEL=INFO            # DEBUG|INFO|WARNING|ERROR
```

**Log Locations** (when using Docker Compose):
- Container logs accessible via `docker compose logs`
- Host logs in `./logs/` directory (repo root)
- PID file at `./logs/animica-p2p.pid`

### P2P Configuration

**Environment Variables**:
```bash
ANIMICA_P2P_PORT=30333            # P2P listen port
ANIMICA_P2P_HOST=0.0.0.0          # P2P bind address
ANIMICA_P2P_BOOTSTRAP_NODES=...   # Comma-separated bootstrap multiaddrs
```

**Default Ports by Network**:
- Mainnet: RPC 8545, P2P 30333, Metrics 9000
- Testnet: RPC 18546, P2P 31334, Metrics 19000
- Devnet: RPC 28545, P2P 31335, Metrics 29000
- Local-devnet: RPC 38545, P2P 31336, Metrics 39000

## Build Instructions

### Prerequisites

**Required**:
- Python 3.11+
- Docker and Docker Compose (v2.0+)
- Git

**Optional** (for native builds):
- Rust toolchain (for Rust components)
- Node.js 20+ (for web UIs)
- Build tools (gcc, make, pkg-config)

### Building the Node

#### Standard Build (Docker-based - Recommended)

```bash
# 1. Run setup script (creates venv, installs dependencies)
./setup.sh --with-pq

# 2. Activate virtual environment
source .venv/bin/activate

# 3. Build Docker images
animica node up --build

# Or with specific network
export ANIMICA_NETWORK=devnet
docker compose -f tests/devnet/docker-compose.yml build
```

**Build Artifacts**:
- Docker images: `animica-node-devnet`, `animica-rpc`, etc.
- Python wheel: `.venv/lib/python3.*/site-packages/animica/`
- Installed CLI: `.venv/bin/animica`

#### Development Build (Standalone Python)

```bash
# Install in development mode
./setup.sh --with-pq
source .venv/bin/activate

# Run directly without Docker
python -m rpc
```

### Platform-Specific Build Notes

#### Linux
- Native builds fully supported
- Docker recommended for isolation
- Binary output: `.venv/bin/animica` (Python entrypoint script)

#### macOS
- Full support via Docker Desktop
- Native Python builds supported
- M1/M2: Ensure Docker uses arm64 images

#### Windows
- Use WSL2 (Ubuntu 22.04+) recommended
- Docker Desktop required
- Native Windows builds not officially supported

## Node Binary/Artifact Locations for Qt Wallet Integration

### Recommended Approach: Bundled Python + Docker

For the Qt wallet, we will bundle:

1. **Python Runtime**: Embed Python 3.11+ with the wallet
2. **Animica Python Package**: Include installed package in `wallet-qt/bundle/python/`
3. **Docker Compose Files**: Copy compose files to `wallet-qt/bundle/compose/`
4. **Docker Integration**: Wallet will invoke Docker Compose via QProcess

**Bundled Structure** (to be implemented):
```
wallet-qt/
  └── bundle/
      ├── python/           # Python runtime + animica package
      │   ├── bin/python
      │   └── lib/...
      ├── compose/          # Docker compose files
      │   └── node.yml
      └── scripts/          # Helper scripts
          └── run_node.sh
```

### Alternative Approach: Standalone Python Module

If Docker is not available, fall back to standalone Python RPC server:

**Entrypoint**: `python -m rpc`  
**Location**: `.venv/bin/python -m rpc` or system Python  
**Configuration**: Via environment variables (see above)

## Node Control Interface

### Process Management

**Detection**:
- PID file at `./logs/animica-p2p.pid` (Docker mode)
- Check port binding on `127.0.0.1:8545`
- RPC health check: `{"jsonrpc":"2.0","method":"node.ping","id":1}`

**Signals**:
- `SIGTERM`: Graceful shutdown (via Docker stop)
- `SIGKILL`: Force kill (last resort)

### Health Check Endpoints

**RPC Health Check**:
```bash
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"node.ping","id":1}'

# Expected response:
# {"jsonrpc":"2.0","result":"pong","id":1}
```

**Common RPC Methods** (to be used by wallet):
- `node.ping` - Health check
- `chain.getHead` - Current block height/hash
- `chain.getChainId` - Network chain ID
- `state.getBalance` - Account balance
- `state.getNonce` - Account nonce
- `tx.sendRawTransaction` - Submit signed transaction
- `tx.getTransactionReceipt` - Get transaction status
- `p2p.listPeers` - Connected peers
- `sync.getStatus` - Sync progress

### Metrics Endpoints

**Prometheus Metrics** (if enabled):
- URL: `http://127.0.0.1:9100/metrics`
- Environment: `ANIMICA_METRICS_ENABLED=true`

## Configuration Keys Reference

### Essential for Wallet Embedding

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANIMICA_RPC_HOST` | `127.0.0.1` | RPC bind address |
| `ANIMICA_RPC_PORT` | `8545` | RPC/WS port |
| `ANIMICA_DATA_DIR` | `~/.animica` | Base data directory |
| `ANIMICA_NETWORK` | `mainnet` | Network profile |
| `ANIMICA_CHAIN_ID` | `1` | Chain ID (overrides network) |
| `ANIMICA_LOG_LEVEL` | `INFO` | Logging verbosity |
| `ANIMICA_P2P_PORT` | `30333` | P2P listen port |

### Security

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANIMICA_RPC_ACCESS_MODE` | `LOCAL_DEV` | Access control mode |
| `ANIMICA_RPC_ADMIN_TOKEN` | (none) | Admin API bearer token |
| `ANIMICA_RPC_CORS_ORIGINS` | `["http://localhost:5173"]` | CORS allowed origins |

## Integration Strategy for Qt Wallet

Based on this analysis, the Qt wallet will:

1. **Preferred Approach (Standalone Python)**:
   - Use system Python or bundled Python
   - Launch node via `python -m rpc` as subprocess
   - Monitor via RPC health checks
   - Stop via process termination (SIGTERM)
   - **Rationale**: Simpler packaging, no Docker dependency, full control over process

2. **Alternative (Docker-based)**:
   - Embed Docker Compose files
   - Launch node via `docker compose up -d`
   - Monitor via RPC health checks
   - Stop via `docker compose down`
   - **Rationale**: Better isolation, production-ready setup

For initial implementation, we'll use **Standalone Python** approach for simplicity and to avoid Docker dependency.

3. **Configuration**:
   - Override `ANIMICA_RPC_HOST` to `127.0.0.1` (enforced)
   - Auto-select `ANIMICA_RPC_PORT` (8545 default, auto-increment on conflict)
   - Set `ANIMICA_DATA_DIR` to wallet-managed directory
   - Set `ANIMICA_NETWORK` based on user selection

4. **Security**:
   - Bind RPC to `127.0.0.1` only (no external access)
   - Restrict file permissions on config/key files
   - Lock data directory to prevent concurrent access
   - PID file + port lock to prevent multiple instances

## Next Steps

1. Implement `NodeManager` class to launch/monitor node
2. Implement `AnimicaRpcClient` for RPC communication
3. Implement `AppPaths` for cross-platform directory management
4. Create minimal UI with Start/Stop controls
5. Add health monitoring and status display
6. Implement log tailing for debugging

## References

- **RPC Config**: `rpc/config.py`
- **Node CLI**: `python/animica/cli/node.py`
- **Server Main**: `rpc/server.py`
- **Docker Compose**: `tests/devnet/docker-compose.yml`
- **Network Config**: `python/animica/config.py`
- **P2P Service**: `p2p/node/service.py`
