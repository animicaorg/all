# Miner Sender Derivation and Canonical TxID Fix

## Problem Statement

The miner had critical bugs that prevented transactions from being properly included in blocks:

1. **Missing Sender**: Miner pulled pending txs from `_FALLBACK_PENDING` but dropped them with "Transaction missing sender; skipping" during execution
2. **Hash Mismatch**: Miner logged included tx hashes that didn't match the pending hash keys, suggesting non-canonical txid computation
3. **No Execution**: Transfers never executed, balances didn't change
4. **Wrong Eviction**: Mempool eviction targeted wrong transaction IDs, leaving stale txs in mempool

## Root Cause

The core issue was that transactions stored in the pending pool didn't have the `sender` field populated. When the miner tried to execute them, it would skip them because the sender was missing. Additionally, the miner was computing transaction IDs inconsistently with `sendRawTransaction`.

## Solution

### 1. Helper Functions Added

#### `_tracked(tx) -> tuple[str, bytes] | None`
Checks if a tx has a tracked hash in `_TX_HASH_MAP`, which maps `id(tx_obj)` to `(tx_hash_hex, raw_bytes)`.

#### `_canonical_txid_hex(tx) -> str`
Gets the canonical txid from a tx object using the rule: `TxID = sha3_256(raw_cbor_bytes)`. This matches the rule used by `sendRawTransaction`.

#### `_decode_cbor_loose(raw) -> dict | None`
Safely decodes CBOR bytes to a dict. Returns `None` if cbor2 is unavailable or decoding fails.

#### `_as_bytes32_addr(val) -> bytes`
Converts address values (bytes, hex string, or bech32) to 32-byte format, handling padding/truncation.

#### `_derive_sender_from_envelope_raw(raw) -> bytes | None`
Derives sender address from raw CBOR envelope by:
1. Decoding the envelope to extract signature info
2. Getting `pubkey` and `alg_id` from signature
3. Reconstructing bech32m address using `address_from_pubkey()`
4. Converting to 32-byte raw address format

#### `_attach_sender_if_possible(tx) -> Tx`
Attaches sender to a Tx dataclass by:
1. Checking if tx already has valid sender (return unchanged if so)
2. Looking up tracked raw envelope from `_TX_HASH_MAP`
3. Deriving sender from envelope using `_derive_sender_from_envelope_raw()`
4. Reconstructing Tx with updated sender using `dataclasses.replace()`

#### `_has_valid_sender(tx) -> bool`
Checks if a tx object has a valid (non-zero) sender. Extracts from either `tx.unsigned.sender` or `tx.sender`.

### 2. Transaction Normalization in `_mine_once()`

Added normalization step after collecting pending txs (lines 1591-1616):

```python
if txs:
    txs_normalized = []
    included_hashes_normalized = []
    
    for i, tx in enumerate(txs):
        # Try to attach sender if missing
        tx_normalized = _attach_sender_if_possible(tx)
        
        # Drop txs that still have no sender
        if not _has_valid_sender(tx_normalized):
            log.warning(f"Dropping tx {tx_hash_hex} - no sender after normalization")
            continue
        
        txs_normalized.append(tx_normalized)
        included_hashes_normalized.append(included_hashes[i])
    
    txs = txs_normalized
    included_hashes = included_hashes_normalized
```

### 3. Canonical Hash-Based Eviction

Updated eviction logic to use canonical hashes (lines 1905-1963):

```python
# Compute canonical hashes for eviction
included_hashes_canonical = [_canonical_txid_hex(tx) for tx in txs]

# Evict from adapter mempool
if hasattr(adapter, "evict_by_hashes"):
    adapter.evict_by_hashes(included_hashes_canonical)
else:
    # Fallback: convert to bytes
    hashes_bytes = [_hex_to_bytes(h) for h in included_hashes_canonical]
    adapter.remove_included(hashes_bytes)

# Evict from _PEND pool
for h in included_hashes_canonical:
    pend.remove(h)

# Evict from _FALLBACK_PENDING
for h in included_hashes_canonical:
    cache.pop(h, None)
    ts_cache.pop(h, None)

# Log with canonical hashes
log.info(f"included_tx_hashes={included_hashes_canonical[:3]}")
```

## Acceptance Criteria

✅ **No Fake Inclusions**: Miner no longer logs txs as included if they lacked sender and were dropped

✅ **Sender Attached**: If sender/pubkey is present in envelope, miner attaches sender and applies tx

✅ **Hash Consistency**: `included_tx_hashes` matches pending hash keys from `_FALLBACK_PENDING`

✅ **Proper Eviction**: After inclusion, fallback pending mempool for those txs is emptied via canonical hashes

## Testing

### Unit Tests
- `test_miner_helpers.py`: Validates all helper functions work correctly
- `test_sender_derivation.py`: Tests sender derivation and attachment logic

### Integration Verification
- Canonical txid computation is consistent with `sendRawTransaction`
- Sender validation correctly identifies valid/invalid senders
- Normalization workflow drops txs without sender
- Eviction uses canonical hashes everywhere

## Implementation Notes

### Minimal Changes
- Changes localized to `rpc/methods/miner.py` only
- No modifications to core tx types or other modules
- Backward compatible with existing code

### Safe Defaults
- `_decode_cbor_loose()` safely returns `None` if cbor2 not installed
- `_derive_sender_from_envelope_raw()` returns `None` if derivation fails
- `_attach_sender_if_possible()` returns original tx if attachment fails
- Txs without sender are dropped with warning (not silently included)

### Thread Safety
- `_TX_HASH_MAP` is not thread-safe but acceptable because:
  - RPC methods are called sequentially within same worker process
  - Mining operations are synchronous
  - Map is short-lived and cleaned up after use

## Related Files

- `rpc/methods/miner.py`: Primary implementation
- `rpc/methods/tx.py`: Reference for `_decode_tx()` and `_FALLBACK_PENDING`
- `core/types/tx.py`: Tx dataclass structure

## Migration Notes

No migration needed. The fix is backward compatible and improves existing behavior without breaking changes.
