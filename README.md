# Animica L1 Blockchain

**Animica** is a fully decentralized layer-1 blockchain platform for verifiable AI and quantum-secure execution. This repository houses the complete blockchain node implementation, consensus engine, execution layer, cryptographic infrastructure, wallets, SDKs, developer tooling, and supporting services for running and extending the network.

## 🌟 Key Features

- **Fully Decentralized P2P Network**: Gossip-based peer discovery and communication with no central authority
- **Post-Quantum Cryptography**: Dilithium3 (ML-DSA-65) and SPHINCS+ signatures for quantum-resistant security
- **PoIES Consensus**: Proof-of-Integrated-External-Services combining hash-share work with AI/Quantum/Storage proofs
- **Multiple Transport Protocols**: TCP, QUIC, and WebSocket with end-to-end encryption
- **Python-VM Execution**: Deterministic Python-based smart contracts with gas metering
- **AI & Quantum Integration**: Off-chain compute coordination via AICF (AI Capability Framework)
- **Multi-Network Support**: Mainnet, testnet, devnet configurations with isolated state
- **Developer Tools**: Studio IDE, contract templates, multi-language SDKs (Python, TypeScript, Rust)

## 📁 Repository Structure

This monorepo contains:

- **Core Protocol**: `core/`, `consensus/`, `execution/`, `mempool/`, `rpc/`, `p2p/`, `mining/`
  - **P2P Network** (`p2p/`): Full peer-to-peer networking with quantum-resistant handshake, gossip protocol, and multi-transport support
  - **Consensus** (`consensus/`): PoIES algorithm for decentralized block validation
- **Cryptography & Proofs**: `proofs/`, `zk/`, `pq/`, `randomness/`
- **Wallets & Explorer**: `wallet/` (Flutter), `wallet-extension/` (browser extension), `explorer-web/`
- **Mining**: `mining/` (core), `apps/miner-gui/` (Qt desktop GUI miner)
- **Studio & Tooling**: `studio-web/`, `studio-wasm/`, `studio-services/`, `templates/`
- **SDKs & APIs**: `sdk/` (Python/TypeScript/Rust), `docs/` (specifications), `spec/` (canonical schemas)
- **Operations**: `ops/`, `tests/devnet/`, `installers/`, `chains/` (network metadata)
- **Website**: `website/` (Astro + TypeScript main site)
- **Compute Platform**: `packages/` (Auth, Billing, Inference, Sandbox, GitHub App services) - See [COMPUTE_PLATFORM_QUICKSTART.md](COMPUTE_PLATFORM_QUICKSTART.md)

Each module has its own README with detailed information. See the Copilot instructions at the bottom of this file for coding guidelines.

## 🚀 Animica Compute + LLM Cloud Platform

The **Animica Compute Platform** provides enterprise-ready LLM inference, code execution, and GitHub integration with native ANM token payments. 

**Quick Start**: 
```bash
make compute-dev  # Start all compute services with Docker Compose
```

See [COMPUTE_PLATFORM_QUICKSTART.md](COMPUTE_PLATFORM_QUICKSTART.md) for detailed setup instructions.

## 🌍 Website

The production website lives in `website/`. See `website/README.md` for local dev and Docker deployment (nginx on port 4321).

## 🌐 Decentralized Architecture

**Animica is a fully peer-to-peer network** where nodes communicate directly without relying on central servers:

- ✅ **Peer Discovery**: Automatic discovery via DNS seeds, mDNS, and Kademlia DHT
- ✅ **Gossip Protocol**: Efficient block/transaction/proof propagation
- ✅ **Consensus**: Deterministic PoIES validation by all nodes
- ✅ **No Central Authority**: Public RPC nodes like `127.0.0.1` provide public discovery/sync helpers; run your own node for day-to-day queries and mining.

**Run your own node** to strengthen the network and maintain decentralization. See [P2P Networking Guide](docs/P2P_NETWORKING_GUIDE.md) for details.

## 📋 Prerequisites

### Required Software

- **Python 3.11+** with `venv` and `pip`
- **Node.js 20+** with npm
- **Docker** and **Docker Compose** (v2.0+) for running nodes
- **Git** for repository management

### Build Tools (Optional, for Rust Components)

On Ubuntu/Debian:
```bash
sudo apt-get update
sudo apt-get install -y build-essential pkg-config libssl-dev
```

