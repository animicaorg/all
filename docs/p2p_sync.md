# P2P-First Sync Guide

This guide explains how Animica nodes achieve decentralized consensus through P2P networking instead of relying on centralized RPC endpoints.

## Overview

Animica uses a **P2P-first architecture** where nodes:
- Discover peers through bootstrap seeds and gossip
- Sync headers and blocks via P2P protocols
- Validate blocks locally using deterministic state execution
- Gossip transactions to the mempool
- Achieve consensus without any centralized "source of truth"

**Key principle**: `127.0.0.1` is a **client-facing service** (for wallets, explorers) but is **NOT** used for node consensus, mining, or validation.

## Model 3: Hybrid with Optional Checkpoints

As of this version, Animica implements **Model 3 (Hybrid)**, which maintains P2P-first sync as the default while adding an optional checkpoint mechanism for additional safety:

- **Default behavior remains P2P-first** for sync/validation/mining
- **No code path requires `127.0.0.1` to be reachable** by default
- **Optional checkpoint mechanism** can consult a configured RPC URL or local file
- **Checkpoints are safety rails** used during initial sync or fork-choice, not live head oracles
- **Graceful degradation**: if checkpoints are unavailable, sync continues via P2P (unless strict mode is enabled)

## Architecture

```
┌─────────────┐     P2P Sync      ┌─────────────┐
│   Node A    │◄─────────────────►│   Node B    │
│  (Miner)    │   Headers/Blocks  │ (Validator) │
└─────────────┘                    └─────────────┘
       │                                  │
       │ P2P Gossip                      │ P2P Gossip
       ▼                                  ▼
┌─────────────┐                    ┌─────────────┐
│   Node C    │◄──────────────────►│   Node D    │
│   (Full)    │     Discovery      │   (Light)   │
└─────────────┘                    └─────────────┘
```

All consensus happens via P2P. RPC endpoints are only for:
- Wallet queries (balance, nonce)
- Block explorers
- Transaction submission from clients
- Debugging/monitoring

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `P2P_ENABLE` | `true` | Enable P2P networking (required for mainnet) |
| `P2P_LISTEN` | `0.0.0.0:30333` | Listen address for P2P connections |
| `P2P_SEEDS` | (auto) | Bootstrap seed addresses (comma-separated) |
| `ANIMICA_P2P_CHAIN_ID` | (from config) | Chain ID for network-specific seed selection |
| `ANIMICA_PEER_STORE_PATH` | `~/.animica/p2p/{network}` | Persistent peer database location |

### Sync Cache & Background Settings

Animica maintains an on-disk sync cache for headers, block payloads, and sync metadata. The cache
survives restarts and is automatically pruned.

| Variable | Default | Description |
|----------|---------|-------------|
| `ANIMICA_SYNC_CACHE_DIR` | `~/.animica/chain-<id>/sync` | Sync cache directory |
| `ANIMICA_SYNC_CACHE_MAX_MB` | `256` | Max block cache size in MB |
| `ANIMICA_SYNC_CACHE_MAX_BLOCKS` | `2000` | Max cached block payloads |
| `ANIMICA_SYNC_CACHE_MAX_HEADERS` | `5000` | Max cached headers/tips to retain |
| `ANIMICA_SYNC_CACHE_STATE_INTERVAL` | `5` | Seconds between cache state flushes |
| `ANIMICA_SYNC_CACHE_PRUNE_INTERVAL` | `60` | Seconds between cache pruning |

The sync cache is P2P-first and never depends on a trusted RPC endpoint. Invalid cache entries are
automatically dropped and re-fetched from peers.

### Checkpoint Configuration (Optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `ANIMICA_CHECKPOINTS_MODE` | `off` | Checkpoint mode: `off`, `rpc`, or `file` |
| `ANIMICA_CHECKPOINTS_RPC_URL` | `http://127.0.0.1:8545/rpc` | RPC endpoint for fetching checkpoints (when mode=rpc) |
| `ANIMICA_CHECKPOINTS_FILE` | (none) | Path to local JSON checkpoint file (when mode=file) |
| `ANIMICA_CHECKPOINTS_MAX_AGE` | (none) | Maximum age of checkpoints in seconds (optional) |
| `ANIMICA_CHECKPOINTS_STRICT` | `false` | If true, fail fast when checkpoints unavailable |

### Network-Specific Seeds

Seeds are automatically selected based on chain ID:

