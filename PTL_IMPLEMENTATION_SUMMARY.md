# PR Summary: Pending Transaction Ledger (PTL) Implementation

## Overview

This PR implements a comprehensive Pending Transaction Ledger (PTL) system to replace mempool-based transaction propagation with a durable, pull-based replication protocol. The PTL provides reliable transaction delivery, per-peer acknowledgment tracking, anti-entropy reconciliation, and rich observability.

## Files Changed

### Core PTL Subsystem (`core/ptl/`)
- **`__init__.py`** - Package initialization with public API exports
- **`model.py`** - Data models for transaction status lifecycle and replication receipts
- **`store.py`** - SQLite-based durable storage for transactions and receipts
- **`service.py`** - High-level service API for transaction submission and status management
- **`selection.py`** - Transaction selection for block building (fee/size/age prioritization)
- **`metrics.py`** - Metrics collection for monitoring
- **`config.py`** - Environment-based configuration system
- **`miner_adapter.py`** - Adapter for miner integration with PTL/mempool toggle
- **`README.md`** - Comprehensive documentation with architecture, API, and usage

### P2P Protocol (`p2p/`)
- **`messages_ptl.py`** - PTL-specific P2P messages (ANNOUNCE, WANT, PUSH, ACK)
- **`ptl_relay.py`** - P2P relay service with reconciliation and anti-entropy
- **`wire/message_ids.py`** (modified) - Added PTL message type IDs (0x0409-0x040C)

### RPC Endpoints (`rpc/methods/`)
- **`ptl.py`** - New RPC methods:
  - `tx.submitRawTransaction` - Submit raw transaction to PTL
  - `tx.get` - Get transaction by ID
  - `tx.pending` - List pending transactions
  - `tx.replicationStatus` - Get detailed replication status with receipts
  - `debug.ptlStats` - Get PTL statistics
  - `debug.ptlPeers` - Get peer replication state
  - Compatibility shims for old mempool RPC

### CLI Commands (`python/animica/cli/`)
- **`tx.py`** (modified) - Enhanced transaction commands:
  - `tx send` - Added `--min-peers` and `--wait-timeout` flags for replication waiting
  - `tx pending` - NEW: List pending transactions from PTL
  - `tx replicate` - NEW: Show replication status with per-peer receipts
  - `tx troubleshoot` - NEW: Diagnose replication issues

### Tests (`tests/integration/`)
- **`test_ptl_basic.py`** - Core PTL functionality tests:
  - Transaction submission and retrieval
  - Status lifecycle transitions
  - Receipt tracking
  - Expiration and rejection
  - Statistics
  - Duplicate handling
  
- **`test_ptl_replication.py`** - Multi-node replication tests:
  - Two-node replication within 3 seconds
  - Anti-entropy reconciliation within 30 seconds
  - Invalid transaction rejection with receipts

## PTL Architecture

```
Client (animica CLI)
       ↓
   RPC Server
       ↓
  PTL Service ←→ PTL Store (SQLite)
       ↓
  PTL Relay Service
       ↓
  P2P Network (PTL_* messages)
       ↓
  Remote Peers
```

### Status Lifecycle

```
NEW → STORED → ANNOUNCED → REPLICATING → ATTESTED → INCLUDED → FINALIZED
                                            ↓
                                         REJECTED
                                            ↓
                                         EXPIRED
```

## Key Features

### 1. Durable Storage
- All transactions persisted to SQLite
- Full metadata tracking (origin, timestamps, fees, receipts)
- Indexed for efficient queries
- Automatic pruning of old terminal transactions

### 2. Pull-Based Replication
- **PTL_ANNOUNCE**: Nodes announce available transactions
- **PTL_WANT**: Peers request specific transactions
- **PTL_PUSH**: Nodes send requested transactions
- **PTL_ACK**: Peers acknowledge receipt (ack/reject/timeout)

### 3. Anti-Entropy Reconciliation
- On peer connect: Exchange full inventories
- Every 10 seconds: Periodic reconciliation
- Ensures eventual consistency even after network partitions