On macOS (via Homebrew):
```bash
brew install pkg-config openssl
```

### Operating System Notes

- **Linux**: Recommended for production (Ubuntu 22.04+ or Debian 12+)
- **macOS**: Fully supported for development (macOS 12+)
- **Windows**: Use WSL2 (Windows Subsystem for Linux) with Ubuntu 22.04+

## 🚀 Installation & Setup

### 🚴‍♂️ CLI Quickstart (Everything End-to-End)

1) **Install dependencies & create the venv**

```bash
./setup.sh            # or: FRESH=1 ./setup.sh --fresh
source .venv/bin/activate
```

2) **Pick the network profile** (determines RPC/P2P ports, data dir, and seeds):

```bash
animica network set devnet          # mainnet | testnet | devnet | local-devnet
animica network get                 # confirm selection
```

3) **Start your node from the CLI** (Docker Compose wrapper):

```bash
animica node up                     # background (default)
animica node up --no-detach         # foreground with logs
animica node up --with-miner        # include miner service
```

4) **Verify health and sync**:

```bash
animica node status                 # chain head, peers, sync info
animica node head                   # latest block header
animica peer list                   # connected peers (expect >0)
```

5) **Ensure peers connect** (connectivity checklist):

```bash
animica peer list --verbose         # shows multiaddrs + scores
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet logs -f node1 | grep p2p
```

If peer count stays 0: verify `ANIMICA_P2P_SEEDS` (or use defaults), open TCP/QUIC ports on your host, and confirm you selected the right network. For a manual seed bump, set:

```bash
export ANIMICA_P2P_SEEDS="/dns4/seed.animica.org/tcp/30333"
animica node down && animica node up
```

6) **Create a wallet and fund it** (dev/test only):

```bash
animica wallet create --label mywallet
animica faucet request mywallet     # devnet/testnet only
animica wallet show mywallet --source chain
```

7) **Send a transaction** (with the running node):

```bash
animica tx send --from mywallet --to anim1recipient... --value 1.0
animica tx status <tx_hash>
```

8) **Stop services** when done:

```bash
animica node down                   # keep data
animica node down --volumes         # wipe data (irreversible)
```

> Need a deeper walkthrough? The sections below expand on each CLI area (networking, node ops, wallets, mining, RPC, Studio Services, Docker, non-Docker boot, and troubleshooting).

### 1. Clone the Repository

```bash
git clone https://github.com/animicaorg/all.git
cd all
```

### 2. Run Setup Script

The `setup.sh` script installs all dependencies and configures the environment:

```bash
# Standard installation
./setup.sh

# Fresh installation (removes existing .venv)
./setup.sh --fresh

# Using environment variable
FRESH=1 ./setup.sh
```

#### Setup Options

Run `./setup.sh --help` to see all options:

```
Options:
  --fresh       Remove existing .venv and perform a clean installation
  -h, --help    Show help message

Environment Variables:
  FRESH=1                 Same as --fresh flag
  PIP_INDEX_URL           Primary pip package index
  PIP_EXTRA_INDEX_URL     Additional pip package index (for custom packages)
```

#### Custom Package Index

For internal deployments or testing with custom package repositories:

```bash
PIP_EXTRA_INDEX_URL=https://your-index.example.com/simple ./setup.sh
```

### 3. Activate Virtual Environment

After setup completes:

```bash
source .venv/bin/activate
```

### 4. Verify Installation

```bash
# Check that the CLI is installed
animica --help

# Test PQ cryptography
python -c "from animica.pq import kem_keygen, kem_encaps, kem_decaps; ek,dk=kem_keygen(); k,ct=kem_encaps(ek); assert kem_decaps(dk,ct)==k; print('✓ KEM ok')"
python -c "from animica.pq import sig_keygen, sig_sign, sig_verify; pk,sk=sig_keygen(); m=b'hi'; s=sig_sign(sk,m); assert sig_verify(pk,m,s); print('✓ SIG ok')"
```

If `animica` is not in your PATH, use the wrapper:

```bash
./animica --help
```

## 🌐 Network Configuration

Animica supports multiple network profiles with isolated data directories and non-conflicting ports:

| Network       | Chain ID | RPC Port | P2P Port | Metrics Port | Use Case                |
|---------------|----------|----------|----------|--------------|-------------------------|
| **mainnet**   | 1        | 8545     | 30333    | 9000         | Production network      |
| **testnet**   | 2        | 18546    | 31334    | 19000        | Public testing          |
| **devnet**    | 1337     | 28545    | 31335    | 29000        | Local development       |
| **local-devnet** | 1337  | 38545    | 31336    | 39000        | Alternative local setup |

