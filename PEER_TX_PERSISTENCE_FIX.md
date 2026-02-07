# Peer Transaction Persistence Fix

## Problem Statement
Ensure pending transactions from known peers are added to everyone's `path=/data/chain-1/mempool/pending.jsonl`.

## Analysis

### Existing Architecture
The mempool persistence mechanism was already in place:
- `MempoolService._persist_snapshot()` is called after every successful transaction admission
- The persist path is set to `{data_dir}/mempool/pending.jsonl` where `data_dir` defaults to:
  1. `$ANIMICA_DATA_DIR` environment variable, or
  2. SQLite DB parent directory, or  
  3. `~/.animica/chain-{chain_id}`
- In Docker deployments, `data_dir` is typically `/data/chain-{id}`

### Transaction Flow
Peer transactions flow through:
1. `TxRelayService.on_tx_data()` - receives transaction from peer
2. `P2PService._admit_tx_result()` - validates and forwards  
3. `P2PDeps.admit_tx()` - performs admission checks
4. `MempoolService.submit()` - adds to mempool and persists
5. `MempoolService._persist_snapshot()` - writes to `pending.jsonl`

**The persistence mechanism was working correctly for all transactions (local and peer)**, but two bugs were preventing peer transactions from being admitted successfully.

## Bugs Found and Fixed

### Bug #1: FeeTooLow Exception Signature Mismatch
**Location:** `mempool/pool.py` line 424

**Problem:** The `FeeTooLow` exception constructor requires keyword arguments, but was being called with a positional argument:
```python
raise FeeTooLow("effective fee below current admit floor")  # ❌ Wrong
```

**Fix:** Updated to use keyword arguments matching the exception signature:
```python
th = self.wm.thresholds(pool_size=len(self.index), capacity=self.cfg.max_txs)
offered = int(getattr(meta, "effective_fee_wei", 0))
required = int(th.admit_floor_wei)
tx_hash_hex = "0x" + h.hex() if h else None
sender = getattr(meta, "sender", None)
raise FeeTooLow(
    offered_gas_price_wei=offered,
    min_required_wei=required,
    tx_hash=tx_hash_hex,
    sender=sender,
)
```

### Bug #2: EffectiveFee Not Extracting Gas Price from Wrapped Tx Objects
**Location:** `mempool/types.py` EffectiveFee.from_tx()

**Problem:** When `Tx.transfer()` creates a transaction, it returns a `Tx` object with the actual transaction data in a nested `unsigned` field. The gas_price is at `tx.unsigned.gas_price`, not `tx.gas_price`. The code was looking for `tx.gas_price` directly, resulting in a gas price of 0.

**Fix:** Added unwrapping logic to handle wrapped Tx objects:
```python
@staticmethod
def from_tx(tx: Tx) -> "EffectiveFee":
    """
    Construct from a transaction object that may carry either legacy or
    EIP-1559-style fee fields.
    """
    # If tx is a wrapped Tx with an 'unsigned' field, unwrap it
    if hasattr(tx, "unsigned") and tx.unsigned is not None:
        tx = tx.unsigned
    
    # ... rest of method
```

## Impact

With these fixes:
1. ✅ Peer transactions can now pass fee floor validation (when they have adequate gas price)
2. ✅ Peer transactions are successfully admitted to the mempool
3. ✅ Peer transactions are persisted to `pending.jsonl` along with local transactions
4. ✅ All existing persistence tests continue to pass
5. ✅ New tests verify peer transaction persistence works correctly

## Testing

Added comprehensive tests in `rpc/tests/test_mempool_peer_tx_persistence.py`:
- `test_peer_tx_persists_to_pending_jsonl` - Verifies single peer transaction is persisted
- `test_multiple_peer_txs_persisted` - Verifies multiple peer transactions are persisted
- `test_local_and_peer_txs_both_persisted` - Verifies both local and peer transactions are persisted

All tests pass:
```
rpc/tests/test_mempool_persistence.py::test_mempool_persists_and_restores PASSED
rpc/tests/test_mempool_peer_tx_persistence.py::test_peer_tx_persists_to_pending_jsonl PASSED
rpc/tests/test_mempool_peer_tx_persistence.py::test_multiple_peer_txs_persisted PASSED  
rpc/tests/test_mempool_peer_tx_persistence.py::test_local_and_peer_txs_both_persisted PASSED
```

## Files Changed

1. **mempool/pool.py** - Fixed FeeTooLow exception call (lines 422-432)
2. **mempool/types.py** - Fixed EffectiveFee.from_tx to handle wrapped Tx objects (lines 84-89)
3. **rpc/tests/test_mempool_peer_tx_persistence.py** - Added comprehensive persistence tests (new file)

## Deployment Notes

No configuration changes or migration steps required. The fixes are backward compatible and transparent to users. The `pending.jsonl` file will continue to be written to the same location (`{data_dir}/mempool/pending.jsonl`), typically `/data/chain-1/mempool/pending.jsonl` in Docker deployments.