### 4. Per-Peer Receipts
- Track acknowledgments from each peer
- Record rejection reasons for debugging
- Automatic ATTESTED status when min peers reached

### 5. Rich Observability
- Transaction status queries
- Replication status with full receipt history
- System-wide statistics
- Per-peer replication state
- CLI troubleshooting tools

## Configuration

### Environment Variables

```bash
# Transaction system selection
export ANIMICA_TX_SYSTEM=ptl  # or "mempool" for legacy

# Replication settings
export ANIMICA_PTL_MIN_PEER_ACKS=2
export ANIMICA_PTL_TTL_SECONDS=3600

# Reconciliation
export ANIMICA_PTL_RECONCILE_INTERVAL_S=10.0

# Block building limits
export ANIMICA_PTL_MAX_BLOCK_SIZE=1000000
export ANIMICA_PTL_MAX_BLOCK_GAS=10000000
```

### Backward Compatibility

PTL includes compatibility shims for old mempool RPC:
- `mempool.add` → `tx.submitRawTransaction`
- `mempool.get` → `tx.get`
- `mempool.list` → `tx.pending`

Set `ANIMICA_TX_SYSTEM=mempool` to use legacy mempool.

## API Examples

### Submit Transaction with Replication Waiting

```bash
animica tx send \
  --from anim1abc... \
  --to anim1xyz... \
  --value 10.5 \
  --min-peers 2 \
  --wait-timeout 30
```

Output:
```
=== Transaction Sent ===
Tx Hash: 0x1234...
Waiting for 2 peer acknowledgments...
✓ Received 2 acknowledgments
Status: ATTESTED
```

### Check Replication Status

```bash
animica tx replicate 0x1234...
```

Output:
```
Replication Status
TxID: 0x1234...
Status: ATTESTED
Acks: 3/2

Receipts (3)
  ✓ ack from peer_a at Sat Jan  4 22:30:15 2026
  ✓ ack from peer_b at Sat Jan  4 22:30:16 2026
  ✓ ack from peer_c at Sat Jan  4 22:30:17 2026
```

### List Pending Transactions

```bash
animica tx pending --limit 10 --status ATTESTED
```

### Troubleshoot Replication

```bash
animica tx troubleshoot 0x1234...
```

Output includes:
- Transaction status
- Acknowledgment counts vs. minimum required
- Rejection reasons if any
- PTL statistics
- Connected peer information
- Actionable recommendations

## RPC API

### Submit Transaction

```json
{
  "method": "tx.submitRawTransaction",
  "params": [{"tx": "0x..."}]
}
```

Response:
```json
{
  "txid": "0x...",
  "status": "STORED",
  "received_at": 1234567890.0,
  "expire_at": 1234571490.0
}
```

### Get Replication Status

```json
{
  "method": "tx.replicationStatus",
  "params": [{"txid": "0x..."}]
}
```

Response:
```json
{
  "txid": "0x...",
  "status": "ATTESTED",
  "ack_count": 3,
  "min_peer_acks": 2,
  "receipts": [
    {
      "peer_id": "peer_a",
      "timestamp": 1234567890.0,
      "status": "ack",
      "reason": "received"
    }
  ]
}
```

## Manual Verification Steps

### Step 1: Start Two Nodes

```bash
# Terminal 1 - Node A
./animica node start \
  --network devnet \
  --data-dir /tmp/node_a \
  --p2p-port 30301 \
  --rpc-port 8545

# Terminal 2 - Node B
./animica node start \
  --network devnet \
  --data-dir /tmp/node_b \
  --p2p-port 30302 \
  --rpc-port 8546
```

### Step 2: Connect Nodes

```bash
# From Node B, connect to Node A
animica p2p connect \
  --peer /ip4/127.0.0.1/tcp/30301 \
  --rpc-url http://127.0.0.1:8546
```

### Step 3: Submit Transaction on Node A

```bash
animica tx send \
  --from anim1... \
  --to anim1... \
  --value 1.0 \
  --min-peers 1 \
  --wait-timeout 30 \
  --rpc-url http://127.0.0.1:8545
```