### Set Active Network

The active network determines which configuration and data directory the CLI uses:

```bash
# Option 1: Set persistent network preference
animica network set devnet

# Option 2: Set via environment variable (session-only)
export ANIMICA_NETWORK=devnet

# Option 3: Use --network flag per command
animica --network testnet node status
```

### Check Current Network

```bash
animica network get
```

### List Available Networks

```bash
animica network list
```

### Data Directory Isolation

Each network uses its own data directory to prevent state contamination:

- **Mainnet**: `~/.local/share/animica/chain-1/` (Linux) or `~/Library/Application Support/animica/chain-1/` (macOS)
- **Testnet**: `~/.local/share/animica/chain-2/`
- **Devnet**: `~/.local/share/animica/chain-1337/`
- **Local-devnet**: `~/.local/share/animica/chain-1337/`

### Environment Variables

- `ANIMICA_NETWORK`: Active network name (mainnet, testnet, devnet, local-devnet)
- `ANIMICA_RPC_URL`: Override default RPC endpoint
- `ANIMICA_CHAIN_ID`: Override default chain ID

## 🌐 Decentralized P2P Network

**Animica is fully decentralized** - nodes connect directly to each other via P2P without any central authority. The public RPC at `127.0.0.1` is provided only for bootstrapping; wallets, miners, and explorers should use a locally run node after syncing.

### P2P Features

- ✅ **Automatic Peer Discovery**: DNS seeds, mDNS, Kademlia DHT
- ✅ **Quantum-Resistant**: Kyber-768 + Dilithium3 post-quantum crypto
- ✅ **Multi-Transport**: TCP, QUIC, WebSocket support
- ✅ **Gossip Protocol**: Efficient block/tx/proof propagation
- ✅ **Consensus**: Independent PoIES validation by all nodes

### Quick P2P Test

Verify your node connects to the decentralized network:

```bash
# Start a node
animica node up

# Check connected peers (should show 8-16 peers)
animica peer list

# Or via RPC
curl http://localhost:8545/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]
}' | jq .
```

### P2P Configuration

```bash
# P2P is enabled by default
export ANIMICA_P2P_ENABLE=true

# Set listen addresses (for public nodes)
export ANIMICA_P2P_LISTEN_TCP=0.0.0.0:30333
export ANIMICA_P2P_LISTEN_QUIC=0.0.0.0:443

# Set max peers
export ANIMICA_P2P_MAX_PEERS=64

# Use custom seeds
export ANIMICA_P2P_SEEDS="/dns4/my-seed.com/tcp/30333"
```

### Documentation

- **[P2P Networking Guide](docs/P2P_NETWORKING_GUIDE.md)** - Complete P2P architecture and troubleshooting
- **[Multi-Node Docker Setup](docs/MULTI_NODE_DOCKER_SETUP.md)** - Test multi-node networks locally

## 🖥️ Node Operations

### Starting a Node

**Before starting a node, set the network:**

```bash
animica network set devnet
```

#### Start Node with Docker Compose

```bash
# Background mode (default)
animica node up

# Foreground mode with logs
animica node up --no-detach

# Include miner service
animica node up --with-miner

# Skip image rebuild
animica node up --no-build
```

#### Network-Specific Ports

When you start a node, it automatically uses the correct ports for your active network:

- **Mainnet**: RPC `8545`, P2P `30333`, Metrics `9000`
- **Testnet**: RPC `18546`, P2P `31334`, Metrics `19000`
- **Devnet**: RPC `28545`, P2P `31335`, Metrics `29000`
- **Local-devnet**: RPC `38545`, P2P `31336`, Metrics `39000`

#### Custom Port Configuration

Override default ports with environment variables:

```bash
HOST_RPC_PORT=9545 HOST_P2P_PORT=31337 animica node up
```

### Stopping a Node

```bash
# Stop node (preserve data)
animica node down

# Stop and delete all data (WARNING: irreversible!)
animica node down --volumes
```

### Check Node Status

```bash
# Get chain status and head block
animica node status

# Get just the chain head
animica node head

# Get specific block
animica node block --height 100
animica node block --hash 0xabc...

# Get transaction
animica node tx --hash 0x123...
```

