# Pending Transaction Ledger (PTL)

The Pending Transaction Ledger (PTL) is a durable, pull-based transaction replication system that replaces the traditional mempool-based push propagation model in Animica.

## Overview

PTL provides:

- **Durable storage**: All pending transactions are persisted to SQLite with full metadata
- **Status lifecycle**: Transactions progress through well-defined states
- **Pull-based replication**: Nodes announce availability and peers pull what they need
- **Anti-entropy reconciliation**: Periodic reconciliation ensures eventual consistency
- **Per-peer receipts**: Track acknowledgments and rejections from each peer
- **Observability**: Rich RPC endpoints for monitoring replication status

## Architecture

```
┌─────────────┐
│   Client    │
│  (animica)  │
└──────┬──────┘
       │ tx.submitRawTransaction
       v
┌─────────────────────────────────────┐
│           PTL Service               │
│  ┌──────────────────────────────┐  │
│  │  Status: NEW → STORED →      │  │
│  │  ANNOUNCED → REPLICATING →   │  │
│  │  ATTESTED → INCLUDED         │  │
│  └──────────────────────────────┘  │
│  ┌──────────────────────────────┐  │
│  │      SQLite Store            │  │
│  │  - Transactions              │  │
│  │  - Receipts                  │  │
│  │  - Metadata                  │  │
│  └──────────────────────────────┘  │
└──────────┬──────────────────────────┘
           │
           v
    ┌──────────────┐
    │  PTL Relay   │
    │   Service    │
    └──────┬───────┘
           │
           │ P2P Messages:
           │ - PTL_ANNOUNCE
           │ - PTL_WANT
           │ - PTL_PUSH
           │ - PTL_ACK
           │
           v
    ┌──────────────┐
    │  Peer Nodes  │
    └──────────────┘
```

## Status Lifecycle

1. **NEW**: Just received, not yet stored
2. **STORED**: Durably stored in PTL
3. **ANNOUNCED**: Announced to at least one peer
4. **REPLICATING**: Being replicated to peers
5. **ATTESTED**: Confirmed by minimum peers (default: 2)
6. **INCLUDED**: Included in a block
7. **FINALIZED**: Block containing transaction is finalized
8. **REJECTED**: Transaction rejected (invalid signature, nonce, etc.)
9. **EXPIRED**: Transaction expired (TTL exceeded)

## P2P Protocol

### Messages

- **PTL_ANNOUNCE**: Node announces available transactions
- **PTL_WANT**: Node requests specific transactions
- **PTL_PUSH**: Node sends requested transactions
- **PTL_ACK**: Node acknowledges receipt (ack/reject/timeout)

### Reconciliation

- **On connect**: Nodes exchange transaction inventories
- **Every 10s**: Anti-entropy reconciliation loop
- **Bounded**: Batch sizes and rate limits prevent flooding

## RPC Endpoints

### Transaction Submission

```bash
# Submit raw transaction
animica rpc tx.submitRawTransaction '{"tx": "0x..."}'

# Returns:
{
  "txid": "0x...",
  "status": "STORED",
  "received_at": 1234567890.0,
  "expire_at": 1234571490.0
}
```

### Query Transactions

```bash
# Get transaction by ID
animica rpc tx.get '{"txid": "0x..."}'

# List pending transactions
animica rpc tx.pending '{"limit": 100, "status": "ATTESTED"}'

# Get replication status with receipts
animica rpc tx.replicationStatus '{"txid": "0x..."}'
```

### Debug/Observability

```bash
# Get PTL statistics
animica rpc debug.ptlStats '{}'

# Get peer replication state
animica rpc debug.ptlPeers '{}'
```

## CLI Commands

### Submit with Replication Waiting

```bash
# Submit and wait for 2 peer acknowledgments
animica tx send \
  --from anim1... \
  --to anim1... \
  --value 1.0 \
  --min-peers 2 \
  --wait-timeout 30
```

### Check Transaction Status

```bash
# Show current status
animica tx status 0x...

# Show pending transactions
animica tx pending --limit 50

# Show replication receipts
animica tx replicate 0x...
```

### Troubleshooting

```bash
# Diagnose replication issues
animica tx troubleshoot 0x...
```

Output includes:
- Current status
- Peer acknowledgments
- Rejection reasons (if any)
- PTL statistics
- Connected peers

## Configuration

### Environment Variables

```bash
# Select transaction system (ptl or mempool)
export ANIMICA_TX_SYSTEM=ptl  # default

# Replication settings
export ANIMICA_PTL_MIN_PEER_ACKS=2  # min peers for ATTESTED status
export ANIMICA_PTL_TTL_SECONDS=3600  # 1 hour

# Database path (optional, defaults to data dir)
export ANIMICA_PTL_DB_PATH=/path/to/ptl.db

# Reconciliation intervals
export ANIMICA_PTL_RECONCILE_INTERVAL_S=10.0
export ANIMICA_PTL_ANNOUNCE_INTERVAL_S=1.0

# Block building limits
export ANIMICA_PTL_MAX_BLOCK_SIZE=1000000  # 1MB
export ANIMICA_PTL_MAX_BLOCK_GAS=10000000
```

