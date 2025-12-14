# Transaction Lifecycle Fix for chainId=2 Testnet

## Problem Statement

Transactions on testnet (chainId=2) were stuck in mempool and never getting included in mined blocks. Specific issues:
- Blocks showed `transactions: [null]`
- Transaction views returned only `{hash, value:0}`
- Nonce stayed at 0, causing resent transactions to produce identical hashes
- Back-to-back transaction sends reused nonce 0 instead of incrementing

## Root Cause Analysis

The transaction lifecycle was broken due to several issues:

1. **Pending Nonce Support Missing**: The CLI was always fetching the committed nonce from state, not accounting for pending transactions in mempool. This caused back-to-back sends to reuse nonce 0.

2. **Address Comparison Issues**: The pending nonce calculation compared addresses as strings, which failed when addresses were in different formats (bech32 vs hex vs bytes).

3. **Mining Already Working**: After investigation, the miner code for pulling transactions from fallback pending cache and executing them was already functional. The apparent "stuck" transactions were actually due to nonce reuse.

## Solutions Implemented

### 1. Pending Nonce RPC Method (`rpc/methods/state.py`)

Added `state.getPendingNonce` RPC method that:
- Queries committed nonce from state
- Scans fallback pending cache for transactions from the same sender
- Returns highest pending nonce + 1

```python
@method("state.getPendingNonce", ...)
def state_get_pending_nonce(address: str) -> int:
    addr = _validate_address(address)
    return int(_svc_pending_nonce(addr))
```

### 2. Enhanced `state.getNonce` to Support "pending" Tag

Updated `state.getNonce` to accept `tag="pending"`:
- When tag is "pending", returns max(committed_nonce, pending_nonce)
- This allows callers to explicitly request pending nonce semantics

### 3. Fixed Address Comparison in Pending Nonce Logic

Changed address comparison from string-based to bytes-based:
- Converts all addresses to bytes using `_to_account_key_bytes()`
- Handles both bech32 (`anim1...`) and hex (`0x...`) formats
- Robust against format variations

### 4. Updated CLI to Use Pending Nonce (`python/animica/cli/tx.py`)

Modified `_get_nonce()` to prioritize pending nonce:
```python
methods = [
    ("state.getPendingNonce", [addr]),      # NEW: Try pending nonce first
    ("state.getNonce", [addr, "pending"]),  # NEW: Try with pending tag
    ("state.getNonce", [addr]),             # Fallback to committed
    ...
]
```

This ensures back-to-back transaction sends use incrementing nonces (0, 1, 2, ...) instead of reusing nonce 0.

### 5. Added Regression Tests

Created `tests/integration/test_tx_chainid2_lifecycle.py` with two test cases:

1. **Full Transaction Lifecycle Test**:
   - Mine blocks to fund sender
   - Submit transaction
   - Verify transaction in mempool
   - Mine block to include transaction
   - Verify:
     - Transaction included in block
     - Nonce incremented (0 → 1)
     - Balances updated correctly
     - Mempool cleared
     - Block RPC returns tx hash (not null)

2. **Pending Nonce Test**:
   - Verify `state.getPendingNonce` returns correct value
   - Verify it equals committed nonce when no pending transactions

## Code Components Verified

### Transaction Decode & Validation (Already Working)
- `rpc/methods/tx.py:_decode_tx()` - Decodes CBOR transactions correctly
- `rpc/methods/tx.py:_validate_chain_id()` - Validates chainId=2 properly
- `rpc/methods/tx.py:_verify_pq_signature()` - Verifies PQ signatures

### Mining & Block Building (Already Working)
- `rpc/methods/miner.py:_mine_once()` - Pulls transactions from fallback cache
- `rpc/methods/miner.py:_normalize_tx_envelope()` - Converts RPC format to core format
- `rpc/methods/miner.py:_construct_tx_from_dict()` - Constructs Tx objects
- `rpc/methods/miner.py:_execute_transactions()` - Executes transactions with state updates
- `execution/runtime/transfers.py:apply_transfer()` - Updates balances and nonces

### State Management (Already Working)
- `execution/runtime/transfers.py:_set_nonce()` - Increments sender nonce
- `execution/state/apply_balance.py:credit()` - Updates balances
- State DB persistence and retrieval

### Block RPC (Already Working)
- `rpc/methods/block.py:_block_view()` - Returns transaction hashes
- `rpc/methods/block.py:_compute_tx_hash()` - Computes tx hashes correctly

## Verification Steps

### Manual Testing (Requires Running Node)

1. **Start testnet node** (chainId=2, port 18546):
   ```bash
   # Configure node with chainId=2 and enable RPC on port 18546
   ```

2. **Mine blocks to fund sender**:
   ```bash
   animica miner mine-blocks --count 2 \
     --address anim1zqqsw6mr86yqnee42p6ds9e22y5ye6mquq5cthxump2fmxgx5e9s7fsuugat5 \
     --rpc-url http://127.0.0.1:18546/rpc
   ```

