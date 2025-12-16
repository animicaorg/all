# Animica P2P Networking Guide

## Overview

**Animica is a fully decentralized blockchain** with peer-to-peer (P2P) networking built into its core architecture. This guide explains how the P2P layer works, how nodes discover and connect to each other, and how the network achieves consensus without relying on a central authority.

## Architecture

### Decentralized Design

Animica uses a **gossip-based P2P protocol** where nodes communicate directly with each other to:

1. **Discover Peers**: Find other nodes on the network
2. **Sync Blockchain**: Exchange blocks and headers
3. **Broadcast Transactions**: Share new transactions with the network
4. **Share Proofs**: Distribute mining shares (hash work, AI, quantum proofs)
5. **Reach Consensus**: Validate and agree on the canonical chain

**There is no central server** - the network operates through direct peer-to-peer connections. The RPC endpoint at `rpc.animica.org` is simply **one node among many** that provides public API access, not a central authority.

### Network Topology

```
        Node A                Node B                Node C
     (Mining Node)        (Full Node RPC)        (Full Node)
          |                     |                     |
          +---------------------+---------------------+
          |                     |                     |
     Gossip: Blocks        Sync: Headers         Share: Txs
          |                     |                     |
     [Connected to 8 peers] [Connected to 12 peers] [Connected to 6 peers]
          |                     |                     |
    Listens on:            Listens on:           Listens on:
    TCP 30333              TCP 30333             TCP 30333
    QUIC 443               QUIC 443              QUIC 443
```

Every node can:
- Accept incoming connections from other nodes (inbound peers)
- Initiate connections to other nodes (outbound peers)
- Relay information to all connected peers (gossip protocol)

## P2P Components

### 1. Peer Discovery

Nodes discover each other through multiple mechanisms:

#### DNS Seeds (Primary)

Network-specific DNS seeds provide initial bootstrap nodes:

```bash
# Mainnet seeds
mainnet.animica.org:30333 (TCP)
mainnet.animica.org:443   (QUIC)

# Testnet seeds
testnet.animica.org:30333 (TCP)
testnet.animica.org:443   (QUIC)

# Devnet seeds
devnet.animica.org:30333 (TCP)
devnet.animica.org:443   (QUIC)
```

DNS seeds are automatically selected based on your network (chain ID):
- Chain ID 1 → Mainnet seeds
- Chain ID 2 → Testnet seeds
- Chain ID 1337 → Devnet seeds

#### IP Fallback

If DNS resolution fails, nodes fall back to hardcoded IP addresses:

```
Primary fallback: 144.126.133.21
Ports: TCP 30333, QUIC 443
```

#### Kademlia DHT (Optional)

A distributed hash table (DHT) allows nodes to find each other without central coordination. Enable with:

```bash
export ANIMICA_P2P_KADEMLIA=true
```

#### mDNS (Local Discovery)

For local development, nodes can discover each other on the same LAN using multicast DNS:

```bash
export ANIMICA_P2P_MDNS=true
```

### 2. Transports

Animica supports three transport protocols:

| Transport | Port | Encryption | Use Case |
|-----------|------|------------|----------|
| **TCP** | 30333 | AEAD | General purpose, widest compatibility |
| **QUIC** | 443 | Built-in TLS | Faster handshake, better for mobile/NAT |
| **WebSocket** | 30335 | AEAD | Browser nodes, web wallets |

All transports use **post-quantum cryptography**:
- **Kyber-768** for key exchange (quantum-resistant KEM)
- **Dilithium3** or **SPHINCS+** for authentication
- **ChaCha20-Poly1305** for message encryption (AEAD)

### 3. Gossip Protocol

The gossip layer broadcasts information across the network using a **mesh topology**:

```python
Topics:
- blocks    → Full block announcements
- headers   → Lightweight header sync
- txs       → Transaction relay
- shares    → Mining shares (hash, AI, quantum)
- blobs     → Data availability commitments
```

**Rate Limits** prevent spam:
- Transactions: 200/sec per peer
- Blocks: 60/min per peer
- Shares: configurable via policy

**Duplicate Detection**: Bloom filters prevent re-broadcasting the same data.

