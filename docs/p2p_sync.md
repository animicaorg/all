# P2P-First Sync and Bootstrap

## Overview

Animica nodes use **P2P-first decentralized sync** via bootstrap seeds, NOT via a trusted HTTP RPC endpoint. This design ensures:

- **Decentralization**: No single point of trust or failure
- **Censorship resistance**: Nodes can sync from any peer
- **Network resilience**: Multiple bootstrap seeds with automatic fallback
- **Security**: P2P handshake uses post-quantum cryptography (Kyber768 + Dilithium3)

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Animica Node (python -m rpc)                               │
│                                                              │
│  ┌──────────────┐         ┌──────────────┐                 │
│  │  RPC Server  │         │  P2P Service │                 │
│  │  (HTTP/WS)   │         │  (TCP/QUIC)  │                 │
│  │  Port 8545   │         │  Port 30333  │                 │
│  └──────────────┘         └──────────────┘                 │
│         │                         │                         │
│         │                         │                         │
│    Client APIs            P2P Bootstrap & Sync             │
│    (read-only)            (consensus, blocks, txs)         │
└─────────────────────────────────────────────────────────────┘
         │                         │
         │                         │
         ▼                         ▼
┌──────────────────┐    ┌──────────────────────┐
│  Client Wallets  │    │  Bootstrap Seeds     │
│  (SDK/CLI)       │    │  mainnet.animica.org │
│                  │    │  testnet.animica.org │
│  Read-only ops   │    │  devnet.animica.org  │
└──────────────────┘    └──────────────────────┘
```

## Bootstrap Seeds

### Mainnet (chain_id=1)

- **DNS Seeds**:
  - `mainnet.animica.org` (TCP 30333 + UDP 443 QUIC)
  - Primary IP: `144.126.133.21`

- **Multiaddr Format**:
  ```
  /dns4/mainnet.animica.org/tcp/30333
  /dns4/mainnet.animica.org/udp/443/quic-v1
  /ip4/144.126.133.21/tcp/30333
  /ip4/144.126.133.21/udp/443/quic-v1
  ```

### Testnet (chain_id=2)

- **DNS Seeds**:
  - `testnet.animica.org` (TCP 30333 + UDP 443 QUIC)

### Devnet (chain_id=1337)

- **DNS Seeds**:
  - `devnet.animica.org` (TCP 30333 + UDP 443 QUIC)

### Automatic Selection

Seeds are automatically selected based on `chain_id` when `ANIMICA_P2P_CHAIN_ID` is set. No manual configuration needed for standard networks.

## P2P Transports

### TCP (Port 30333)

- **Protocol**: TCP with PQ handshake
- **Use**: Reliable peer connections, header/block sync
- **Config**: `ANIMICA_P2P_ENABLE_TCP=true` (default)

### QUIC (UDP 443)

- **Protocol**: QUIC with PQ handshake
- **Use**: Low-latency connections, NAT traversal
- **Port**: UDP 443 (production standard)
- **Config**: `ANIMICA_P2P_ENABLE_QUIC=true` (default)

**IMPORTANT**: QUIC uses UDP 443. HTTPS uses TCP 443. Both can coexist on the same host.

### WebSocket (Port 30335)

- **Protocol**: WS/WSS for browser clients
- **Use**: Studio Web, wallet extensions
- **Config**: `ANIMICA_P2P_ENABLE_WS=true` (default)

## Starting a Node

### Docker (Mainnet)

```bash
# Set network
animica network set mainnet

# Start node (P2P enabled by default)
animica node up

# Node will bootstrap via mainnet.animica.org
# and sync headers/blocks via P2P
```

### Docker Compose (Explicit)

```bash
cd ops/docker
docker compose -f docker-compose.mainnet.yml up -d
```

The mainnet compose exposes:
- **8545**: HTTP RPC (client APIs)
- **30333**: P2P TCP
- **443/udp**: P2P QUIC
- **9000**: Prometheus metrics

### Python (Development)

```bash
# Export P2P config
export ANIMICA_P2P_ENABLE=true
export ANIMICA_P2P_CHAIN_ID=1  # mainnet seeds auto-selected
export ANIMICA_CHAIN_ID=1

