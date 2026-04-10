# mempool2 Quick Reference

## Installation

```python
# Package is at: /home/runner/work/all/all/mempool2/
# Import from parent directory or add to PYTHONPATH
from mempool2 import admit_tx, MempoolStorage, select_txs
```

## Core Operations

### 1. Initialize Storage

```python
from mempool2 import MempoolStorage

storage = MempoolStorage("/path/to/mempool.db")
```

### 2. Admit Transaction

```python
from mempool2 import admit_tx

# Basic admission (no state checks)
success, rejection = admit_tx(envelope, storage, source="rpc")

# With state checks
def get_state(address):
    return (balance, confirmed_nonce)

success, rejection = admit_tx(
    envelope, storage, source="rpc",
    balance_getter=get_state,
    chain_id=1,
    max_tx_bytes=128*1024,
    min_fee_rate=1
)

if success:
    print("Admitted!")
else:
    print(f"Rejected: {rejection.message}")
    print(f"Reason: {rejection.reason}")
    print(f"Hint: {rejection.hint}")
```

### 3. Select for Block Template

```python
from mempool2 import select_txs

txs = select_txs(
    storage,
    max_gas=8_000_000,
    max_bytes=1_048_576
)
# Returns List[TxEnvelope]
```

### 4. Eviction

```python
from mempool2.evict import check_capacity, per_sender_limit

# Check capacity
to_evict = check_capacity(storage, max_txs=10000, max_bytes=50_000_000)
for txid in to_evict:
    storage.remove_tx(txid)

# Per-sender limit
to_evict = per_sender_limit(storage, sender_addr, max_per_sender=100)
for txid in to_evict:
    storage.remove_tx(txid)
```

### 5. Statistics

```python
stats = storage.get_stats()
print(f"Count: {stats.tx_count}")
print(f"Bytes: {stats.total_bytes}")
print(f"Senders: {stats.unique_senders}")
print(f"Min fee: {stats.fee_stats.min_fee_rate}")
print(f"Max fee: {stats.fee_stats.max_fee_rate}")
```

## Data Types

```python
from mempool2 import MempoolEntry, TxSource

# MempoolEntry
entry = MempoolEntry(
    envelope=tx_envelope,
    arrival_time=time.time(),
    fee_rate=envelope.body.fee // envelope.body.gas_limit,
    source=TxSource.RPC,
    peer_id=None  # or peer ID string for P2P
)

# TxSource enum
TxSource.RPC        # From JSON-RPC
TxSource.P2P        # From peer
TxSource.LOCAL      # Locally generated
TxSource.RESUBMIT   # After reorg
```

## Policy Functions

```python
from mempool2 import policy

# All return Optional[TxReject]
rejection = policy.check_format(envelope)
rejection = policy.check_chain_id(envelope, 1)
rejection = policy.check_size(envelope, 128*1024)
rejection = policy.check_fee(envelope, min_fee_rate=1)
rejection = policy.check_nonce(envelope, confirmed_nonce=5, pending_nonces={6, 7})
rejection = policy.check_funds(envelope, balance=1000000, pending_debits=50000)

if rejection is None:
    # Valid!
    pass
else:
    print(rejection.message)
```

## Storage Methods

```python
# Add transaction
added = storage.add_tx(entry)  # Returns bool

# Get transaction
entry = storage.get_tx(txid)  # Returns MempoolEntry or None

# Check existence
exists = storage.has_tx(txid)  # Returns bool

# Remove transaction
removed = storage.remove_tx(txid)  # Returns bool

# List all (sorted by fee desc)
entries = storage.list_txs(limit=100)

# Iterate by fee
for entry in storage.iter_by_fee(descending=True):
    # Highest fee first
    pass

for entry in storage.iter_by_fee(descending=False):
    # Lowest fee first
    pass

# Query by sender
entries = storage.get_sender_txs(sender_addr)
nonces = storage.get_sender_nonces(sender_addr)
debits = storage.get_sender_pending_debits(sender_addr)

# Statistics
stats = storage.get_stats()

# Clear all
count = storage.clear()
```

## Error Handling

```python
from coretx import RejectReason

success, rejection = admit_tx(...)

if not success:
    # Check rejection reason
    if rejection.reason == RejectReason.nonce_too_low:
        # Handle nonce error
        pass
    elif rejection.reason == RejectReason.insufficient_funds:
        # Handle balance error
        pass
    elif rejection.reason == RejectReason.fee_too_low:
        # Handle fee error
        pass
    
    # Get RPC error code
    rpc_code = rejection.code  # e.g., 2301 for nonce_too_low
    
    # Get diagnostic context
    context = rejection.context  # dict with details
    
    # For internal errors
    if rejection.error_class:
        print(f"Internal error: {rejection.error_class}")
```

