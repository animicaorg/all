# P2P-First Sync Guide

This guide explains how Animica nodes achieve decentralized consensus through P2P networking instead of relying on centralized RPC endpoints.

## Overview

Animica uses a **P2P-first architecture** where nodes:
- Discover peers through bootstrap seeds and gossip
- Sync headers and blocks via P2P protocols
- Validate blocks locally using deterministic state execution
- Gossip transactions to the mempool
- Achieve consensus without any centralized "source of truth"

**Key principle**: `rpc.animica.org` is a **client-facing service** (for wallets, explorers) but is **NOT** used for node consensus, mining, or validation.

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

P2P is **enabled by default** in all compose files (`P2P_ENABLE=true`).

### Manual Start

```bash
# Set network
export ANIMICA_NETWORK=mainnet
export ANIMICA_CHAIN_ID=1

# Enable P2P (enabled by default)
export P2P_ENABLE=true
export P2P_LISTEN=0.0.0.0:30333

# Seeds are auto-loaded for mainnet, or override:
export P2P_SEEDS="/dns4/mainnet.animica.org/tcp/30333,/ip4/144.126.133.21/tcp/30333"

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

## Comparison: P2P vs. Proxy (Legacy)

| Feature | P2P-First (Default) | Proxy (Deprecated) |
|---------|---------------------|---------------------|
| Decentralized | ✅ Yes | ❌ No (relies on central endpoint) |
| Mainnet Ready | ✅ Yes | ❌ No (centralization risk) |
| Consensus | Local validation | External validation |
| Offline Support | ✅ Works offline with peers | ❌ Requires internet |
| Security | ✅ No single point of failure | ⚠️ Centralized trust |
| Performance | Fast (local) | Slower (network latency) |
| Configuration | Enabled by default | Must explicitly enable |

**Recommendation**: Always use P2P-first for production. Proxy is only for specialized testing.

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
