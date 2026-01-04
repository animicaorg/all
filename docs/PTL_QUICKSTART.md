# PTL Replication Quick Start Guide

## Overview

The Pending Transaction Ledger (PTL) is Animica's transaction propagation system that provides:
- **Reliable delivery** with per-peer acknowledgment tracking
- **Durable storage** of pending transactions in SQLite
- **Pull-based replication** protocol (ANNOUNCE → WANT → PUSH → ACK)
- **Anti-entropy reconciliation** for eventual consistency
- **Rich observability** with detailed replication status

PTL replaces mempool-based propagation and is enabled by default.

## Quick Start

### 1. Enable PTL (Default)

PTL is enabled by default. To explicitly enable or configure:

```bash
# Enable PTL (default)
export ANIMICA_PTL_ENABLE=1
export ANIMICA_TX_SYSTEM=ptl

# Configure PTL parameters (optional)
export ANIMICA_PTL_MIN_PEER_ACKS=2        # Minimum peer acks for quorum (default: 2)
export ANIMICA_PTL_TTL_SECONDS=3600       # Transaction TTL (default: 1 hour)
export ANIMICA_PTL_DB_PATH=/path/to/ptl.db # Custom database path
```

To use legacy mempool instead:

```bash
export ANIMICA_TX_SYSTEM=mempool
```

### 2. Send a Transaction with Replication

Send a transaction and wait for peer acknowledgments:

```bash
# Send and wait for 2 peer acks (30 second timeout)
animica tx send \
  --from anim1... \
  --to anim1... \
  --value 0.1 \
  --min-peers 2 \
  --wait-timeout 30

# Output:
# Transaction Submitted
# Tx Hash: 0xabcd...
# Transaction broadcast successfully
#
# Waiting for 2 peer acknowledgments...
# ✓ Received 2 acknowledgments
# Status: eligible
```

### 3. Check Replication Status

Query detailed replication status for a transaction:

```bash
# Human-readable output
animica tx replicate 0xabcd...

# JSON output for scripting
animica tx replicate 0xabcd... --json
```

**Example output:**

```
Replication Status
TxID: 0xabcd...
Local Status: eligible
Quorum: ✓ 2/2 acknowledgments
Received: Sat Jan 04 23:45:00 2026

Peer Receipts (2)
  ack from peer1 at Sat Jan 04 23:45:01 2026
  ack from peer2 at Sat Jan 04 23:45:02 2026

Persistence: 2 receipts stored (sqlite)
```

### 4. List Pending Transactions

View all pending transactions in the PTL:

```bash
# List all pending transactions
animica tx pending --limit 50

# Filter by status
animica tx pending --status ATTESTED
```

**Example output:**

```
Total: 5

  TxID: 0xabcd...
  Status: ATTESTED
  Acks: 2
  Size: 256 bytes
  Fee: 1000
  Received: Sat Jan 04 23:45:00 2026
```

### 5. Troubleshoot Replication Issues

Diagnose why a transaction isn't replicating:

```bash
animica tx troubleshoot 0xabcd...
```

**Example output:**

```
Troubleshooting Transaction 0xabcd...

Status: STORED

Acknowledgments: 1/2
Insufficient peer acknowledgments
Recommendations:
  1. Check network connectivity: animica p2p peers
  2. Verify peer count is sufficient
  3. Wait for anti-entropy reconciliation (10s interval)
  4. Check debug.ptlPeers for peer state

PTL Stats
Min peer acks required: 2
TTL: 3600s

Transactions by status:
  STORED: 3
  ATTESTED: 2

Connected Peers: 5
  peer1: 10 announced, 5 wanted
  peer2: 8 announced, 3 wanted
  ...
```

## RPC Methods

PTL provides the following RPC methods for programmatic access:

### Canonical PTL Methods

1. **`ptl.replicationStatus`** - Get detailed replication status
   ```json
   {
     "jsonrpc": "2.0",
     "id": 1,
     "method": "ptl.replicationStatus",
     "params": [{"txid": "0xabcd..."}]
   }
   ```

   **Response:**
   ```json
   {
     "tx_hash": "0xabcd...",
     "local_status": "eligible",
     "peers": [
       {
         "peer_id": "peer1",
         "status": "ack",
         "first_seen_ts": 1704410700,
         "last_update_ts": 1704410700
       }
     ],
     "quorum": {
       "required_acks": 2,
       "observed_acks": 2,
       "quorum_met": true
     },
     "persistence": {
       "stored_receipts_count": 2,
       "store_backend": "sqlite"
     },
     "received_at": 1704410700,
     "updated_at": 1704410702
   }
   ```