## Common Patterns

### RPC Handler

```python
async def eth_sendRawTransaction(tx_bytes: bytes) -> str:
    envelope = decode_tx_envelope(tx_bytes)
    
    success, rejection = admit_tx(
        envelope, storage, source="rpc",
        balance_getter=get_account_state
    )
    
    if success:
        # Broadcast to network
        await p2p_broadcast(envelope)
        return envelope.txid.hex()
    else:
        raise JsonRpcError(rejection.code, rejection.message)
```

### P2P Handler

```python
async def on_peer_transaction(peer_id: str, tx_bytes: bytes):
    envelope = decode_tx_envelope(tx_bytes)
    
    success, rejection = admit_tx(
        envelope, storage, source="p2p", peer_id=peer_id,
        balance_getter=get_account_state
    )
    
    if success:
        # Relay to other peers
        await gossip_to_peers(envelope, exclude=[peer_id])
    else:
        logger.debug(f"Rejected tx from {peer_id}: {rejection.reason}")
```

### Block Production

```python
def create_block():
    # Select transactions
    txs = select_txs(storage, max_gas=8_000_000, max_bytes=1_048_576)
    
    # Build block
    block = Block(
        transactions=txs,
        total_fees=sum(tx.body.fee for tx in txs),
        gas_used=sum(tx.body.gas_limit for tx in txs),
    )
    
    return block
```

### Reorg Handling

```python
def handle_reorg(reverted_blocks, new_blocks):
    # Resubmit transactions from reverted blocks
    for block in reverted_blocks:
        for tx in block.transactions:
            admit_tx(tx, storage, source="resubmit")
    
    # Remove transactions now in canonical chain
    for block in new_blocks:
        for tx in block.transactions:
            storage.remove_tx(tx.txid)
```

### Periodic Maintenance

```python
async def mempool_maintenance():
    while True:
        await asyncio.sleep(60)  # Every minute
        
        # Evict expired transactions (older than 1 hour)
        from mempool2.evict import evict_expired
        current_time = time.time()
        to_evict = evict_expired(storage, current_time, max_age_seconds=3600)
        for txid in to_evict:
            storage.remove_tx(txid)
        
        # Enforce capacity limits
        from mempool2.evict import check_capacity
        to_evict = check_capacity(storage, max_txs=10000, max_bytes=50_000_000)
        for txid in to_evict:
            storage.remove_tx(txid)
```

## Testing

```python
# Run all tests
pytest mempool2/tests/

# Run specific module
pytest mempool2/tests/test_policy.py -v

# Run with coverage
pytest mempool2/tests/ --cov=mempool2 --cov-report=html
```

## Key Guarantees

1. **Never throws**: `admit_tx()` NEVER throws exceptions
2. **Pure functions**: All policy functions have no side effects
3. **Deterministic**: Eviction order is always the same
4. **Crash-safe**: SQLite WAL mode protects against corruption
5. **Nonce ordering**: Block templates enforce sequential nonces per sender

## Performance Tips

1. **Use indexes**: Query by sender or fee_rate uses indexes (fast)
2. **Batch operations**: Use transactions for multiple operations
3. **Limit queries**: Use `limit` parameter when listing transactions
4. **Cache stats**: Statistics query is expensive for large mempools
5. **Periodic cleanup**: Run eviction regularly to keep mempool manageable

## Troubleshooting

### Transaction rejected: "scheme_unsupported"
- PQ crypto library not available
- Install required crypto dependencies
- See `coretx/crypto.py` for supported schemes

### Transaction rejected: "nonce_gap"
- Missing earlier nonces from same sender
- User must submit nonces sequentially
- Check `rejection.context["missing_nonces"]`

### Transaction rejected: "insufficient_funds"
- Balance too low for value + fee
- Check pending transactions consume balance
- See `rejection.context["usable"]` for available balance

### Storage performance degradation
- Too many transactions in mempool
- Run capacity eviction
- Consider lowering `max_txs` parameter
- Vacuum database: `sqlite3 mempool.db "VACUUM;"`

## Links

- Full documentation: `mempool2/README.md`
- Implementation summary: `mempool2/IMPLEMENTATION_SUMMARY.md`
- coretx package: `../coretx/`
- Test suite: `mempool2/tests/`