### Code Configuration

```python
from core.ptl.config import PtlConfig

# Load from environment
config = PtlConfig.from_env()

# Or configure explicitly
config = PtlConfig(
    tx_system="ptl",
    min_peer_acks=2,
    ttl_seconds=3600,
)
```

## Integration

### In Node Setup

```python
from core.ptl.store import PtlStore
from core.ptl.service import PtlService
from core.ptl.selection import PtlSelector
from core.ptl.config import PtlConfig
from p2p.ptl_relay import PtlRelayService

# Load config
config = PtlConfig.from_env()

if config.use_ptl():
    # Initialize PTL
    ptl_store = PtlStore(config.db_path or "data/ptl.db")
    ptl_service = PtlService(
        ptl_store,
        ttl_seconds=config.ttl_seconds,
        min_peer_acks=config.min_peer_acks,
    )
    
    # Create selector for mining
    ptl_selector = PtlSelector(
        ptl_service,
        max_block_size=config.max_block_size,
        max_block_gas=config.max_block_gas,
    )
    
    # Create P2P relay
    ptl_relay = PtlRelayService(
        ptl_service,
        reconcile_interval_s=config.reconcile_interval_s,
        # ... P2P callbacks
    )
    
    # Register with RPC deps
    deps.set("ptl_service", ptl_service)
    deps.set("ptl_relay", ptl_relay)
    
    # Start background tasks
    asyncio.create_task(ptl_service.maintenance_loop())
    asyncio.create_task(ptl_relay.reconcile_loop())
```

### In Miner

```python
from core.ptl.miner_adapter import MinerTxAdapter

# Create adapter
tx_adapter = MinerTxAdapter(
    ptl_service=ptl_service,
    ptl_selector=ptl_selector,
    config=config,
)

# Select transactions for block
txs = await tx_adapter.select_for_block(max_txs=1000)

# After block is mined
txids = [tx["txid"] for tx in txs]
await tx_adapter.mark_included(txids, height=block_height)
```

## Testing

### Unit Tests

```bash
pytest core/ptl/tests/ -v
```

### Integration Tests

```bash
# Basic PTL functionality
pytest tests/integration/test_ptl_basic.py -v

# Two-node replication
pytest tests/integration/test_ptl_replication.py -v
```

### Manual Testing

```bash
# Start two nodes
./animica node start --network devnet --data-dir node1 --p2p-port 30301 --rpc-port 8545
./animica node start --network devnet --data-dir node2 --p2p-port 30302 --rpc-port 8546

# Connect nodes
animica p2p connect --peer /ip4/127.0.0.1/tcp/30301

# Submit on node 1
animica tx send --from anim1... --to anim1... --value 1.0 --rpc-url http://127.0.0.1:8545

# Check replication on node 2
animica tx pending --rpc-url http://127.0.0.1:8546

# Should see transaction within 3 seconds
```

## Acceptance Criteria ✓

1. ✅ **Submit on node A available on node B within 3s**
   - Tested in `test_ptl_two_node_replication`
   
2. ✅ **Anti-entropy reconnect within 30s**
   - Tested in `test_ptl_anti_entropy_reconciliation`
   
3. ✅ **Statuses with reasons**
   - Full status lifecycle with reject reasons
   - Tested in `test_ptl_status_lifecycle` and `test_ptl_invalid_transaction_rejection`
   
4. ✅ **CLI replication receipts**
   - `animica tx replicate` shows per-peer receipts
   - `animica tx send --min-peers` waits for acks
   
5. ✅ **Observability endpoints**
   - `debug.ptlStats` for overall statistics
   - `debug.ptlPeers` for per-peer state
   - `tx.replicationStatus` for detailed replication info

## Migration from Mempool

PTL is backward-compatible with existing mempool RPC methods via compatibility shims:

- `mempool.add` → `tx.submitRawTransaction`
- `mempool.get` → `tx.get`
- `mempool.list` → `tx.pending`

To keep using mempool:

```bash
export ANIMICA_TX_SYSTEM=mempool
```

## Performance Characteristics

- **Storage**: SQLite with indexes, ~1KB per transaction + receipts
- **Replication latency**: <3s typical, depends on reconcile interval
- **Throughput**: Limited by network bandwidth and peer count
- **Memory**: O(active transactions + peer state)
- **Disk**: Grows with transaction volume, pruned after finalization

## Security Considerations

- **Receipt verification**: Receipts are peer-reported, not cryptographically signed
- **Sybil resistance**: Min peer acks helps but doesn't prevent Sybil attacks
- **DoS protection**: Rate limiting on P2P messages
- **Privacy**: Transaction content is not encrypted
- **Replay protection**: Handled at transaction validation layer

## Future Enhancements

- Cryptographic receipt signatures
- Bloom filter-based inventory reconciliation
- Sharded PTL for horizontal scaling
- Persistent peer reputation scoring
- Compressed transaction encoding for bandwidth