**Mainnet (chain_id=1)**:
```bash
/dns4/mainnet.animica.org/udp/443/quic-v1
/dns4/mainnet.animica.org/tcp/30333
/ip4/144.126.133.21/udp/443/quic-v1  # IP fallback
/ip4/144.126.133.21/tcp/30333
```

**Testnet (chain_id=2)**:
```bash
/dns4/testnet.animica.org/udp/443/quic-v1
/dns4/testnet.animica.org/tcp/30333
/ip4/144.126.133.21/udp/443/quic-v1
/ip4/144.126.133.21/tcp/30333
```

**Devnet (chain_id=1337)**:
```bash
/dns4/devnet.animica.org/udp/443/quic-v1
/dns4/devnet.animica.org/tcp/30333
/ip4/144.126.133.21/udp/443/quic-v1
/ip4/144.126.133.21/tcp/30333
```

Seeds are loaded from `p2p/fixtures/seed_list.txt` and can be overridden via `P2P_SEEDS`.

## Starting a Node

### Using Docker Compose (Recommended)

```bash
# Mainnet
docker compose -f ops/docker/docker-compose.mainnet.yml up -d

# Testnet  
docker compose -f ops/docker/docker-compose.testnet.yml up -d

# Devnet
docker compose -f ops/docker/docker-compose.devnet.yml up -d
```

P2P is **enabled by default** in all compose files with the following configuration:
- `ANIMICA_P2P_ENABLE=true` — Enables P2P networking
- `ANIMICA_P2P_CHAIN_ID` — Set to network chain ID (1=mainnet, 2=testnet, 1337=devnet)
- Network-specific seeds are **automatically loaded** based on chain ID
- Both TCP (port 30333) and QUIC (UDP port 443) transports are enabled by default

**Important**: When running `animica node up`, ensure the following ports are accessible:
- **TCP 30333** — P2P connections (must be open for peer connectivity)
- **UDP 443** — QUIC transport (preferred, faster than TCP)
- TCP 8545 — RPC (localhost/trusted clients only, DO NOT expose publicly)

### Manual Start

```bash
# Set network
export ANIMICA_NETWORK=mainnet
export ANIMICA_CHAIN_ID=1

# Enable P2P (enabled by default)
export ANIMICA_P2P_ENABLE=true
export ANIMICA_P2P_CHAIN_ID=1  # Auto-loads mainnet seeds

# Optional: Override default listen addresses
export ANIMICA_P2P_LISTEN_TCP=0.0.0.0:30333
export ANIMICA_P2P_LISTEN_QUIC=0.0.0.0:443

# Optional: Override seeds (not recommended - use auto-selected seeds)
# export ANIMICA_P2P_SEEDS="/dns4/mainnet.animica.org/tcp/30333,/dns4/mainnet.animica.org/udp/443/quic-v1"

# Start node
python -m rpc
```

The node will:
1. Initialize P2P service with persistent peer store
2. Connect to bootstrap seeds
3. Discover additional peers via gossip
4. Sync headers from genesis to head
5. Download and validate blocks
6. Join mempool gossip for new transactions

## Verifying P2P Operation

### Check Connected Peers

```bash
# Via RPC
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"p2p.listPeers","id":1}'

# Expected response:
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "peers": [
      {
        "peer_id": "12D3KooWBm...",
        "addr": "/ip4/144.126.133.21/tcp/30333",
        "connected_at": 1702934400,
        "protocols": ["animica/sync/1", "animica/gossip/1"]
      }
    ],
    "total": 5
  }
}
```

### Check Sync Status

```bash
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"chain.getHead","id":1}'
```

You can also use the sync control RPCs (or the CLI wrappers below):

```bash
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"sync.getStatus","id":1}'
```

### Sync Control (CLI)

```bash
animica sync status
animica sync pause
animica sync resume
animica sync force --clear-cache
```

### Monitor Logs

```bash
# Docker
docker logs -f animica-mainnet-node

# Look for:
# - "P2P service started"
# - "Connected to peer 12D3KooW..."
# - "Synced block 123 from peer"
# - "Broadcasting transaction to 5 peers"
```

## Mining with P2P

Mining uses **local P2P validation** by default (no proxy):

```bash
# Mine 5 blocks (uses P2P consensus)
animica miner mine-blocks --count 5 premine

# Output:
# Mining 5 block(s) with local P2P validation with payout to address anim1...
# Using device: auto
#   Block 1/5 mined (height: 101, reward: 5.000000000 ANM = 5000000000 nANM)
#   Block 2/5 mined (height: 102, reward: 5.000000000 ANM = 5000000000 nANM)
# ...
```

