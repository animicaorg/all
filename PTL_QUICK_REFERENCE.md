# PTL Quick Reference Guide

## For Users

### Submit Transaction with Replication Waiting
```bash
animica tx send \
  --from anim1abc... \
  --to anim1xyz... \
  --value 10.5 \
  --min-peers 2 \
  --wait-timeout 30
```

### Check Transaction Status
```bash
animica tx status 0x1234...
```

### View Replication Receipts
```bash
animica tx replicate 0x1234...
```

### List Pending Transactions
```bash
animica tx pending --limit 50 --status ATTESTED
```

### Troubleshoot Issues
```bash
animica tx troubleshoot 0x1234...
```

## For Developers

### Initialize PTL
```python
from core.ptl.config import PtlConfig
from core.ptl.store import PtlStore
from core.ptl.service import PtlService

config = PtlConfig.from_env()
store = PtlStore(config.db_path or "data/ptl.db")
service = PtlService(store, 
    ttl_seconds=config.ttl_seconds,
    min_peer_acks=config.min_peer_acks
)
```

### Submit Transaction
```python
tx_bytes = b"..."  # CBOR-encoded transaction
txid, entry = await service.submit(tx_bytes, origin="my_app")
print(f"Submitted: {txid.hex()}, status: {entry.status}")
```

### Query Transaction
```python
entry = await service.get(txid)
if entry:
    print(f"Status: {entry.status}")
    print(f"Acks: {entry.ack_count()}/{service.min_peer_acks}")
```

### Check Replication Status
```python
status = await service.get_replication_status(txid)
for receipt in status["receipts"]:
    print(f"{receipt['peer_id']}: {receipt['status']}")
```

### Select for Mining
```python
from core.ptl.selection import PtlSelector

selector = PtlSelector(service)
txs = await selector.select_for_block(max_txs=1000)
```

### Mark as Included
```python
await service.mark_included(txid, height=12345)
```

## For Node Operators

### Configuration
```bash
# Enable PTL (default)
export ANIMICA_TX_SYSTEM=ptl

# Set minimum peer acknowledgments
export ANIMICA_PTL_MIN_PEER_ACKS=2

# Set transaction TTL
export ANIMICA_PTL_TTL_SECONDS=3600

# Custom database path
export ANIMICA_PTL_DB_PATH=/data/ptl.db
```

### Monitoring
```bash
# Get PTL statistics
animica rpc debug.ptlStats '{}'

# Get peer state
animica rpc debug.ptlPeers '{}'

# List pending by status
animica rpc tx.pending '{"status": "ATTESTED", "limit": 100}'
```

### Disable PTL (use legacy mempool)
```bash
export ANIMICA_TX_SYSTEM=mempool
```

## Status Values

| Status | Description |
|--------|-------------|
| NEW | Just received, not yet stored |
| STORED | Durably stored in PTL |
| ANNOUNCED | Announced to at least one peer |
| REPLICATING | Being replicated to peers |
| ATTESTED | Confirmed by minimum peers |
| INCLUDED | Included in a block |
| FINALIZED | Block finalized |
| REJECTED | Invalid (bad signature, nonce, etc.) |
| EXPIRED | TTL exceeded |

## P2P Messages

| Message | Direction | Purpose |
|---------|-----------|---------|
| PTL_ANNOUNCE | Broadcast | Announce available transactions |
| PTL_WANT | Request | Request specific transactions |
| PTL_PUSH | Response | Send requested transactions |
| PTL_ACK | Response | Acknowledge receipt |

## RPC Methods

### tx.submitRawTransaction
Submit a raw transaction to PTL.
```json
{
  "method": "tx.submitRawTransaction",
  "params": [{"tx": "0x...", "origin": "my_app"}]
}
```

### tx.get
Get transaction by ID.
```json
{
  "method": "tx.get",
  "params": [{"txid": "0x..."}]
}
```

### tx.pending
List pending transactions.
```json
{
  "method": "tx.pending",
  "params": [{"limit": 100, "status": "ATTESTED"}]
}
```

### tx.replicationStatus
Get detailed replication status.
```json
{
  "method": "tx.replicationStatus",
  "params": [{"txid": "0x..."}]
}
```

### debug.ptlStats
Get PTL statistics.
```json
{
  "method": "debug.ptlStats",
  "params": [{}]
}
```

### debug.ptlPeers
Get peer replication state.
```json
{
  "method": "debug.ptlPeers",
  "params": [{}]
}
```

