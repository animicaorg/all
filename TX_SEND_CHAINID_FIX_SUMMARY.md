# Fix: tx.sendRawTransaction chain_id inclusion in signed payload

## Summary

Fixed the "header/tx missing chain id for signing" error when broadcasting transactions via `animica tx send --chain-id 1`.

## Root Cause

The issue was in `core/encoding/canonical.py` function `_chain_id_from()`. When the SDK sends a signed transaction, it uses this envelope structure:

```python
{
  "body": {
    "chainId": 1,
    "from": "anim1...",
    "to": "anim1...",
    "nonce": 0,
    "value": 1000,
    "gasLimit": 21000,
    "maxFee": 1000000000,
    "data": b""
  },
  "sig": {
    "algId": 4098,
    "pubkey": b"...",
    "sig": b"..."
  }
}
```

However, `_chain_id_from()` only checked for `chainId` in:
- Object attributes (`obj.chain_id`, `obj.chainId`)
- Flat mapping keys (`mapping["chain_id"]`, `mapping["chainId"]`)

It did NOT check for nested `body` field, causing the error when the node tried to validate the signature by calling `tx_signing_bytes(envelope)`.

## Solution

Updated `_chain_id_from()` to also check for `chainId` in nested `body` structure:

```python
# Check for nested body structure (signed envelope: {"body": {...}, "sig": {...}})
if "body" in mapping and isinstance(mapping["body"], Mapping):
    body = mapping["body"]
    if "chain_id" in body:
        return int(body["chain_id"])
    if "chainId" in body:
        return int(body["chainId"])
```

## Files Changed

1. **core/encoding/canonical.py**
   - Updated `_chain_id_from()` to check nested `body.chainId`
   - 6 lines added

2. **core/encoding/test_canonical_chain_id_from_body.py** (new)
   - 8 comprehensive unit tests for `_chain_id_from()`
   - Tests flat structure, nested body, snake_case vs camelCase, missing chainId, etc.

3. **python/animica/cli/tests/test_tx_send_chainid_integration.py** (new)
   - 2 integration tests for CLI->SDK->RPC flow
   - Verifies chainId is included in broadcast payload
   - Verifies dry-run shows chainId

4. **python/animica/cli/tests/test_chain_id_resolution.py**
   - Fixed 4 test assertions to handle `resolve_chain_id()` tuple return

## Testing

All tests pass:
- ✅ Unit tests: 8/8 passed (core/encoding)
- ✅ Integration tests: 2/2 passed (CLI tests)
- ✅ SDK tests: 6/6 passed (test_tx_chainid.py)
- ✅ CLI tests: 47/47 passed (test_tx_cli.py)
- ✅ Chain ID resolution: 21/21 passed (test_chain_id_resolution.py)
- ✅ **Total: 84 tests passed**

## Validation

Simulated complete node validation flow:
1. ✓ CLI builds transaction with chain_id=1
2. ✓ SDK packs with signature in `{"body": {...}, "sig": {...}}` format
3. ✓ Node receives raw CBOR via `tx.sendRawTransaction`
4. ✓ Node extracts chain_id from `body.chainId`
5. ✓ Node validates chain_id matches (1 == 1)
6. ✓ Node computes sign bytes for signature verification

## Impact

- **Before**: Broadcasting transactions failed with "header/tx missing chain id for signing"
- **After**: Transactions broadcast successfully with chain_id validated correctly

## Backward Compatibility

The fix is **fully backward compatible**:
- Still supports flat `chainId` in tx objects
- Still supports `chain_id` attribute on objects
- Adds support for nested `body.chainId` without breaking existing paths
- All existing tests pass without modification (except 4 test assertions that needed to unpack tuple)

## Related Issues

This fix completes the chain_id inclusion work by ensuring:
1. SDK includes chainId in body ✓ (already working)
2. CLI passes chainId to SDK ✓ (already working)
3. Node can extract chainId from signed envelope ✓ (FIXED)
4. Node validates chainId matches ✓ (already working)

## Commands to Test

```bash
# Dry-run (shows transaction details without broadcasting)
animica tx send --from alice --to anim1... --value 1.0 --chain-id 1 --dry-run

# Broadcast (requires running node)
animica tx send --from alice --to anim1... --value 1.0 --chain-id 1

# Expected output (dry-run):
# === Dry-Run Mode ===
# From:       anim1...
# To:         anim1...
# Value:      1.0 ANM
# Chain ID:   1
# ✓ Transaction built and signed (not broadcast)

# Expected output (broadcast):
# === Transaction Submitted ===
# Tx Hash: 0x...
# ✓ Transaction broadcast successfully
```

## References

- Problem statement: "Ensure tx.sendRawTransaction includes chain_id in signed payload"
- SDK encode.py: Already packs body with chainId correctly
- CLI tx.py: Already passes chain_id to SDK correctly
- Node tx.py: Already validates chainId via `_validate_chain_id()` correctly
- **Fixed**: Node canonical.py `_chain_id_from()` now extracts from body