### 4. Synchronization

Nodes sync the blockchain using a headers-first approach:

1. **Headers Sync**: Download and verify lightweight headers
2. **Block Sync**: Fetch full blocks for validated headers
3. **State Sync**: Execute transactions to rebuild state
4. **Mempool Sync**: Share pending transactions

**Flow Control**: Credit-based system prevents overwhelming slower peers.

### 5. Consensus (PoIES)

**Proof-of-Integrated-External-Services (PoIES)** is Animica's consensus mechanism:

```
Block Acceptance Score:
S = H(u) + Σ ψ(proof)

Where:
- H(u) = -ln(u) from hash work (like PoW)
- ψ(proof) = contribution from AI/Quantum/Storage proofs

Accept block if: S ≥ Θ (difficulty threshold)
```

**Decentralized Validation**:
- Any node can validate blocks independently
- No leader election or validator set
- Fork choice: longest chain with highest cumulative work
- Deterministic: same inputs always produce same result

**No Central Authority**:
- Miners compete to produce valid blocks
- Nodes independently verify and accept/reject
- Network converges on canonical chain through gossip
- Malicious blocks are rejected by honest nodes

## Running a Decentralized Node

### Quick Start

```bash
# 1. Clone and setup
git clone https://github.com/animicaorg/all.git
cd all
./setup.sh
source .venv/bin/activate

# 2. Set your network (mainnet, testnet, or devnet)
export ANIMICA_NETWORK=mainnet

# 3. Enable P2P (enabled by default)
export ANIMICA_P2P_ENABLE=true

# 4. Start the node
python -m rpc.server --db ~/.animica/chain-1/mainnet.db
```

Your node will:
1. ✅ Connect to network-specific seed nodes
2. ✅ Discover and connect to other peers
3. ✅ Sync the blockchain from peers
4. ✅ Relay transactions and blocks
5. ✅ Accept RPC connections for wallet/explorer access

### Configuration

#### Listen Addresses

Configure where your node listens for incoming connections:

```bash
# Listen on all interfaces (public node)
export ANIMICA_P2P_LISTEN_TCP=0.0.0.0:30333
export ANIMICA_P2P_LISTEN_QUIC=0.0.0.0:443

# Listen only on localhost (private node)
export ANIMICA_P2P_LISTEN_TCP=127.0.0.1:30333
```

#### Advertised Addresses

Tell other nodes how to reach you (important for NAT/firewall):

```bash
# Use your public IP
export ANIMICA_P2P_ADVERTISED_ADDRS="/ip4/203.0.113.5/tcp/30333"

# Use your domain
export ANIMICA_P2P_ADVERTISED_ADDRS="/dns4/node.example.com/tcp/30333"

# Multiple addresses
export ANIMICA_P2P_ADVERTISED_ADDRS="/ip4/203.0.113.5/tcp/30333,/dns4/node.example.com/quic/443"
```

#### Peer Limits

Control how many peers your node connects to:

```bash
export ANIMICA_P2P_MAX_PEERS=64        # Total peers
export ANIMICA_P2P_MAX_OUTBOUND=16     # Outbound connections
```

Higher peer counts improve:
- ✅ Network resilience
- ✅ Sync speed
- ✅ Gossip coverage
- ❌ Bandwidth usage
- ❌ Memory usage

#### Custom Seeds

Override default seeds to bootstrap from your own nodes:

```bash
export ANIMICA_P2P_SEEDS="/ip4/1.2.3.4/tcp/30333,/dns4/my-seed.com/quic/443"
```

### Firewall Configuration

Open ports for incoming connections:

```bash
# Ubuntu/Debian (ufw)
sudo ufw allow 30333/tcp  # TCP transport
sudo ufw allow 443/udp    # QUIC transport

# CentOS/RHEL (firewalld)
sudo firewall-cmd --permanent --add-port=30333/tcp
sudo firewall-cmd --permanent --add-port=443/udp
sudo firewall-cmd --reload
```

### Docker Deployment

Run a full node with Docker:

```yaml
# docker-compose.yml
version: '3.8'
services:
  animica-node:
    image: animica/node:latest
    restart: unless-stopped
    environment:
      - ANIMICA_NETWORK=mainnet
      - ANIMICA_P2P_ENABLE=true
      - ANIMICA_P2P_LISTEN_TCP=0.0.0.0:30333
      - ANIMICA_P2P_LISTEN_QUIC=0.0.0.0:443
      - ANIMICA_P2P_MAX_PEERS=64
    ports:
      - "8545:8545"    # RPC
      - "30333:30333"  # P2P TCP
      - "443:443/udp"  # P2P QUIC
    volumes:
      - ./data:/root/.animica
```

```bash
docker-compose up -d
```

## Verifying P2P Connectivity

### 1. Check Connected Peers

```bash
# Using CLI
animica peer list

# Using RPC
curl -s http://localhost:8545/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]
}' | jq .

# Expected output:
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": [
    {
      "id": "12D3KooWPeer...",
      "addr": "/ip4/203.0.113.10/tcp/30333",
      "status": "connected",
      "direction": "outbound",
      "latencyMs": 45.2
    },
    ...
  ]
}
```

**Healthy Node**: 8-16 peers connected
**Warning**: <3 peers (limited connectivity)
**Problem**: 0 peers (check firewall/network)

### 2. Monitor Sync Status

```bash
# Check current height
animica node status

# Watch sync progress
watch -n 2 'animica node status | grep height'
```

### 3. Test Peer Addition

Manually connect to a specific node:

```bash
# Add peer
animica peer add /ip4/203.0.113.10/tcp/30333

# Verify connection
animica peer list | grep 203.0.113.10
```

### 4. Check P2P Metrics

If metrics are enabled:

```bash
curl -s http://localhost:9000/metrics | grep p2p_

# Key metrics:
# p2p_peers{state="connected"}      → Number of connected peers
# p2p_msgs_total{topic="blocks"}    → Messages received per topic
# p2p_bytes_total{dir="rx"}         → Bytes received
# p2p_rtt_seconds                   → Peer latency
```

### 5. View P2P Logs

```bash
# If running with systemd
journalctl -u animica-node -f | grep p2p

# Docker
docker logs -f animica-node | grep p2p

# Direct run (with DEBUG logging)
export RUST_LOG=debug
python -m rpc.server 2>&1 | grep -i "p2p\|peer\|gossip"
```

Look for:
- ✅ `"Kyber handshake successful"`
- ✅ `"Peer connected: 12D3Koo..."`
- ✅ `"Sync: received 100 headers"`
- ❌ `"dial failed"` (connectivity issues)
- ❌ `"handshake timeout"` (network/firewall)

## Multi-Node Testing

### Local Test Network

Run 3 nodes locally to test P2P connectivity:

```bash
# Terminal 1: Node A (seed)
python -m p2p.cli.listen \
  --db ~/.animica/nodeA.db \
  --listen tcp://127.0.0.1:41000 \
  --chain-id 1337

# Terminal 2: Node B (connects to A)
export ANIMICA_P2P_SEEDS="tcp://127.0.0.1:41000"
python -m p2p.cli.listen \
  --db ~/.animica/nodeB.db \
  --listen tcp://127.0.0.1:41010 \
  --chain-id 1337

# Terminal 3: Node C (connects to A, discovers B)
export ANIMICA_P2P_SEEDS="tcp://127.0.0.1:41000"
python -m p2p.cli.listen \
  --db ~/.animica/nodeC.db \
  --listen tcp://127.0.0.1:41020 \
  --chain-id 1337
```

Verify connectivity:
```bash
# From another terminal
python -m p2p.cli.peer list --db ~/.animica/nodeA.db
# Should show connections to B and C

python -m p2p.cli.peer list --db ~/.animica/nodeB.db
# Should show connections to A and C (discovered via gossip)
```

### Docker Multi-Node Test

See `docs/MULTI_NODE_DOCKER_SETUP.md` for a complete docker-compose configuration that runs:
- 3 full nodes with P2P
- 1 miner node
- 1 explorer UI
- Automatic peer discovery
- Shared network namespace

## Troubleshooting

### No Peers Connecting