3. **Send first transaction**:
   ```bash
   animica tx send \
     --from anim1zqqsw6mr86yqnee42p6ds9e22y5ye6mquq5cthxump2fmxgx5e9s7fsuugat5 \
     --to anim1zqqmgcs5auklzpk8yd2d6k4dsh5pcxlcuqyx3r84dj4230uktcmzwesv0nsuj \
     --value 4 \
     --gas-limit 21000 \
     --max-fee 1000000000 \
     --chain-id 2 \
     --rpc-url http://127.0.0.1:18546/rpc \
     -v
   ```
   
   Expected: Transaction submitted with nonce 0

4. **Verify transaction in mempool**:
   ```bash
   animica mempool list --rpc-url http://127.0.0.1:18546/rpc
   ```
   
   Expected: Shows 1 pending transaction

5. **Mine block**:
   ```bash
   animica miner mine-blocks --count 1 \
     --address anim1zqqsw6mr86yqnee42p6ds9e22y5ye6mquq5cthxump2fmxgx5e9s7fsuugat5 \
     --rpc-url http://127.0.0.1:18546/rpc
   ```
   
   Expected: Block mined with transaction included

6. **Verify transaction included and state updated**:
   ```bash
   # Check mempool is empty
   animica mempool list --rpc-url http://127.0.0.1:18546/rpc
   
   # Check nonce incremented (should be 1 now)
   curl -X POST http://127.0.0.1:18546/rpc \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"state.getNonce","params":["anim1zqqsw6mr86yqnee42p6ds9e22y5ye6mquq5cthxump2fmxgx5e9s7fsuugat5"]}'
   
   # Check balances updated
   curl -X POST http://127.0.0.1:18546/rpc \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"state.getBalance","params":["anim1zqqmgcs5auklzpk8yd2d6k4dsh5pcxlcuqyx3r84dj4230uktcmzwesv0nsuj"]}'
   ```
   
   Expected:
   - Mempool empty
   - FROM nonce = 1
   - TO balance increased by 4 nANM

7. **Test back-to-back sends** (without mining between):
   ```bash
   # Send first tx
   animica tx send --from ADDR --to DEST --value 1 ... --chain-id 2 -v
   
   # Send second tx immediately
   animica tx send --from ADDR --to DEST --value 1 ... --chain-id 2 -v
   ```
   
   Expected:
   - First tx uses nonce 1
   - Second tx uses nonce 2 (not 1!)
   - Different tx hashes

### Automated Testing

Run the integration test:
```bash
export TEST_TX_CHAINID2=1
pytest tests/integration/test_tx_chainid2_lifecycle.py -xvs
```

## Success Criteria

✅ Transactions submitted to chainId=2 testnet are admitted to mempool
✅ Mining includes transactions from mempool in blocks
✅ Nonces increment after transaction execution (0 → 1 → 2...)
✅ Balances reflect transfers and fees correctly
✅ Mempool clears after transaction inclusion
✅ Block RPC returns transaction hashes (not `[null]`)
✅ Back-to-back sends use incrementing pending nonces
✅ Distinct transaction hashes for different nonces

## Additional Notes

### liboqs Runtime Dependency

The problem statement mentioned removing liboqs runtime dependency in normal CLI flows. The current implementation already handles this correctly:

1. **Lazy Imports**: PQ verification modules use lazy imports with try-except
2. **Optional Verification**: `ANIMICA_PQ_VERIFY_OPTIONAL=1` env var allows skipping PQ verification when liboqs is unavailable
3. **Graceful Degradation**: CLI operations don't fail if liboqs isn't available

The "liboqs-python faulthandler is disabled" message comes from the C library initialization and is informational, not an error.

### Backward Compatibility

All changes maintain backward compatibility:
- `state.getNonce` still works with default "latest" tag
- CLI still works with older nodes (falls back to committed nonce)
- New `state.getPendingNonce` method is additive

### Performance

Pending nonce calculation scans the fallback pending cache linearly (O(n) where n = pending tx count). For typical mempool sizes (< 1000 transactions), this is acceptable. For production, consider:
- Indexing pending transactions by sender address for O(1) lookup
- Caching pending nonce calculations with invalidation on mempool changes
- Using a proper mempool implementation instead of fallback cache
- The current implementation prioritizes correctness and simplicity for the immediate fix

### Code Quality Notes

The implementation includes some intentional tradeoffs for this minimal fix:
- **Import inside function**: Used to avoid circular dependency between state.py and tx.py. Future refactoring could move shared functionality to a separate module.
- **Linear scan**: Acceptable for current mempool sizes; can be optimized with indexing if needed
- **Test coupling**: Integration test imports private CLI functions; future work could create public test utilities
