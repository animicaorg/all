# Multi-Node Docker Setup for Animica

This guide demonstrates how to run multiple Animica nodes with P2P networking using Docker Compose. This setup simulates a real distributed network on your local machine.

## Overview

We'll create a local test network with:
- **3 Full Nodes** with P2P enabled
- **1 Miner Node** producing blocks
- **1 Explorer UI** for visualization
- **Automatic peer discovery** between nodes
- **Isolated databases** per node
- **Prometheus metrics** for monitoring

## Prerequisites

- Docker 20.10+
- Docker Compose v2.0+
- 8GB RAM available
- 20GB disk space

## Quick Start

### 1. Create Docker Compose Configuration

Create `docker-compose.multinode.yml`:

```yaml
version: '3.8'

networks:
  animica-net:
    driver: bridge
    ipam:
      config:
        - subnet: 172.28.0.0/16

services:
  # ============================================
  # Node 1 - Seed Node (Full Node + RPC)
  # ============================================
  node1:
    image: animica/node:latest
    container_name: animica-node1
    hostname: node1
    networks:
      animica-net:
        ipv4_address: 172.28.0.10
    ports:
      - "8545:8545"      # RPC HTTP
      - "30333:30333"    # P2P TCP
      - "443:443/udp"    # P2P QUIC
      - "9001:9000"      # Metrics
    environment:
      - ANIMICA_NETWORK=devnet
      - ANIMICA_P2P_ENABLE=true
      - ANIMICA_P2P_LISTEN_TCP=0.0.0.0:30333
      - ANIMICA_P2P_LISTEN_QUIC=0.0.0.0:443
      - ANIMICA_P2P_ADVERTISED_ADDRS=/ip4/172.28.0.10/tcp/30333,/ip4/172.28.0.10/udp/443/quic-v1
      - ANIMICA_P2P_MAX_PEERS=50
      - ANIMICA_P2P_MDNS=true
      - RPC_HOST=0.0.0.0
      - RPC_PORT=8545
      - ANIMICA_DB_PATH=/data/node1.db
      - LOG_LEVEL=info
    volumes:
      - node1-data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8545/healthz"]
      interval: 30s
      timeout: 10s
      retries: 3

  # ============================================
  # Node 2 - Full Node (Syncs from Node 1)
  # ============================================
  node2:
    image: animica/node:latest
    container_name: animica-node2
    hostname: node2
    networks:
      animica-net:
        ipv4_address: 172.28.0.11
    ports:
      - "8546:8545"      # RPC HTTP
      - "30334:30333"    # P2P TCP
      - "9002:9000"      # Metrics
    environment:
      - ANIMICA_NETWORK=devnet
      - ANIMICA_P2P_ENABLE=true
      - ANIMICA_P2P_LISTEN_TCP=0.0.0.0:30333
      - ANIMICA_P2P_LISTEN_QUIC=0.0.0.0:443
      - ANIMICA_P2P_ADVERTISED_ADDRS=/ip4/172.28.0.11/tcp/30333
      - ANIMICA_P2P_SEEDS=/ip4/172.28.0.10/tcp/30333
      - ANIMICA_P2P_MAX_PEERS=50
      - ANIMICA_P2P_MDNS=true
      - RPC_HOST=0.0.0.0
      - RPC_PORT=8545
      - ANIMICA_DB_PATH=/data/node2.db
      - LOG_LEVEL=info
    volumes:
      - node2-data:/data
    restart: unless-stopped
    depends_on:
      - node1
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8545/healthz"]
      interval: 30s
      timeout: 10s
      retries: 3

  # ============================================
  # Node 3 - Full Node (Syncs from Node 1)
  # ============================================
  node3:
    image: animica/node:latest
    container_name: animica-node3
    hostname: node3
    networks:
      animica-net:
        ipv4_address: 172.28.0.12
    ports:
      - "8547:8545"      # RPC HTTP
      - "30335:30333"    # P2P TCP
      - "9003:9000"      # Metrics
    environment:
      - ANIMICA_NETWORK=devnet
      - ANIMICA_P2P_ENABLE=true
      - ANIMICA_P2P_LISTEN_TCP=0.0.0.0:30333
      - ANIMICA_P2P_LISTEN_QUIC=0.0.0.0:443
      - ANIMICA_P2P_ADVERTISED_ADDRS=/ip4/172.28.0.12/tcp/30333
      - ANIMICA_P2P_SEEDS=/ip4/172.28.0.10/tcp/30333
      - ANIMICA_P2P_MAX_PEERS=50
      - ANIMICA_P2P_MDNS=true
      - RPC_HOST=0.0.0.0
      - RPC_PORT=8545
      - ANIMICA_DB_PATH=/data/node3.db
      - LOG_LEVEL=info
    volumes:
      - node3-data:/data
    restart: unless-stopped
    depends_on:
      - node1
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8545/healthz"]
      interval: 30s
      timeout: 10s
      retries: 3

  # ============================================
  # Miner - Produces Blocks
  # ============================================
  miner:
    image: animica/miner:latest
    container_name: animica-miner
    hostname: miner
    networks:
      animica-net:
        ipv4_address: 172.28.0.20
    environment:
      - ANIMICA_NETWORK=devnet
      - ANIMICA_RPC_URL=http://node1:8545/rpc
      - ANIMICA_MINING_ADDRESS=anim1devnetminer000000000000000000000000000000
      - ANIMICA_MINING_HASH_WORK=true
      - ANIMICA_MINING_THREADS=2
      - LOG_LEVEL=info
    depends_on:
      - node1
    restart: unless-stopped

  # ============================================
  # Explorer UI
  # ============================================
  explorer:
    image: animica/explorer:latest
    container_name: animica-explorer
    hostname: explorer
    networks:
      animica-net:
        ipv4_address: 172.28.0.30
    ports:
      - "3000:3000"
    environment:
      - REACT_APP_RPC_URL=http://localhost:8545/rpc
      - REACT_APP_NETWORK=devnet
    depends_on:
      - node1
    restart: unless-stopped

  # ============================================
  # Prometheus - Metrics Collection
  # ============================================
  prometheus:
    image: prom/prometheus:latest
    container_name: animica-prometheus
    hostname: prometheus
    networks:
      animica-net:
        ipv4_address: 172.28.0.40
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus-data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
    restart: unless-stopped

  # ============================================
  # Grafana - Metrics Visualization
  # ============================================
  grafana:
    image: grafana/grafana:latest
    container_name: animica-grafana
    hostname: grafana
    networks:
      animica-net:
        ipv4_address: 172.28.0.41
    ports:
      - "3001:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=animica
      - GF_USERS_ALLOW_SIGN_UP=false
    volumes:
      - grafana-data:/var/lib/grafana
    depends_on:
      - prometheus
    restart: unless-stopped

volumes:
  node1-data:
  node2-data:
  node3-data:
  prometheus-data:
  grafana-data:
```