### View Node Logs

With Docker Compose directly:

```bash
# For devnet
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet logs -f node1

# For mainnet (when using ops/docker/docker-compose.mainnet.yml)
docker compose -f ops/docker/docker-compose.mainnet.yml logs -f node
```

## 💼 Wallet Management

### Create a Wallet

```bash
# Create new wallet with label
animica wallet create --label mywallet

# For development/testing only (uses fallback crypto if PQ unavailable)
animica wallet create --label devwallet --allow-insecure-fallback
```

### List Wallets

```bash
animica wallet list
```

Output example:
```
Idx Default Label             Address                              Alg
--- ------- ----------------  -----------------------------------  ----------------
  0   *     premine           anim1zqqjt3258rgnfckqxv686unmg...   dilithium3
  1         mywallet          anim1abc123...                        dilithium3
```

### Show Wallet Details

```bash
# By label
animica wallet show mywallet

# By address
animica wallet show anim1abc123...

# Show balance from chain (requires node running)
animica wallet show mywallet --source chain

# Show secret key (WARNING: sensitive!)
animica wallet show mywallet --show-secret --i-know-what-im-doing
```

Set the required environment variable:
```bash
export ANIMICA_ALLOW_SECRET=1
animica wallet show mywallet --show-secret --i-know-what-im-doing
```

### Import a Wallet

```bash
# From JSON file
animica wallet import --file /path/to/wallet.json

# Override label on import
animica wallet import --file wallet.json --label imported

# Force overwrite existing
animica wallet import --file wallet.json --force
```

### Export a Wallet

```bash
# Export by label
animica wallet export mywallet --out /secure/path/wallet-backup.json

# Export by address
animica wallet export anim1abc... --out wallet-backup.json
```

### Set Default Wallet

```bash
animica wallet set-default mywallet
```

### Wallet File Location

By default, wallets are stored in:
- Linux: `~/.animica/wallets.json`
- macOS: `~/.animica/wallets.json`
- Windows (WSL): `~/.animica/wallets.json`

Override with:
```bash
export ANIMICA_WALLETS_FILE=/custom/path/wallets.json
# or
animica wallet --wallet-file /custom/path/wallets.json list
```

## ⛏️ Mining

Mining requires a running node. Mine blocks to earn block rewards.

### GUI Miner (Recommended)

**NEW**: Production-quality Qt desktop GUI miner with first-run wizard, real-time dashboard, device auto-detection, and live stats.

```bash
# Install GUI miner dependencies
cd apps/miner-gui
pip install -e .

# Launch GUI miner
animica gui miner

# Or use the alias
animica-miner-gui
```

**Features**:
- First-run wizard for easy setup (network, RPC, wallet, devices)
- Real-time dashboard with hashrate, shares, and blocks
- Auto-detect CPU/GPU devices with recommendations
- Configuration editor with JSON schema validation
- Live logs with filtering and search
- Hashrate and shares graphs (matplotlib)
- Dark theme and system tray support
- Auto-start mining and crash recovery

See [apps/miner-gui/README.md](apps/miner-gui/README.md) for detailed documentation.

### Mine Blocks via CLI

```bash
# Mine 5 blocks to a wallet (by label)
animica miner mine-blocks --count 5 premine

# Mine to a bech32 address
animica miner mine-blocks --count 10 anim1abc123...

# Mine with verbose output (shows transaction details)
animica miner mine-blocks --count 5 --verbose premine

# Specify RPC URL
animica miner mine-blocks --count 5 --rpc-url http://localhost:8545 mywallet
```

### Continuous Mining (Docker)

The miner service is included when using `--with-miner`:

```bash
animica node up --with-miner
```

Or start miner separately with Docker Compose:

```bash
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet up -d miner
```

### Mining Configuration

Environment variables:

```bash
export ANIMICA_MINER_ADDRESS=anim1...      # Default payout address
export ANIMICA_MINER_MAX_NONCE=100000      # Max nonce iterations
export ANIMICA_MINER_THREADS=4             # CPU mining threads
```

### Stratum Mining Pool (Advanced)

```bash
# Run a Stratum pool
animica miner run-pool \
  --rpc-url http://localhost:8545 \
  --db-url sqlite:///pool.db \
  --stratum-bind 0.0.0.0:3333

# Show pool configuration
animica miner show-config

# Generate pool payout address
animica miner generate-payout-address --label pool-operator
```