## Troubleshooting

### Transaction not replicating
```bash
# Check peer connections
animica p2p peers

# Check PTL peer state
animica rpc debug.ptlPeers '{}'

# Troubleshoot specific transaction
animica tx troubleshoot 0x...
```

### Insufficient acknowledgments
- Ensure min 2 peers connected
- Check network connectivity
- Wait for reconciliation (10s interval)
- Verify transaction is valid

### Transaction rejected
```bash
# Check rejection reason
animica tx replicate 0x...

# Common causes:
# - Invalid signature
# - Nonce mismatch
# - Insufficient balance
# - Validity window expired
```

## Database Queries

```sql
-- Count transactions by status
SELECT status, COUNT(*) FROM ptl_transactions GROUP BY status;

-- Find expired transactions
SELECT txid, expire_at FROM ptl_transactions 
WHERE expire_at < unixepoch() AND status NOT IN ('INCLUDED', 'FINALIZED', 'REJECTED');

-- Check replication receipts
SELECT txid, peer_id, status, reason, timestamp 
FROM ptl_receipts 
WHERE txid = x'...';

-- Find high-fee transactions
SELECT txid, fee, size FROM ptl_transactions 
WHERE status = 'ATTESTED' 
ORDER BY fee DESC LIMIT 10;
```

## Performance Tuning

### Reconciliation Interval
```bash
# Faster reconciliation (more network traffic)
export ANIMICA_PTL_RECONCILE_INTERVAL_S=5.0

# Slower reconciliation (less traffic)
export ANIMICA_PTL_RECONCILE_INTERVAL_S=30.0
```

### Block Building Limits
```bash
# Larger blocks
export ANIMICA_PTL_MAX_BLOCK_SIZE=5000000  # 5MB
export ANIMICA_PTL_MAX_BLOCK_GAS=50000000

# Smaller blocks
export ANIMICA_PTL_MAX_BLOCK_SIZE=500000   # 500KB
export ANIMICA_PTL_MAX_BLOCK_GAS=5000000
```

### TTL
```bash
# Longer TTL (more memory/disk)
export ANIMICA_PTL_TTL_SECONDS=7200  # 2 hours

# Shorter TTL (less memory/disk)
export ANIMICA_PTL_TTL_SECONDS=1800  # 30 minutes
```

## Common Patterns

### Wait for Attestation
```python
import time

async def wait_for_attestation(service, txid, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        status = await service.get_replication_status(txid)
        if status and status.get("status") in ["ATTESTED", "INCLUDED", "FINALIZED"]:
            return True
        await asyncio.sleep(1)
    return False

attested = await wait_for_attestation(service, txid)
```

### Custom Selection Policy
```python
class CustomSelector:
    def __init__(self, service):
        self.service = service
    
    async def select_high_value(self, limit=100):
        """Select transactions with value > threshold."""
        pending = await self.service.get_pending(limit=1000)
        high_value = [tx for tx in pending if tx.value > 1_000_000_000]
        return sorted(high_value, key=lambda tx: -tx.fee)[:limit]
```

### Monitor Replication Health
```python
async def check_replication_health(service):
    stats = service.get_stats()
    
    stored = stats.get("STORED", 0)
    attested = stats.get("ATTESTED", 0)
    rejected = stats.get("REJECTED", 0)
    
    total_active = stored + attested
    reject_rate = rejected / max(total_active, 1)
    
    return {
        "active": total_active,
        "reject_rate": reject_rate,
        "healthy": reject_rate < 0.1  # <10% rejection
    }
```

## Testing Checklist

- [ ] Submit transaction
- [ ] Verify STORED status
- [ ] Check peer announcement
- [ ] Confirm peer receipt
- [ ] Verify ATTESTED status
- [ ] Test anti-entropy after disconnect
- [ ] Test invalid transaction rejection
- [ ] Test expiration
- [ ] Test block inclusion
- [ ] Verify observability endpoints

## Migration Checklist

- [ ] Update client apps to use new RPC methods
- [ ] Test with ANIMICA_TX_SYSTEM=ptl
- [ ] Verify peer connectivity
- [ ] Monitor replication metrics
- [ ] Update wallet UIs for receipts
- [ ] Document new CLI commands
- [ ] Train support staff
- [ ] Plan rollback strategy (set ANIMICA_TX_SYSTEM=mempool)