### 2. Create Prometheus Configuration

Create `prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'node1'
    static_configs:
      - targets: ['node1:9000']
        labels:
          instance: 'node1'

  - job_name: 'node2'
    static_configs:
      - targets: ['node2:9000']
        labels:
          instance: 'node2'

  - job_name: 'node3'
    static_configs:
      - targets: ['node3:9000']
        labels:
          instance: 'node3'
```

### 3. Start the Network

```bash
# Start all services
docker-compose -f docker-compose.multinode.yml up -d

# Watch logs from all nodes
docker-compose -f docker-compose.multinode.yml logs -f

# Watch just node1
docker-compose -f docker-compose.multinode.yml logs -f node1
```

### 4. Verify P2P Connectivity

```bash
# Check Node 1 peers
curl -s http://localhost:8545/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]
}' | jq '.result | length'

# Should show 2 peers (node2 and node3)

# Check Node 2 peers
curl -s http://localhost:8546/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]
}' | jq '.result | length'

# Should show at least 1 peer (node1, possibly node3)

# List all peers with details
curl -s http://localhost:8545/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]
}' | jq '.result'
```

Expected output:
```json
[
  {
    "id": "12D3KooWNode2...",
    "addr": "/ip4/172.28.0.11/tcp/30333",
    "status": "connected",
    "direction": "inbound",
    "latencyMs": 2.5
  },
  {
    "id": "12D3KooWNode3...",
    "addr": "/ip4/172.28.0.12/tcp/30333",
    "status": "connected",
    "direction": "inbound",
    "latencyMs": 3.1
  }
]
```

### 5. Verify Blockchain Sync