2. **`tx.submitRawTransaction`** - Submit raw transaction to PTL

3. **`tx.get`** - Get transaction details by ID

4. **`tx.pending`** - List pending transactions

5. **`debug.ptlStats`** - Get PTL statistics

6. **`debug.ptlPeers`** - Get peer replication state

### Backward-Compatible Alias

- **`tx.replicationStatus`** - Alias for `ptl.replicationStatus`

## Transaction Lifecycle

PTL transactions follow this lifecycle:

```
NEW → STORED → ANNOUNCED → REPLICATING → ATTESTED → INCLUDED → FINALIZED
                                            ↓
                                         REJECTED
                                            ↓
                                         EXPIRED
```

**Status descriptions:**
- **NEW**: Just received, not yet stored
- **STORED**: Durably stored in PTL database
- **ANNOUNCED**: Announced to at least one peer
- **REPLICATING**: Being replicated to peers
- **ATTESTED**: Confirmed by minimum required peers (quorum met)
- **INCLUDED**: Included in a block
- **FINALIZED**: Block containing transaction is finalized
- **REJECTED**: Rejected as invalid
- **EXPIRED**: TTL exceeded before inclusion

## Configuration Reference

All configuration is via environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `ANIMICA_PTL_ENABLE` | Enable PTL service | `true` |
| `ANIMICA_TX_SYSTEM` | Transaction system (`ptl` or `mempool`) | `ptl` |
| `ANIMICA_PTL_MIN_PEER_ACKS` | Minimum peer acks for quorum | `2` |
| `ANIMICA_PTL_TTL_SECONDS` | Transaction TTL in seconds | `3600` |
| `ANIMICA_PTL_DB_PATH` | Custom PTL database path | `~/.animica/chain-{id}/ptl/ptl.db` |
| `ANIMICA_PTL_RECONCILE_INTERVAL_S` | Anti-entropy interval | `10.0` |
| `ANIMICA_PTL_ANNOUNCE_BATCH_SIZE` | Announcement batch size | `100` |
| `ANIMICA_PTL_MAX_PUSH_BATCH` | Max push batch size | `50` |

## Receipt Status Values

Peer receipts can have the following status values:

- **`ack`**: Peer acknowledged receipt successfully
- **`reject`**: Peer rejected transaction (e.g., invalid signature, nonce)
- **`timeout`**: Peer did not respond within timeout window

## Best Practices

1. **Wait for quorum**: Use `--min-peers` flag to ensure reliable delivery
   ```bash
   animica tx send --from ... --to ... --value 1 --min-peers 2
   ```

2. **Monitor replication**: Check status periodically for important transactions
   ```bash
   animica tx replicate 0xabcd...
   ```

3. **Set appropriate TTL**: Adjust TTL based on network conditions
   ```bash
   export ANIMICA_PTL_TTL_SECONDS=7200  # 2 hours
   ```

4. **Use JSON output for automation**: Parse JSON output in scripts
   ```bash
   STATUS=$(animica tx replicate 0xabcd... --json | jq -r '.local_status')
   ```

5. **Troubleshoot proactively**: Use troubleshoot command for debugging
   ```bash
   animica tx troubleshoot 0xabcd...
   ```

## Troubleshooting

### Transaction not replicating

1. Check peer count:
   ```bash
   animica p2p peers
   ```

2. Verify PTL is enabled:
   ```bash
   animica rpc call debug.ptlStats '{}'
   ```

3. Check anti-entropy reconciliation (runs every 10s by default)

### Only partial acknowledgments

- Check network connectivity to peers
- Verify peers are synced and accepting transactions
- Wait for anti-entropy reconciliation to complete
- Check peer state: `animica rpc call debug.ptlPeers '{}'`

### PTL not available error

PTL is not initialized. Ensure:
1. Node is running with PTL enabled: `ANIMICA_PTL_ENABLE=1`
2. `ANIMICA_TX_SYSTEM=ptl` (default)
3. Node has been started/restarted after configuration changes

## Migration from Mempool

To migrate from mempool-based propagation to PTL:

1. Update environment variables:
   ```bash
   export ANIMICA_TX_SYSTEM=ptl
   export ANIMICA_PTL_ENABLE=1
   ```

2. Restart node to initialize PTL service

3. Update client code to use new CLI commands or RPC methods

4. Test with `--min-peers` flag to validate replication

## Support

For issues or questions:
- Check logs: `animica node logs`
- File issue: https://github.com/animicaorg/all/issues
- Documentation: `core/ptl/README.md`