## 💸 Transactions

### Send a Transaction

```bash
# Send tokens (by wallet label)
animica tx send --from mywallet --to anim1recipient... --value 1.5

# Send with specific nonce
animica tx send --from mywallet --to anim1... --value 1.0 --nonce 5

# Send to a label (resolves address from wallet)
animica tx send --from sender --to recipient --value 0.5
```

### Check Transaction Status

```bash
# Get transaction by hash
animica tx status <tx_hash>

# Get transaction receipt
animica tx receipt <tx_hash>
```

### Transaction via RPC

```bash
# Using the CLI RPC command
animica rpc call state.getBalance '{"params": ["anim1..."]}'

# Get nonce
animica rpc call state.getNonce '{"params": ["anim1..."]}'
```

## 🔌 RPC Interaction

### Using animica CLI

```bash
# Call RPC method (no params)
animica rpc call chain.getHead

# Call with parameters (JSON)
animica rpc call state.getBalance '{"params": ["anim1..."]}'

# Call with params (array)
animica rpc call chain.getBlockByNumber '{"params": [100, true]}'
```

### Using curl

```bash
# Get chain head
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"chain.getHead","params":[],"id":1}'

# Get balance
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"state.getBalance","params":["anim1..."],"id":1}'

# Get block by height
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"chain.getBlockByNumber","params":[100],"id":1}'
```

### Available RPC Methods

See `spec/openrpc.json` for the complete RPC API specification, or visit the OpenAPI documentation when running a node:

```
http://127.0.0.1:8545/docs
```

## 🚰 Faucet (Devnet/Testnet Only)

Request test tokens for development and testing:

```bash
# Request tokens to a wallet label
animica faucet request mywallet

# Request tokens to a bech32 address
animica faucet request anim1abc123...

# Request specific amount (in nANM)
animica faucet request mywallet --amount 1000000000000000
```

**Note**: Faucet is only available on devnet and testnet. Mainnet requires acquiring ANM through exchanges or mining.

## 🎨 Studio Services (Optional)

Studio Services provide additional developer tools including contract deployment/verification API, artifact storage, and the Explorer web UI.

### Start Studio Services

**Studio Services are optional and separate from the node.** Start the node first, then start Studio Services:

```bash
# Set network
animica network set devnet

# Start node
animica node up

# Start Studio Services (in separate terminal or after)
animica studio up
```

Or start both together by using docker-compose profiles directly (see Docker Compose section).

### Studio Services Commands

```bash
# Start Studio Services
animica studio up
animica studio up --no-detach  # Foreground mode

# Check status
animica studio status

# View logs
animica studio logs
animica studio logs --follow

# Stop Studio Services
animica studio down
animica studio down --volumes  # Also delete storage
```

### Studio Services Endpoints

When Studio Services is running:

- **API**: `http://127.0.0.1:8081`
- **OpenAPI Docs**: `http://127.0.0.1:8081/docs`
- **Explorer**: `http://127.0.0.1:5173` (if enabled)

### Configure Studio Services

```bash
# Validate configuration
animica studio config

# Override configuration
animica studio up \
  --rpc-url http://localhost:8545 \
  --chain-id 1337 \
  --storage-dir ./studio-data
```

## 🐳 Docker Compose Manual Usage

For users who prefer direct Docker Compose commands:

### Devnet

```bash
# Set network context
export ANIMICA_NETWORK=devnet

# Start node + miner (dev profile)
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet \
  --profile dev up -d

# Start node + miner + Studio Services + Explorer (dev + studio profiles)
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet \
  --profile dev --profile studio up -d

# Check running containers
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet ps

# View logs
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet logs -f node1
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet logs -f miner

# Stop everything
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet down

# Stop and remove volumes (deletes all data)
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet down -v
```

### Mainnet

```bash
# Start mainnet node
docker compose -f ops/docker/docker-compose.mainnet.yml up -d

# View logs
docker compose -f ops/docker/docker-compose.mainnet.yml logs -f node

# Stop
docker compose -f ops/docker/docker-compose.mainnet.yml down
```

### Testnet

```bash
# Start testnet node
docker compose -f ops/docker/docker-compose.testnet.yml up -d

# View logs
docker compose -f ops/docker/docker-compose.testnet.yml logs -f node

# Stop
docker compose -f ops/docker/docker-compose.testnet.yml down
```