The miner:
1. Queries local mempool via RPC for pending transactions
2. Builds block template with selected transactions
3. Performs PoW hash search to meet difficulty target
4. Validates block locally against P2P-synced state
5. Broadcasts block to peers via P2P gossip
6. Peers validate and propagate the block

**No external RPC calls are made for consensus validation.**

## Transaction Submission

Transactions are submitted to local node and gossiped to peers:

```bash
# Submit transaction
animica tx send --to anim1... --amount 10

# Process:
# 1. Client submits to local node RPC
# 2. Node validates signature and nonce
# 3. Node adds to local mempool
# 4. Node broadcasts to P2P peers via gossip
# 5. Peers validate and propagate
# 6. Miners include in next block
```

## Peer Persistence

Peers are stored persistently to avoid re-discovery on restart:

```bash
# Default locations:
~/.animica/p2p/mainnet/peerstore.db   # Mainnet peers
~/.animica/p2p/testnet/peerstore.db   # Testnet peers
~/.animica/p2p/devnet/peerstore.db    # Devnet peers

# Override:
export ANIMICA_PEER_STORE_PATH=/custom/path
```

The peer store tracks:
- Peer IDs and addresses
- Last seen timestamp
- Connection success/failure history
- Protocol versions supported

## Ports and Firewall

Open these ports for P2P:

| Port | Protocol | Purpose |
|------|----------|---------|
| 30333 | TCP | P2P connections (default) |
| 443 | UDP | QUIC (preferred transport) |
| 8545 | TCP | RPC (localhost or trusted clients only) |
| 9000 | TCP | Metrics (localhost only) |

**Security**:
- **DO NOT** expose RPC (8545) to public internet
- P2P ports (30333, 443) should be public for peer connections
- Use firewall to restrict RPC access to trusted IPs

## Troubleshooting

### No Peers Connected

**Symptoms**: `p2p.listPeers` returns empty list

**Solutions**:
```bash
# 1. Check seeds are configured
echo $P2P_SEEDS

# 2. Verify network connectivity
ping mainnet.animica.org
nc -zv 144.126.133.21 30333

# 3. Check firewall (allow outbound 30333)
sudo ufw allow out 30333/tcp

# 4. Enable debug logging
export ANIMICA_LOG_LEVEL=DEBUG
python -m rpc
```

### Sync Stuck

**Symptoms**: Chain head not advancing

**Solutions**:
```bash
# 1. Check peer count
curl -X POST http://localhost:8545/rpc \
  -d '{"jsonrpc":"2.0","method":"p2p.listPeers","id":1}'

# 2. Force peer refresh
# Stop node, clear peer store, restart:
rm ~/.animica/p2p/mainnet/peerstore.db
python -m rpc

# 3. Check block validation errors in logs
docker logs animica-mainnet-node | grep -i "error\|validation"
```

### Mining Not Including Transactions

**Symptoms**: Mined blocks have no transactions despite mempool having pending txs

**Solutions**:
```bash
# 1. Check mempool
curl -X POST http://localhost:8545/rpc \
  -d '{"jsonrpc":"2.0","method":"mempool.pending","id":1}'

# 2. Check transaction nonces (must be sequential)
curl -X POST http://localhost:8545/rpc \
  -d '{"jsonrpc":"2.0","method":"account.getNonce","params":["anim1..."],"id":1}'

# 3. Check transaction fees (may be too low)
# Transactions must meet minimum fee policy
```

## E2E Testing: Two Nodes Syncing

Test that nodes sync via P2P without internet:

```bash
# Terminal 1: Start node A (miner)
export ANIMICA_CHAIN_ID=1337  # devnet
export P2P_LISTEN=0.0.0.0:30333
export P2P_SEEDS=""  # no external seeds
export ANIMICA_RPC_PORT=8545
python -m rpc

# Terminal 2: Start node B (syncer)
export ANIMICA_CHAIN_ID=1337
export P2P_LISTEN=0.0.0.0:30334
export P2P_SEEDS="/ip4/127.0.0.1/tcp/30333"  # connect to node A
export ANIMICA_RPC_PORT=8546
python -m rpc

# Terminal 3: Mine blocks on node A
curl -X POST http://localhost:8545/rpc \
  -d '{"jsonrpc":"2.0","method":"miner.mine","params":[{"count":5}],"id":1}'

# Terminal 4: Verify node B synced
curl -X POST http://localhost:8546/rpc \
  -d '{"jsonrpc":"2.0","method":"chain.getHead","id":1}'

# Should show same height as node A
```