```bash
# Check heights on all nodes
echo "Node 1:" && curl -s http://localhost:8545/rpc -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' | jq '.result.height'

echo "Node 2:" && curl -s http://localhost:8546/rpc -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' | jq '.result.height'

echo "Node 3:" && curl -s http://localhost:8547/rpc -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' | jq '.result.height'

# All nodes should have the same (or very close) height
```

### 6. Test Transaction Broadcast

Send a transaction to Node 2 and verify it reaches Node 1 and Node 3:

```bash
# Send transaction to Node 2
TX_HASH=$(curl -s http://localhost:8546/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,
  "method":"tx.sendRawTransaction",
  "params":["0x<your-signed-tx-hex>"]
}' | jq -r '.result')

# Wait a moment for gossip
sleep 2

# Check if Node 1 received it
curl -s http://localhost:8545/rpc -H 'content-type: application/json' -d "{
  \"jsonrpc\":\"2.0\",\"id\":1,
  \"method\":\"tx.getTransactionByHash\",
  \"params\":[\"$TX_HASH\"]
}" | jq .

# Check if Node 3 received it
curl -s http://localhost:8547/rpc -H 'content-type: application/json' -d "{
  \"jsonrpc\":\"2.0\",\"id\":1,
  \"method\":\"tx.getTransactionByHash\",
  \"params\":[\"$TX_HASH\"]
}" | jq .
```

## Monitoring

### Access Services

- **Node 1 RPC**: http://localhost:8545
- **Node 2 RPC**: http://localhost:8546
- **Node 3 RPC**: http://localhost:8547
- **Explorer**: http://localhost:3000
- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3001 (admin/animica)

### View P2P Metrics in Prometheus

1. Open http://localhost:9090
2. Query examples:
   ```promql
   # Number of connected peers per node
   p2p_peers{state="connected"}
   
   # Messages received per topic
   rate(p2p_msgs_total[1m])
   
   # Bandwidth usage
   rate(p2p_bytes_total[1m])
   
   # Peer latency
   p2p_rtt_seconds
   ```

### Create Grafana Dashboard

1. Open http://localhost:3001 (login: admin/animica)
2. Add Prometheus data source: http://prometheus:9090
3. Import dashboard from `ops/grafana/p2p-dashboard.json`

Or create custom panels:

**Peer Count Panel**:
```promql
p2p_peers{state="connected"}
```

**Network Throughput Panel**:
```promql
rate(p2p_bytes_total{dir="rx"}[1m])
```

**Block Propagation Time**:
```promql
histogram_quantile(0.95, rate(p2p_block_propagation_seconds_bucket[5m]))
```

## Testing Scenarios

### Scenario 1: Node Failure Recovery

Test network resilience when a node goes down:

```bash
# Stop Node 2
docker stop animica-node2

# Wait 30 seconds

# Verify Node 1 and Node 3 are still connected
curl -s http://localhost:8545/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]
}' | jq '.result | length'

# Restart Node 2
docker start animica-node2

# Wait for reconnection
sleep 10

# Verify Node 2 rejoined
curl -s http://localhost:8546/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]
}' | jq '.result | length'

# Verify sync continued
curl -s http://localhost:8546/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]
}' | jq '.result.height'
```

### Scenario 2: Network Partition

Simulate a network split:

```bash
# Disconnect Node 3 from network
docker network disconnect animica-net animica-node3

# Wait 60 seconds, check Node 1 and Node 2 can still operate
curl -s http://localhost:8545/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]
}' | jq '.result.height'

# Reconnect Node 3
docker network connect animica-net animica-node3

# Node 3 should re-sync and catch up
sleep 30
curl -s http://localhost:8547/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]
}' | jq '.result.height'
```

### Scenario 3: Adding a New Node

Add a 4th node to the running network:

```bash
# Start new node container
docker run -d \
  --name animica-node4 \
  --network animica-net \
  --ip 172.28.0.13 \
  -p 8548:8545 \
  -p 30336:30333 \
  -e ANIMICA_NETWORK=devnet \
  -e ANIMICA_P2P_ENABLE=true \
  -e ANIMICA_P2P_SEEDS=/ip4/172.28.0.10/tcp/30333 \
  -e ANIMICA_P2P_MAX_PEERS=50 \
  -e RPC_HOST=0.0.0.0 \
  -e RPC_PORT=8545 \
  animica/node:latest

# Wait for connection
sleep 10

# Verify Node 4 connected
curl -s http://localhost:8548/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]
}' | jq '.result | length'

# Verify Node 1 sees Node 4
curl -s http://localhost:8545/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]
}' | jq '.result | length'

# Should be 3 peers now (node2, node3, node4)
```