### Custom Port Configuration

```bash
# Override ports via environment variables
HOST_RPC_PORT=9545 HOST_P2P_PORT=31337 HOST_METRICS_PORT=9090 \
  docker compose -f tests/devnet/docker-compose.yml -p animica-devnet up -d
```

## 🔧 Running Without Docker (Advanced)

For development or when Docker is not available:

### Boot Node Directly

```bash
# Activate environment
source .venv/bin/activate

# Set network
export ANIMICA_NETWORK=devnet

# Boot the node
python -m core.boot \
  --genesis core/genesis/genesis.json \
  --db sqlite:///data/animica.db
```

### Start RPC Server

```bash
# Start RPC server
python -m rpc.server \
  --db sqlite:///data/animica.db \
  --genesis core/genesis/genesis.json \
  --chain-id 1337 \
  --host 0.0.0.0 \
  --port 8545 \
  --cors "[*]" \
  --log-level INFO
```

### Start Miner

```bash
# Start CPU miner
python -m mining.cli.miner start \
  --threads 4 \
  --device cpu \
  --rpc-url http://127.0.0.1:8545
```

## 🌍 Environment Variables Reference

### Network & RPC

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `ANIMICA_NETWORK` | Active network profile | mainnet | `devnet` |
| `ANIMICA_RPC_URL` | Node RPC endpoint | Network-specific | `http://127.0.0.1:8545/rpc` |
| `ANIMICA_CHAIN_ID` | Override chain ID | Network-specific | `1337` |
| `ANIMICA_RPC_HOST` | RPC server bind host | `0.0.0.0` | `127.0.0.1` |
| `ANIMICA_RPC_PORT` | RPC server bind port | `8545` | `9545` |
| `ANIMICA_RPC_DB_URI` | Database URI for RPC | `sqlite:///animica.db` | `sqlite:////data/chain.db` |
| `ANIMICA_LOG_LEVEL` | Logging level | `INFO` | `DEBUG` |
| `ANIMICA_RPC_CORS_ORIGINS` | CORS allowed origins | `[*]` | `["http://localhost:3000"]` |

### Mining

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `ANIMICA_MINER_ADDRESS` | Default miner payout address | None | `anim1abc...` |
| `ANIMICA_MINER_MAX_NONCE` | Max nonce iterations per block | `100000` | `1000000` |
| `MINER_DEVICE` | Mining device | `cpu` | `cuda`, `opencl` |
| `MINER_THREADS` | CPU mining threads | Auto | `4` |
| `MINER_LOG_LEVEL` | Miner log level | `INFO` | `DEBUG` |

### P2P Networking

Bootstrap-only mode is disabled by default. Enable it explicitly with
`ANIMICA_BOOTSTRAP_NODE=true` (or `animica node up --bootstrap-node`) and disable
it with `ANIMICA_BOOTSTRAP_NODE=false` or by unsetting the variable. You can also
override the bootstrap RPC endpoint via `ANIMICA_BOOTSTRAP_RPC_URL`.

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `ANIMICA_P2P_SEEDS` | Seed node addresses | Network-specific | `node1.example.com:30333` |
| `ANIMICA_P2P_LISTEN` | P2P listen address | `0.0.0.0:30333` | `0.0.0.0:31337` |
| `ANIMICA_BOOTSTRAP_NODE` | Enable bootstrap-only RPC mode | `false` | `true` |
| `ANIMICA_BOOTSTRAP_RPC_URL` | Override bootstrap RPC endpoint | Network-specific | `http://127.0.0.1:8545/rpc` |
| `ANIMICA_BOOTSTRAP_PASSWORD` | Bootstrap password (mainnet only) | None | `<secure-password>` |

### Wallet & Keys

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `ANIMICA_WALLETS_FILE` | Wallet store location | `~/.animica/wallets.json` | `/secure/wallets.json` |
| `ANIMICA_ALLOW_SECRET` | Allow secret key display | `0` | `1` (enable) |
| `ANIMICA_DEFAULT_ADDRESS` | Default wallet address | None | `anim1...` |

### Studio Services

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `RPC_URL` | Node RPC endpoint | Required | `http://127.0.0.1:8545` |
| `CHAIN_ID` | Chain ID | `1337` | `1` |
| `STORAGE_DIR` | Storage directory | `./.data` | `/var/studio-data` |
| `HOST` | Studio API bind host | `0.0.0.0` | `127.0.0.1` |
| `PORT` | Studio API bind port | `8081` | `8080` |
| `ALLOWED_ORIGINS` | CORS origins | None | `http://localhost:3000` |
| `FAUCET_KEY` | Faucet private key (dev only) | None | `<hex-encoded-key>` |

