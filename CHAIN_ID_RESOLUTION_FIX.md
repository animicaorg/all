# Chain ID Resolution Fix

## Problem Statement

The Animica CLI was experiencing a critical issue with chain ID mismatch in certain scenarios, leading to transaction failures with the error:

```
Chain ID mismatch: {'got': 0, 'expected': 1}
```

This error occurred when:
1. The `build` command hardcoded `chainId: 31337` regardless of the actual network
2. Transactions were missing chain IDs entirely (defaulting to 0 on the node)
3. Users specified incorrect chain IDs via CLI flags or environment variables

## Root Causes

1. **Hardcoded chain ID in `build` command**: The transaction builder hardcoded chain ID to 31337, causing mismatches when connecting to other networks.

2. **Missing chain ID validation**: The `build` and `sign` commands didn't validate chain IDs against the node before creating or signing transactions.

3. **Unclear error messages**: When mismatches occurred, error messages didn't provide actionable guidance for resolution.

## Solution

### 1. Enhanced `build` Command

**Changes:**
- Added `--chain-id` parameter with `ANIMICA_CHAIN_ID` environment variable support
- Integrated `resolve_chain_id()` function for automatic detection and validation
- Now queries the node for chain ID when not explicitly provided
- Validates explicit chain IDs against the node's chain ID

**Usage:**
```bash
# Auto-detect chain ID from node
animica tx build --from anim1... --to anim1... --value 1.0

# Explicit chain ID (validated against node)
animica tx build --from anim1... --to anim1... --value 1.0 --chain-id 1

# Using environment variable
export ANIMICA_CHAIN_ID=1337
animica tx build --from anim1... --to anim1... --value 1.0
```

### 2. Enhanced `sign` Command

**Changes:**
- Added `--rpc-url` parameter for chain ID validation
- Validates transaction's chain ID against the node before signing
- Warns when transaction file has no chain ID
- Handles invalid chain ID formats gracefully

**Usage:**
```bash
# Sign with chain ID validation
animica tx sign --file tx.json --key 0 --rpc-url http://localhost:8545
```

### 3. PQ Unsafe Mode Detection

**Changes:**
- Added `_warn_if_unsafe_pq_mode()` helper function
- Warns users when using `ANIMICA_UNSAFE_PQ_FAKE=1` (development mode)
- Integrated into `send` command

**Output when using unsafe mode:**
```
⚠️  WARNING: Using ANIMICA_UNSAFE_PQ_FAKE=1 mode
   This is NOT SECURE and should only be used for development/testing.
   Install liboqs-python for production use.
```

### 4. Improved Error Messages

When a chain ID mismatch occurs, users now see:

```
============================================================
Error: Chain ID mismatch between CLI and node
============================================================
CLI chain ID:  99
Node chain ID: 1

The transaction would be rejected by the node.

Solutions:
  1. Remove --chain-id flag (auto-detect from node: 1)
  2. Set --chain-id 1 to match the node
  3. Unset ANIMICA_CHAIN_ID env var if set
  4. Connect to a different node with --rpc-url
============================================================
```

## Edge Cases Handled

The fix handles all common edge cases:

1. ✅ **Node unreachable**: Clear error message when node cannot be contacted
2. ✅ **Node returns null/invalid chain ID**: Validation catches and reports this
3. ✅ **CLI flag vs environment variable conflicts**: Both are validated against node
4. ✅ **Missing chain ID in transaction**: Warning displayed with guidance
5. ✅ **Invalid chain ID format**: TypeError/ValueError caught with clear message
6. ✅ **Dry-run mode**: Chain ID validated before building transaction

## Testing

### Test Coverage

Added comprehensive test suite with 17 tests covering:

- Auto-detection scenarios
- Explicit chain ID matching and mismatching
- Environment variable handling
- Node error conditions
- Invalid formats
- PQ unsafe mode warnings

**Run tests:**
```bash
pytest python/animica/cli/tests/test_chain_id_resolution.py -v
```

**Results:**
```
17 passed in 0.80s
```

### Manual Verification

To manually verify the fix:

1. **Test auto-detection:**
   ```bash
   # Start a local devnet on chain ID 1337
   animica tx build --from anim1... --to anim1... --value 1.0
   # Should detect and use chain ID 1337
   ```

2. **Test mismatch detection:**
   ```bash
   animica tx build --from anim1... --to anim1... --value 1.0 --chain-id 99
   # Should fail with clear mismatch error
   ```

3. **Test environment variable:**
   ```bash
   export ANIMICA_CHAIN_ID=1
   animica tx build --from anim1... --to anim1... --value 1.0
   # Should validate against node
   ```

## Migration Guide

### For Users

**Before (broken):**
```bash
# Would always use chain ID 31337, regardless of network
animica tx build --from anim1... --to anim1... --value 1.0
```

**After (fixed):**
```bash
# Automatically detects chain ID from node
animica tx build --from anim1... --to anim1... --value 1.0

# Or explicitly specify (with validation)
animica tx build --from anim1... --to anim1... --value 1.0 --chain-id 1
```

### For Developers

The `resolve_chain_id()` function is now used consistently across all transaction commands:

```python
from animica.cli.tx import resolve_chain_id

# Resolve and validate chain ID
chain_id = resolve_chain_id(rpc_url, cli_chain_id)
# Returns validated chain ID or raises typer.Exit with clear error
```

## Security Considerations

1. **Chain ID validation prevents replay attacks**: Transactions are now bound to the correct chain.
2. **PQ unsafe mode warning**: Users are explicitly warned when using insecure development mode.
3. **No secrets in error messages**: Error messages only contain non-sensitive information.

## Files Modified

- `python/animica/cli/tx.py`: Main implementation
  - Added `_warn_if_unsafe_pq_mode()` function
  - Enhanced `build` command with chain ID resolution
  - Enhanced `sign` command with chain ID validation
  - Improved error handling throughout

- `python/animica/cli/tests/test_chain_id_resolution.py`: New test suite
  - 17 comprehensive tests
  - Covers all edge cases and error scenarios

## Related Issues

- Fixes: "Chain ID mismatch: {'got': 0, 'expected': 1}" error
- Related to: PR #326 (initial chain ID resolution for `send` command)
- Addresses: PQ unsafe mode warnings for development environments

## Future Enhancements

Potential future improvements:

1. Add `--chain-id` to all other transaction-related commands for consistency
2. Add chain ID to wallet metadata for automatic network detection
3. Implement chain ID caching to reduce RPC calls
4. Add network name resolution (e.g., `--network mainnet` → chain ID 1)

## Conclusion

This fix ensures that all CLI transaction commands properly resolve and validate chain IDs, preventing the "got: 0, expected: 1" error and providing clear, actionable guidance to users when issues occur.
