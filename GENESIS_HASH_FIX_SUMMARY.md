# Fix Summary: Genesis Hash Display Bug

## Issue
When running `animica node status`, the genesis hash was displayed as:
```
RPC Reported Genesis Hash: 0x<bound method Header.hash of Header(...)>
```

Instead of the expected hex string:
```
RPC Reported Genesis Hash: 0x6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242
```

## Root Cause
The `Header` class in `core/types/header.py` defines `hash()` as a **method** (not a property):
```python
def hash(self) -> bytes:
    """Consensus header hash (block id): sha3_256(CBOR(header))."""
    return sha3_256(self.to_cbor())
```

Multiple places in the codebase used `getattr(header, "hash", None)` which returns the **bound method object** instead of calling it. When converted to a string, this produces the ugly `<bound method ...>` representation.

## Solution
Added callable checks before using hash values:
```python
# Before (broken):
hash_val = getattr(header, "hash", None)
if hash_val:
    return "0x" + hash_val.hex()  # ERROR: bound method has no .hex()

# After (fixed):
hash_val = getattr(header, "hash", None)
if callable(hash_val):
    hash_val = hash_val()
if hash_val:
    return "0x" + hash_val.hex()  # OK: bytes has .hex()
```

## Files Changed
1. `rpc/methods/net.py` - `net_get_genesis_hash()` RPC method
2. `rpc/methods/chain.py` - Header hash computation fallback
3. `rpc/methods/block.py` - Header hash computation fallback
4. `execution/adapters/block_db.py` - Block hash derivation
5. `execution/cli/apply_block.py` - Head hash retrieval
6. `p2p/sync/headers.py` - Header sync operations
7. `p2p/deps.py` - Debug output formatting

## Testing
Manual verification confirmed:
- ✓ Callable methods are properly invoked
- ✓ Non-callable properties work unchanged
- ✓ No bound method strings leak into output

## Impact
- **User-visible**: Fixed the confusing output in `animica node status`
- **System-wide**: Improved robustness across 7 modules
- **Backward compatible**: Works with both method and property implementations