### Docker Compose

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `HOST_RPC_PORT` | Host RPC port mapping | Network-specific | `9545` |
| `HOST_P2P_PORT` | Host P2P port mapping | Network-specific | `31337` |
| `HOST_METRICS_PORT` | Host metrics port mapping | Network-specific | `9090` |

### Testing & Development

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `ANIMICA_TESTALL_NO_LINT` | Skip linting in testall.sh | `0` | `1` |
| `ANIMICA_TEST_SIG_ALG` | Force signature algorithm in tests | Auto | `dilithium3` |
| `ANIMICA_PQ_MODE` | Post-quantum mode | `enabled` | `disabled` |
| `ANIMICA_ALLOW_PQ_PURE_FALLBACK` | Allow pure Python PQ fallback | `0` | `1` (dev only) |

## ✅ Testing

### Run All Tests

```bash
# Activate environment
source .venv/bin/activate

# Run complete test suite (Python + Node + Rust)
./testall.sh
```

### Python Tests

```bash
# All Python tests
pytest -q

# Specific modules
pytest consensus/tests/ -v
pytest execution/tests/ -v
pytest rpc/tests/ -v
pytest mempool/tests/ -v
pytest p2p/tests/ -v
pytest wallet/tests/ -v

# With coverage
pytest --cov=consensus consensus/tests/
pytest --cov=execution execution/tests/
```

### Fast Smoke Tests

```bash
# Run only fast unit tests (skip slow integration tests)
pytest -m "not slow and not integration" -q
```

### Specific Test

```bash
# Run specific test file
pytest tests/test_mining_manual.py -v

# Run specific test function
pytest tests/test_mining_manual.py::test_mining_flow -v

# Run with verbose output and stop on first failure
pytest tests/test_mining_manual.py::test_mining_flow -vv -x --tb=long
```

### Test with Devnet

```bash
# Start devnet
animica network set devnet
animica node up

# Run integration tests against devnet
pytest tests/integration/ --rpc http://127.0.0.1:28545

# Stop devnet
animica node down
```

## 🔍 Troubleshooting

### "Network not set" Error

**Problem**: Commands fail with "Error: No network configured"

**Solution**: Set a network first
```bash
animica network set devnet
# or
export ANIMICA_NETWORK=devnet
# or use --network flag
animica --network devnet node status
```

### Port Already in Use

**Problem**: `docker compose up` fails with "port is already allocated"

**Solution**: Check for existing containers or use custom ports
```bash
# Check for existing containers
docker ps

# Stop existing containers
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet down

# Or use custom ports
HOST_RPC_PORT=9545 animica node up
```

### Node Not Syncing

**Problem**: Node doesn't sync or can't connect to peers

**Solution**:
```bash
# Check P2P connectivity
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet logs node1 | grep p2p

# Verify seed nodes
echo $ANIMICA_P2P_SEEDS

# Check network configuration
animica network get
```

### Wallet Not Found

**Problem**: `animica wallet show` returns "Wallet not found"

**Solution**:
```bash
# List all wallets
animica wallet list

# Verify wallet file exists
ls -la ~/.animica/wallets.json

# Check wallet file location
echo $ANIMICA_WALLETS_FILE
```

### Transaction Pending Forever

**Problem**: Transaction stays pending and never confirms

**Solution**: Mine blocks to include the transaction
```bash
# Mine blocks to process pending transactions
animica miner mine-blocks --count 5 premine

# Check transaction status
animica tx status <tx_hash>
```

### Genesis File Not Found

**Problem**: Node fails to start with "Genesis file not found"

**Solution**:
```bash
# Copy appropriate genesis file
bash genesis/devnet.sh

# Or specify path explicitly
python -m core.boot --genesis /path/to/genesis.json
```

### DB Initialization Failed

**Problem**: Database initialization or corruption errors

**Solution**: Remove DB and reinitialize
```bash
# For devnet (chain ID 1337)
rm -rf ~/.local/share/animica/chain-1337/
animica node up

# Or use Docker volumes
animica node down --volumes
animica node up
```

### Docker Compose Issues