Expected: Transaction submitted and ack received within 3 seconds

### Step 4: Verify Replication on Node B

```bash
# Check pending transactions on Node B
animica tx pending --rpc-url http://127.0.0.1:8546
```

Expected: Transaction appears within 3 seconds

### Step 5: Check Replication Details

```bash
# On Node A, check replication status
animica tx replicate <txid> --rpc-url http://127.0.0.1:8545
```

Expected: Shows receipt from Node B with "ack" status

### Step 6: Test Anti-Entropy

```bash
# Disconnect Node B
# (kill and restart without connecting)

# Submit transaction on Node A while B is disconnected
animica tx send --from ... --to ... --value 1.0 \
  --rpc-url http://127.0.0.1:8545

# Reconnect Node B
animica p2p connect --peer /ip4/127.0.0.1/tcp/30301 \
  --rpc-url http://127.0.0.1:8546

# Wait up to 30 seconds and check Node B
animica tx pending --rpc-url http://127.0.0.1:8546
```

Expected: Transaction appears within 30 seconds via anti-entropy

### Step 7: Test Observability

```bash
# Check PTL stats
animica rpc debug.ptlStats '{}' --rpc-url http://127.0.0.1:8545

# Check peer state
animica rpc debug.ptlPeers '{}' --rpc-url http://127.0.0.1:8545
```

Expected: Shows transaction counts by status and peer replication state

## Acceptance Criteria ✅

| Criteria | Status | Evidence |
|----------|--------|----------|
| Submit on node A available on node B within 3s | ✅ | `test_ptl_two_node_replication` |
| Anti-entropy reconnect within 30s | ✅ | `test_ptl_anti_entropy_reconciliation` |
| Statuses with reasons | ✅ | Full lifecycle + `test_ptl_invalid_transaction_rejection` |
| CLI replication receipts | ✅ | `animica tx replicate` command |
| Observability endpoints | ✅ | `debug.ptlStats`, `debug.ptlPeers` RPC methods |

## Migration from Mempool

### For Node Operators

No action required - PTL is enabled by default. To keep using mempool:

```bash
export ANIMICA_TX_SYSTEM=mempool
```

### For Client Applications

Update to use new RPC methods:
- Old: `mempool.add` → New: `tx.submitRawTransaction`
- Old: `mempool.get` → New: `tx.get`
- Old: `mempool.list` → New: `tx.pending`

Compatibility shims are provided for existing applications.

### For Wallet Developers

Use `--min-peers` flag in CLI or check `tx.replicationStatus` RPC to wait for peer acknowledgments before showing "confirmed" status.

## Performance Characteristics

- **Replication latency**: <3s typical (depends on reconcile interval)
- **Storage overhead**: ~1KB per transaction + receipts
- **Network overhead**: Announce batches every 1s, reconcile every 10s
- **Memory usage**: O(active transactions + peer state)
- **Disk growth**: Pruned hourly for terminal transactions

## Security Considerations

- Receipt verification relies on peer honesty (not cryptographically signed)
- Min peer acks help but don't prevent Sybil attacks
- Rate limiting prevents DoS on P2P messages
- Transaction content not encrypted (network-level encryption recommended)

## Future Enhancements

- [ ] Cryptographic receipt signatures
- [ ] Bloom filter-based inventory reconciliation
- [ ] Sharded PTL for horizontal scaling
- [ ] Persistent peer reputation scoring
- [ ] Compressed transaction encoding

## Summary

This PR delivers a production-ready PTL system that:

1. ✅ Provides durable, queryable transaction storage
2. ✅ Implements pull-based replication with anti-entropy
3. ✅ Tracks per-peer receipts with detailed status
4. ✅ Offers rich CLI commands for debugging
5. ✅ Exposes comprehensive RPC endpoints
6. ✅ Maintains backward compatibility with mempool
7. ✅ Includes integration tests validating all acceptance criteria
8. ✅ Provides clear documentation and migration guide

The PTL system is ready for testing and deployment.