# Start RPC server (includes P2P)
python -m rpc
```

## Mining (P2P-First)

Mining should use the **local node's RPC**, NOT a remote trusted endpoint:

```bash
# CORRECT: Mine to local node (synced via P2P)
animica miner mine-blocks --count 5 premine

# INCORRECT: Mine with deprecated proxy (centralized)
animica miner mine-blocks --count 5 premine --use-proxy
```

The `--use-proxy` flag is **deprecated** and should NOT be used for production mining.

## Verifying P2P Connectivity

### Check Peers

```bash
# Via CLI
animica peer list

# Via RPC
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"p2p.listPeers","id":1}'
```

### Expected Output

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "peers": [
      {
        "peer_id": "0xabc123...",
        "addrs": ["/ip4/144.126.133.21/tcp/30333"],
        "protocols": ["animica/1"],
        "connected_at": "2025-12-16T18:00:00Z"
      }
    ],
    "total": 1
  }
}
```

## Firewall & Port Configuration

### Required Ports

| Port | Protocol | Direction | Purpose |
|------|----------|-----------|---------|
| 8545 | TCP | Inbound | HTTP RPC (client APIs) |
| 30333 | TCP | Inbound/Outbound | P2P TCP connections |
| 443 | UDP | Inbound/Outbound | P2P QUIC connections |
| 9000 | TCP | Inbound (optional) | Prometheus metrics |

### Firewall Rules (Example: ufw)

```bash
# Allow RPC (if serving clients)
sudo ufw allow 8545/tcp

# Allow P2P TCP
sudo ufw allow 30333/tcp

# Allow P2P QUIC (UDP 443)
sudo ufw allow 443/udp

# Allow metrics (optional)
sudo ufw allow 9000/tcp
```

### Nginx Reverse Proxy

**CRITICAL**: Nginx must NOT terminate UDP 443 QUIC traffic.

```nginx
# HTTPS (TCP 443) - can be reverse proxied
server {
    listen 443 ssl http2;
    server_name rpc.animica.org;
    
    location /rpc {
        proxy_pass http://localhost:8545;
        # ... standard reverse proxy config
    }
}

# QUIC (UDP 443) - must pass through
# Do NOT configure nginx to handle UDP 443
# The Animica node listens directly on UDP 443
```

**Port Coexistence**:
- HTTPS uses **TCP 443** (nginx handles)
- QUIC uses **UDP 443** (node handles)
- Both can run on the same host simultaneously

## Peer Persistence

Peers are persisted to disk to reduce bootstrap time on restart:

```bash
# Default peer store location (network-specific)
~/.animica/p2p/mainnet/peerstore.db
~/.animica/p2p/testnet/peerstore.db
~/.animica/p2p/devnet/peerstore.db

# Custom location
export ANIMICA_PEER_STORE_PATH=~/.animica/custom_peers
```

## Environment Variables

### P2P Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ANIMICA_P2P_ENABLE` | `true` | Enable P2P networking |
| `ANIMICA_P2P_CHAIN_ID` | (auto) | Chain ID for seed selection |
| `ANIMICA_P2P_ENABLE_TCP` | `true` | Enable TCP transport |
| `ANIMICA_P2P_ENABLE_QUIC` | `true` | Enable QUIC transport |
| `ANIMICA_P2P_ENABLE_WS` | `true` | Enable WebSocket transport |
| `ANIMICA_P2P_LISTEN_TCP` | `0.0.0.0:30333` | TCP listen address |
| `ANIMICA_P2P_LISTEN_QUIC` | `0.0.0.0:443` | QUIC listen address |
| `ANIMICA_P2P_SEEDS` | (auto) | Bootstrap seeds (comma-separated) |
| `ANIMICA_P2P_MAX_PEERS` | `50` | Maximum peer connections |
| `ANIMICA_PEER_STORE_PATH` | (auto) | Peer store database path |

### Legacy Variables (Backward Compatibility)