**Problem**: Docker Compose fails or containers crash

**Solution**: Reset and rebuild
```bash
# Stop everything and remove volumes
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet down -v

# Clean Docker system (careful!)
docker system prune -af

# Rebuild and start
animica node up --build
```

### PQ Cryptography Issues

**Problem**: Wallet creation fails with PQ errors

**Solution**:
```bash
# Test PQ availability
python -c "from animica.pq import sig_keygen; print('PQ available')"

# For development only: use fallback
animica wallet create --label devwallet --allow-insecure-fallback

# Reinstall PQ package
pip install -e pq/ --force-reinstall
```

### RPC Connection Refused

**Problem**: CLI commands fail with "connection refused"

**Solution**:
```bash
# Check if node is running
docker ps | grep animica

# Check node logs
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet logs node1

# Verify RPC URL
echo $ANIMICA_RPC_URL

# Try connecting manually
curl http://127.0.0.1:8545/rpc
```

### Test Collection Errors

**Problem**: `pytest` fails to collect tests

**Solution**:
```bash
# Some test modules require optional dependencies - these are automatically skipped
# Check which tests are being skipped
pytest --collect-only -q | grep SKIPPED

# Install missing dependencies
pip install -e ".[dev]"

# Reinstall all
./setup.sh --fresh
```

## 📚 Documentation Links

- **Quickstart Guide**: [`QUICKSTART.md`](QUICKSTART.md) - Fast setup and basic operations
- **Architecture Overview**: `docs/ARCHITECTURE.md` - System design and data flow
- **Contract Development**: `docs/dev/CONTRACTS_START.md` - Write Python-VM smart contracts
- **RPC API Reference**: `spec/openrpc.json` - Complete JSON-RPC API specification
- **ABI Schemas**: `spec/abi.schema.json` - Contract ABI format
- **Governance**: `governance/GOVERNANCE.md` - Protocol upgrade process
- **Security**: [`SECURITY.md`](SECURITY.md) - Security policies and reporting
- **Wallet Guide**: `wallet/README.md` - Flutter wallet documentation
- **Explorer**: `explorer-web/README.md` - Block explorer setup
- **Module READMEs**: Each `<module>/README.md` - Component-specific documentation

### Specifications

- **PoIES Consensus**: `spec/poies_math.md` - Consensus algorithm details
- **Gas & VM**: `vm_py/specs/GAS.md`, `vm_py/specs/DETERMINISM.md` - Execution model
- **Receipts**: `execution/specs/RECEIPTS.md` - Transaction receipt format
- **AICF**: `aicf/README.md` - AI Capability Framework lifecycle

## 🤝 Contributing

We welcome contributions! Please follow these guidelines:

- **Keep changes scoped**: Focus on a single area or feature per PR
- **Follow existing patterns**: Review similar code and maintain consistency
- **Write tests**: Add unit tests for new features and bug fixes
- **Update documentation**: Keep README and module docs in sync with code changes
- **Run tests locally**: Ensure `./testall.sh` passes before submitting
- **Use clear commit messages**: Explain what and why, not just how

### Code Style

- Python: Follow PEP 8, use `ruff` for linting
- TypeScript: Follow project ESLint configuration
- Rust: Use `rustfmt` and `clippy`

### Pull Request Process

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes and commit: `git commit -m "feat: add feature X"`
4. Push to your fork: `git push origin feature/my-feature`
5. Open a Pull Request with a clear description
6. Respond to review feedback
7. Squash and merge once approved

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for detailed guidelines.

## 💬 Support

### Getting Help

- **Documentation**: Check module-specific READMEs and `docs/` folder
- **Examples**: Review test files for usage patterns
- **Issues**: Search existing issues or create a new one
- **Discussions**: Use GitHub Discussions for questions

### Reporting Issues

When filing an issue, include:

- **Purpose**: What you're trying to accomplish
- **Environment**: OS, Python version, Docker version
- **Steps to reproduce**: Minimal commands to reproduce the issue
- **Expected vs. actual behavior**: What should happen vs. what happens
- **Logs**: Relevant error messages and stack traces

### Security Issues

**DO NOT** open public issues for security vulnerabilities. Instead:

- Email security@animica.org (if available)
- Or file a private security advisory on GitHub
- See [`SECURITY.md`](SECURITY.md) for our security policy

For security-sensitive topics (keys, proofs, VKs, installer signing), request a security review before merging.