## Advanced: Custom Seed Lists

For private networks, create custom seed list:

```bash
# seeds.txt
/ip4/192.168.1.100/tcp/30333
/ip4/192.168.1.101/tcp/30333
/dns4/seed.mynetwork.local/tcp/30333

# Use custom seeds
export P2P_SEEDS="$(cat seeds.txt | tr '\n' ',')"
python -m rpc
```

## Checkpoints (Model 3)

Checkpoints provide an optional safety mechanism to verify the canonical chain against known-good block hashes at specific heights. This is useful for:
- Initial sync on a fresh node (avoid syncing to a minority fork)
- Fork choice / reorg validation (prevent deep reorgs to bad chains)

### Checkpoint Modes

**Off (default)**:
```bash
export ANIMICA_CHECKPOINTS_MODE=off
```
- No checkpoints used
- Pure P2P consensus
- No external dependencies

**RPC Mode**:
```bash
export ANIMICA_CHECKPOINTS_MODE=rpc
export ANIMICA_CHECKPOINTS_RPC_URL=http://127.0.0.1:8545/rpc
```
- Fetches checkpoints from RPC endpoint
- Tries `chain.getCheckpoints` JSON-RPC method first
- Falls back to HTTP endpoints (`/checkpoints.json`, `/checkpoints`)
- Non-strict by default: continues without checkpoints if unavailable

**File Mode**:
```bash
export ANIMICA_CHECKPOINTS_MODE=file
export ANIMICA_CHECKPOINTS_FILE=~/.animica/checkpoints.json
```
- Loads checkpoints from local JSON file
- No network calls
- Useful for air-gapped or private networks

### Checkpoint Format

Checkpoints are stored in JSON format:

```json
{
  "checkpoints": [
    {"height": 1000, "hash": "0x1234abcd..."},
    {"height": 2000, "hash": "0x5678ef01..."},
    {"height": 3000, "hash": "0x9abc2345..."}
  ],
  "timestamp": 1234567890
}
```

Or as a plain list:

```json
[
  {"height": 1000, "hash": "0x1234abcd..."},
  {"height": 2000, "hash": "0x5678ef01..."}
]
```

### Strict Mode

By default, if checkpoints are unavailable, the node continues syncing via P2P with a warning. Enable strict mode to fail fast:

```bash
export ANIMICA_CHECKPOINTS_STRICT=true
```

With strict mode:
- Node will refuse to start if checkpoints cannot be loaded
- Checkpoint mismatches will halt sync immediately
- Useful for production deployments requiring additional validation

### Checkpoint Verification

When checkpoints are enabled, the node verifies:
1. During initial sync: blocks at checkpoint heights match expected hashes
2. During fork choice: new best chain matches checkpoints
3. If mismatch detected: chain is rejected with clear error logs

**Important**: Checkpoints are **safety rails**, not consensus rules. They:
- Do NOT replace P2P validation
- Do NOT require `127.0.0.1` to be reachable by default
- Are optional and can be disabled entirely

## Comparison: P2P vs. Proxy (Legacy)

| Feature | P2P-First (Default) | P2P + Checkpoints | Proxy (Deprecated) |
|---------|---------------------|-------------------|---------------------|
| Decentralized | ✅ Yes | ✅ Yes | ❌ No (relies on central endpoint) |
| Mainnet Ready | ✅ Yes | ✅ Yes | ❌ No (centralization risk) |
| Consensus | Local validation | Local validation + checkpoints | External validation |
| Offline Support | ✅ Works offline with peers | ⚠️ Checkpoints need fetch once | ❌ Requires internet |
| Security | ✅ No single point of failure | ✅ Additional safety rail | ⚠️ Centralized trust |
| Performance | Fast (local) | Fast (local) | Slower (network latency) |
| Configuration | Enabled by default | Opt-in via env vars | Must explicitly enable |

**Recommendation**: 
- Use **P2P-first** (default) for most deployments
- Add **checkpoints** for additional safety in production
- **Never use proxy** for mainnet

## Further Reading

- [P2P Architecture Specs](../p2p/specs/)
- [Consensus Protocol](../consensus/README.md)
- [Mining Guide](tutorials/MINING_GUIDE.md)
- [Docker Compose Examples](../ops/docker/)

## Support

For P2P issues:
- GitHub: https://github.com/animicaorg/all/issues
- Discord: https://discord.gg/animica
- Check logs for detailed error messages