| Variable | Equivalent | Notes |
|----------|------------|-------|
| `P2P_ENABLE` | `ANIMICA_P2P_ENABLE` | For backward compat |
| `P2P_LISTEN` | `ANIMICA_P2P_LISTEN_TCP` | TCP only |
| `P2P_SEEDS` | `ANIMICA_P2P_SEEDS` | Override auto-selection |

## Deprecated: Trusted RPC Proxy

The RPC proxy (`rpc/proxy.py`) is **deprecated** for node operations:

### ❌ DO NOT USE FOR:
- Node consensus or sync
- Mining or block validation
- Any operation requiring chain truth

### ✅ ONLY USE FOR:
- Client-side wallet applications (read-only)
- Development/testing scenarios
- External monitoring tools

### Why Deprecated?

Using a trusted HTTP RPC endpoint (e.g., `rpc.animica.org`) for node operations:
- **Centralizes trust**: Single point of failure
- **Breaks decentralization**: Defeats P2P design
- **Security risk**: Remote endpoint could be compromised
- **Censorship risk**: Remote endpoint could filter/block

### Migration

**Before** (Deprecated):
```bash
# Mining with proxy (centralized, NOT recommended)
export ANIMICA_TRUSTED_RPC_URL=https://rpc.animica.org/rpc
animica miner mine-blocks --count 5 premine --use-proxy
```

**After** (P2P-First):
```bash
# Start local node (synced via P2P)
animica node up

# Mine to local node (decentralized, recommended)
animica miner mine-blocks --count 5 premine
```

## Troubleshooting

### No Peers Connected

1. **Check P2P is enabled**:
   ```bash
   curl http://localhost:8545/readyz
   # Should show P2P service running
   ```

2. **Check firewall**:
   ```bash
   sudo ufw status
   # Ensure ports 30333 and 443/udp are open
   ```

3. **Check seeds**:
   ```bash
   # Verify seeds are configured
   grep -r "mainnet.animica.org" ~/.animica/
   ```

4. **Manual seed connection** (debug):
   ```bash
   # Force specific seed
   export ANIMICA_P2P_SEEDS=/dns4/mainnet.animica.org/tcp/30333
   python -m rpc
   ```

### Sync Stalled

1. **Check peer count**:
   ```bash
   animica peer list
   # Should have at least 1 peer
   ```

2. **Check logs**:
   ```bash
   docker logs animica-mainnet-node
   # Look for P2P sync progress
   ```

3. **Restart node**:
   ```bash
   animica node down
   animica node up
   # Bootstrap will retry
   ```

### QUIC Port Conflict

If UDP 443 is in use:

```bash
# Option 1: Use different QUIC port
export ANIMICA_P2P_LISTEN_QUIC=0.0.0.0:30334
export HOST_P2P_QUIC_PORT=30334

# Option 2: Disable QUIC (use TCP only)
export ANIMICA_P2P_ENABLE_QUIC=false
```

## Security

### Post-Quantum Handshake

All P2P connections use PQ-secure handshake:
- **Key Exchange**: Kyber768 (ML-KEM)
- **Signatures**: Dilithium3 (ML-DSA)
- **AEAD**: ChaCha20-Poly1305

### Peer Authentication

Peers are authenticated via:
1. **Node Key**: Long-term Dilithium3 keypair
2. **Peer ID**: Derived from public key
3. **Hello Protocol**: Exchange chain_id, head, capabilities

### Network Isolation

Mainnet, testnet, and devnet use different:
- Seeds
- Peer stores
- Protocol prefixes

Cross-network contamination is prevented by design.

## Reference

- **Consensus**: `consensus/` (PoIES scoring)
- **P2P Stack**: `p2p/` (transports, gossip, sync)
- **Config**: `p2p/config.py` (seed selection)
- **Bootstrap**: `rpc/deps.py` (P2P service init)
- **Specs**: `spec/` (network parameters)

## Support

For issues or questions:
- **GitHub Issues**: https://github.com/animicaorg/all/issues
- **Discord**: https://discord.gg/animica (if available)
- **Docs**: `/docs/` directory in this repository