## Cleanup

```bash
# Stop all services
docker-compose -f docker-compose.multinode.yml down

# Remove volumes (WARNING: deletes blockchain data)
docker-compose -f docker-compose.multinode.yml down -v

# Remove images
docker rmi animica/node:latest animica/miner:latest animica/explorer:latest
```

## Advanced Configurations

### Adding More Nodes

To scale to 10 nodes, use a script:

```bash
#!/bin/bash
# generate-nodes.sh

NUM_NODES=10
BASE_PORT=8545
BASE_P2P_PORT=30333
BASE_METRICS_PORT=9000

for i in $(seq 1 $NUM_NODES); do
  RPC_PORT=$((BASE_PORT + i - 1))
  P2P_PORT=$((BASE_P2P_PORT + i - 1))
  METRICS_PORT=$((BASE_METRICS_PORT + i))
  IP="172.28.0.$((10 + i - 1))"
  
  echo "  node${i}:"
  echo "    image: animica/node:latest"
  echo "    container_name: animica-node${i}"
  echo "    networks:"
  echo "      animica-net:"
  echo "        ipv4_address: ${IP}"
  echo "    ports:"
  echo "      - \"${RPC_PORT}:8545\""
  echo "      - \"${P2P_PORT}:30333\""
  echo "      - \"${METRICS_PORT}:9000\""
  echo "    environment:"
  echo "      - ANIMICA_NETWORK=devnet"
  echo "      - ANIMICA_P2P_ENABLE=true"
  echo "      - ANIMICA_P2P_SEEDS=/ip4/172.28.0.10/tcp/30333"
  echo "    volumes:"
  echo "      - node${i}-data:/data"
  echo ""
done
```

### Geographic Distribution

Simulate nodes in different regions with latency:

```bash
# Use tc (traffic control) to add latency
docker exec animica-node2 tc qdisc add dev eth0 root netem delay 50ms
docker exec animica-node3 tc qdisc add dev eth0 root netem delay 100ms

# Verify latency in peer list
curl -s http://localhost:8545/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]
}' | jq '.result[].latencyMs'
```

### Private Network

Create an isolated private network:

```yaml
# Add to each node's environment
- ANIMICA_P2P_PRIVATE_NETWORK=true
- ANIMICA_P2P_NETWORK_KEY=0xdeadbeef...  # Shared secret
```

## Troubleshooting

### Nodes Not Connecting

**Check logs**:
```bash
docker logs animica-node2 | grep -i "peer\|p2p\|dial"
```

**Verify network**:
```bash
docker network inspect animica-net
```

**Test connectivity**:
```bash
docker exec animica-node2 nc -zv node1 30333
```

### Slow Sync

**Increase peer count**:
```yaml
- ANIMICA_P2P_MAX_PEERS=100
```

**Add more seed nodes**:
```yaml
- ANIMICA_P2P_SEEDS=/ip4/172.28.0.10/tcp/30333,/ip4/172.28.0.11/tcp/30333
```

### High Resource Usage

**Reduce peer count**:
```yaml
- ANIMICA_P2P_MAX_PEERS=10
```

**Limit CPU**:
```yaml
deploy:
  resources:
    limits:
      cpus: '1.0'
      memory: 2G
```

## Conclusion

This multi-node setup demonstrates that **Animica is fully decentralized** with:

✅ Nodes discovering and connecting to each other via P2P  
✅ Blockchain syncing through peer gossip (no central server)  
✅ Transactions broadcasting across the network  
✅ Consensus achieved through independent validation  
✅ Network resilience to node failures  

The `rpc.animica.org` endpoint is simply **one node among many**, not a central authority. Users can run their own nodes and participate in the decentralized network.

## Further Reading

- **P2P Networking Guide**: `/docs/P2P_NETWORKING_GUIDE.md`
- **Node Deployment**: `/docs/NODE_DEPLOYMENT.md`
- **Consensus Algorithm**: `/consensus/README.md`
- **Production Setup**: `/ops/README.md`