**Symptoms**: `p2p.listPeers` returns empty array

**Solutions**:

1. **Check P2P is enabled**:
   ```bash
   # Should be "true" by default
   echo $ANIMICA_P2P_ENABLE
   ```

2. **Verify seed connectivity**:
   ```bash
   # Test DNS resolution
   dig mainnet.animica.org
   
   # Test TCP connectivity
   nc -zv mainnet.animica.org 30333
   ```

3. **Check firewall**:
   ```bash
   # Test if your node is reachable
   nc -zv <your-public-ip> 30333
   ```

4. **Review logs**:
   ```bash
   # Look for dial errors
   grep -i "dial\|seed\|p2p" ~/.animica/logs/node.log
   ```

### Slow Sync

**Symptoms**: Blockchain height not increasing

**Solutions**:

1. **Check peer quality**:
   ```bash
   animica peer list | jq '.[] | select(.latencyMs > 500)'
   # Remove slow peers
   ```

2. **Increase peer count**:
   ```bash
   export ANIMICA_P2P_MAX_PEERS=100
   ```

3. **Add fast peers manually**:
   ```bash
   animica peer add /ip4/<known-fast-node>/tcp/30333
   ```

### High Bandwidth Usage

**Symptoms**: Network traffic exceeds expectations

**Solutions**:

1. **Reduce peer count**:
   ```bash
   export ANIMICA_P2P_MAX_PEERS=16
   ```

2. **Disable gossip topics**:
   ```bash
   # Only sync, don't relay (light node behavior)
   export ANIMICA_P2P_GOSSIP_RELAY=false
   ```

3. **Rate limit outbound**:
   ```bash
   export ANIMICA_P2P_RATE_LIMIT_OUT=1000000  # bytes/sec
   ```

### Connection Timeouts

**Symptoms**: Peers connecting then immediately disconnecting

**Solutions**:

1. **Check NAT/UPnP**:
   ```bash
   export ANIMICA_P2P_NAT_UPNP=true
   export ANIMICA_P2P_NAT_PMP=true
   ```

2. **Set external IP explicitly**:
   ```bash
   export ANIMICA_P2P_EXTERNAL_IP=<your-public-ip>
   ```

3. **Use STUN for NAT traversal**:
   ```bash
   export ANIMICA_P2P_STUN="stun.l.google.com:19302"
   ```

### Fork/Chain Reorg Issues

**Symptoms**: Node on different chain than network

**Solutions**:

1. **Verify chain ID matches network**:
   ```bash
   animica node status | grep chainId
   # Mainnet: 1, Testnet: 2, Devnet: 1337
   ```

2. **Re-sync from trusted checkpoint**:
   ```bash
   # Export state at safe height
   animica chain export --height 1000000 > checkpoint.json
   
   # Clear and reimport
   rm -rf ~/.animica/chain-1/
   animica chain import < checkpoint.json
   ```

3. **Add trusted peers**:
   ```bash
   export ANIMICA_P2P_SEEDS="/dns4/mainnet.animica.org/tcp/30333"
   ```

## Security Considerations

### Post-Quantum Cryptography

All P2P connections use quantum-resistant algorithms:

- **Key Exchange**: Kyber-768 (NIST PQC finalist)
- **Authentication**: Dilithium3 (ML-DSA-65) or SPHINCS+
- **Encryption**: ChaCha20-Poly1305 (AEAD cipher)

This protects against:
- ✅ Future quantum computer attacks on Diffie-Hellman
- ✅ Harvest-now-decrypt-later scenarios
- ✅ Signature forgery

### DoS Protection

Built-in protections against network attacks:

1. **Rate Limiting**: Token bucket per peer and topic
2. **Connection Limits**: Max peers, max streams per peer
3. **Early Validation**: Drop invalid messages before processing
4. **Bans**: Temporary and permanent peer bans for malicious behavior
5. **Proof-of-Work**: Handshake requires computational cost

### Private Networks

For permissioned/private deployments:

```bash
# Disable public discovery
export ANIMICA_P2P_PRIVATE_NETWORK=true

# Only connect to explicit seeds
export ANIMICA_P2P_SEEDS="/ip4/10.0.0.1/tcp/30333,/ip4/10.0.0.2/tcp/30333"

# Use custom network key (shared secret)
export ANIMICA_P2P_NETWORK_KEY="<hex-secret>"
```

## Advanced Topics

### Custom Peer Discovery

Implement your own peer discovery service:

```python
# my_discovery.py
from p2p.discovery import SeedEndpoint

async def discover_peers(chain_id: int) -> list[SeedEndpoint]:
    """Fetch peer list from custom source (database, API, etc.)"""
    # Your logic here
    return [
        SeedEndpoint(address="/ip4/1.2.3.4/tcp/30333", priority=1.0),
        SeedEndpoint(address="/dns4/node.mynetwork.com/quic/443", priority=0.9),
    ]

# Register with P2P service
from p2p.discovery import register_discovery_plugin
register_discovery_plugin("custom", discover_peers)
```

### Consensus Participation

Run a mining node to participate in block production:

```bash
# Enable mining
export ANIMICA_MINING_ENABLE=true
export ANIMICA_MINING_ADDRESS=<your-anim1-address>

# Choose proof types
export ANIMICA_MINING_HASH_WORK=true      # CPU/GPU mining
export ANIMICA_MINING_AI_PROOFS=true      # Submit AI computations
export ANIMICA_MINING_QUANTUM_PROOFS=true # Quantum circuit results

# Start node with miner
python -m rpc.server --enable-miner
```

Mined blocks are automatically broadcast to peers via P2P gossip.

### Bridge Nodes

Run nodes that bridge different networks:

```bash
# Node bridges mainnet ↔ testnet for cross-chain transfers
python -m bridge.service \
  --chain-a mainnet \
  --chain-b testnet \
  --mode relay
```

See `docs/BRIDGE_SETUP.md` for details.

## FAQ

**Q: Do I need to run a node to use Animica?**  
A: No, wallets can connect to public RPC nodes like `rpc.animica.org`. But running your own node provides better privacy and helps decentralize the network.

**Q: How much bandwidth does a node use?**  
A: Typical usage: 5-10 GB/month for light sync, 50-100 GB/month for full archival node.

**Q: Can I run a node on a home connection?**  
A: Yes! Make sure ports 30333 (TCP) and 443 (UDP) are forwarded in your router.

**Q: What happens if all seed nodes go down?**  
A: Nodes remember previous peers and will reconnect. The Kademlia DHT also provides decentralized discovery.

**Q: Is P2P required for local development?**  
A: No, you can disable it with `ANIMICA_P2P_ENABLE=false` for single-node testing.

**Q: How do I become a seed node?**  
A: Run a reliable public node and submit a PR to add your address to `ops/seeds/`.

**Q: Can mobile apps run P2P nodes?**  
A: Lightweight sync is possible on mobile. Full nodes are best on desktop/server due to resource requirements.

**Q: Does P2P work behind CGNAT?**  
A: Yes, using STUN/TURN for NAT traversal. Some carriers may block P2P entirely.

## Resources

- **P2P Module README**: `/p2p/README.md` - Technical implementation details
- **Protocol Specs**: `/p2p/specs/` - Wire protocol, handshake, gossip
- **Consensus Docs**: `/consensus/README.md` - PoIES algorithm
- **Test Suite**: `/p2p/tests/` - Example code and test scenarios
- **Discord**: https://discord.gg/animica - Community support
- **GitHub Issues**: Report P2P bugs and request features

## Contributing

Help improve Animica's P2P network:

1. **Run a seed node**: Provide bootstrap infrastructure
2. **Test edge cases**: NAT, mobile, low bandwidth
3. **Optimize performance**: Propose protocol improvements
4. **Write documentation**: Expand this guide with your learnings
5. **Report bugs**: File issues with detailed network diagnostics

See `CONTRIBUTING.md` for guidelines.

---

**Summary**: Animica is a fully decentralized blockchain with robust P2P networking. Nodes communicate directly without central servers, using quantum-resistant cryptography and efficient gossip protocols. Running your own node strengthens the network and aligns with blockchain principles.
