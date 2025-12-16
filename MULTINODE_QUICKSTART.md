# Multi-Node Quickstart

**Test Animica's decentralized P2P network locally with 3 nodes in under 5 minutes.**

## Prerequisites

- Docker 20.10+
- Docker Compose v2.0+
- 4GB RAM available
- 10GB disk space

## Quick Start

### 1. Start the Network

```bash
# Clone and enter repository
git clone https://github.com/animicaorg/all.git
cd all

# Start 3 nodes + monitoring
docker-compose -f docker-compose.multinode.yml up -d

# Watch logs
docker-compose -f docker-compose.multinode.yml logs -f
```

**Wait 30 seconds for nodes to discover each other.**

### 2. Verify P2P Connectivity

Check that nodes have connected to each other:

```bash
# Node 1 peers (should show node2 and node3)
curl -s http://localhost:8545/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]
}' | jq '.result | length'

# Node 2 peers
curl -s http://localhost:8546/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]
}' | jq '.result | length'

# Node 3 peers
curl -s http://localhost:8547/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]
}' | jq '.result | length'
```

**Expected**: Each node shows 1-2 connected peers.

### 3. Verify Blockchain Sync

Check that all nodes have the same chain height:

```bash
echo "Node 1:" && curl -s http://localhost:8545/rpc -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' | jq '.result.height'

echo "Node 2:" && curl -s http://localhost:8546/rpc -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' | jq '.result.height'

echo "Node 3:" && curl -s http://localhost:8547/rpc -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' | jq '.result.height'
```

**Expected**: All nodes report the same height (or within 1-2 blocks).

### 4. Access Monitoring

- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3001 (login: admin/animica)

### 5. Test P2P Gossip

Send a transaction to node 2 and verify it reaches node 1 and node 3:

```bash
# Create a test wallet (if not exists)
source .venv/bin/activate
animica wallet new test-wallet

# Send transaction via node 2
TX_HASH=$(animica --rpc-url http://localhost:8546/rpc tx send \
  --from test-wallet \
  --to anim1testrecipient00000000000000000000000 \
  --value 0.001 | grep "hash" | awk '{print $2}')

# Wait for gossip (2 seconds)
sleep 2

# Check if node 1 received it
curl -s http://localhost:8545/rpc -H 'content-type: application/json' -d "{
  \"jsonrpc\":\"2.0\",\"id\":1,
  \"method\":\"tx.getTransactionByHash\",
  \"params\":[\"$TX_HASH\"]
}" | jq .

# Check if node 3 received it
curl -s http://localhost:8547/rpc -H 'content-type: application/json' -d "{
  \"jsonrpc\":\"2.0\",\"id\":1,
  \"method\":\"tx.getTransactionByHash\",
  \"params\":[\"$TX_HASH\"]
}" | jq .
```

**Expected**: All nodes return the same transaction.

## What Just Happened?

✅ **Node 1** started and listened for peers  
✅ **Node 2** connected to Node 1 via P2P  
✅ **Node 3** connected to Node 1 and discovered Node 2  
✅ **Gossip protocol** shared blocks and transactions between all nodes  
✅ **Consensus** ensured all nodes agree on the canonical chain  
✅ **No central server** - fully decentralized peer-to-peer network  

## Architecture

```
┌──────────┐         ┌──────────┐         ┌──────────┐
│  Node 1  │◄───────►│  Node 2  │◄───────►│  Node 3  │
│  (Seed)  │         │  (Peer)  │         │  (Peer)  │
└──────────┘         └──────────┘         └──────────┘
     ▲                     ▲                     ▲
     │                     │                     │
     └─────────────────────┴─────────────────────┘
              Gossip Protocol (Blocks/Txs)
```

Each node:
- Maintains its own blockchain database
- Validates blocks independently using PoIES consensus
- Broadcasts new transactions to all peers
- Syncs missing blocks from peers
- **No central authority or coordinator**

## Cleanup

```bash
# Stop all nodes
docker-compose -f docker-compose.multinode.yml down

# Remove data (WARNING: deletes blockchain data)
docker-compose -f docker-compose.multinode.yml down -v
```

## Troubleshooting

### No Peers Connecting

Check logs for dial errors:

```bash
docker-compose -f docker-compose.multinode.yml logs node2 | grep -i "peer\|dial"
```

Verify network connectivity:

```bash
docker exec animica-node2 ping -c 3 node1
docker exec animica-node2 nc -zv node1 30333
```

### Nodes Not Syncing

Check if P2P is enabled:

```bash
docker-compose -f docker-compose.multinode.yml logs node2 | grep "P2P\|p2p"
```

Restart nodes:

```bash
docker-compose -f docker-compose.multinode.yml restart
```

### High Resource Usage

Reduce peer count in `docker-compose.multinode.yml`:

```yaml
- ANIMICA_P2P_MAX_PEERS=10  # Instead of 50
```

## Next Steps

- **Production Deployment**: See `docs/NODE_DEPLOYMENT.md`
- **P2P Configuration**: See `docs/P2P_NETWORKING_GUIDE.md`
- **Advanced Testing**: See `docs/MULTI_NODE_DOCKER_SETUP.md`

## Learn More

- **Why is this decentralized?** Each node operates independently with no central coordinator
- **What's the consensus?** PoIES (Proof-of-Integrated-External-Services) - all nodes validate
- **Can I add more nodes?** Yes! Just add another service in docker-compose.yml
- **Is this production-ready?** This is for testing - see production guides for deployment

---

**Summary**: You just ran a fully decentralized blockchain network where nodes discover each other, sync blocks via P2P gossip, and reach consensus without any central authority. This proves Animica is truly decentralized!
