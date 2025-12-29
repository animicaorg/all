# Bug Investigation: "missing sender or nonce" Error

## Bug Summary

When running `animica tx send`, the transaction fails with RPC error `-32010 mempool admission failed` with the detailed error: `admission_failed: missing sender or nonce`.

## Root Cause Analysis

After analyzing the codebase, I've identified the following issue:

### Expected Transaction Envelope Structure

The RPC expects one of two envelope structures:

**Structure 1 (Legacy `Tx` dataclass format):**
```python
{
  "tx": {
    "v": 1,
    "chainId": 1,
    "from": <32 bytes>,
    "nonce": 0,
    "gas": {
      "price": 1,
      "limit": 21000
    },
    "payload": {
      "t": 0,  # TxKind.TRANSFER
      "v": {
        "to": <32 bytes>,
        "amount": 17000000000,
        "data": b""
      }
    },
    "accessList": []
  },
  "sigs": [
    {
      "alg": 4098,
      "pubkey": <bytes>,
      "sig": <bytes>
    }
  ]
}
```

**Structure 2 (Alternative format mentioned in RPC docs):**
```python
{
  "body": { ...transaction fields... },
  "sig": {
    "algId": <int>,
    "pubkey": <bytes>,
    "sig": <bytes>
  }
}
```

### Current CLI Implementation

The CLI (in `python/animica/cli/tx.py`) sends this structure:

```python
{
  "sig": {
    "algId": 4098,
    "pk": <bytes>,  # Note: "pk" not "pubkey"
    "sig": <bytes>,
    "domain": "tx",
    "prehash": "sha3-512",
    "chainId": 1
  },
  "body": {
    "to": <32 bytes>,
    "from": <32 bytes>,
    "value": 17000000000,
    "nonce": 0,
    "gasLimit": 21000,
    "maxFee": 1,
    "data": b"",
    "chainId": 1
  }
}
```

### The Problem Chain

1. **RPC receives the envelope** and tries to decode it using `_decode_tx()` (rpc/methods/tx.py:746)
2. **CBOR decodes successfully** to a dict
3. **RPC tries to construct Tx dataclass** using `Tx.from_obj(obj)` (rpc/methods/tx.py:786)
4. **Tx.from_obj fails** because it expects `obj["tx"]` but the envelope has `obj["body"]` (core/types/tx.py:456)
5. **RPC falls back to using dict** directly (rpc/methods/tx.py:793-794)
6. **Signature validation passes** because `_extract_sig()` is flexible and supports both "pk" and "pubkey" (rpc/methods/tx.py:465)
7. **Balance validation passes** (rpc/methods/tx.py:1426)
8. **Mempool admission fails** because `_sender_bytes()` and `_tx_nonce()` expect a Tx dataclass with `tx.unsigned.sender` and `tx.unsigned.nonce` attributes (rpc/mempool_service.py:40-74)

### Why It Fails

The mempool service helper functions only work with Tx dataclasses:

**_sender_bytes() (rpc/mempool_service.py:40-56):**
```python
def _sender_bytes(tx: Any) -> Optional[bytes]:
    unsigned = getattr(tx, "unsigned", None)  # dict has no "unsigned" attr
    sender = None
    if unsigned is not None:
        sender = getattr(unsigned, "sender", None)
    if sender is None:
        sender = getattr(tx, "sender", None)  # dict has no "sender" attr
    # ... returns None for dict inputs
```

**_tx_nonce() (rpc/mempool_service.py:65-74):**
```python
def _tx_nonce(tx: Any) -> Optional[int]:
    unsigned = getattr(tx, "unsigned", None)  # dict has no "unsigned" attr
    if unsigned is not None:
        nonce = getattr(unsigned, "nonce", None)
    else:
        nonce = getattr(tx, "nonce", None)  # dict has no "nonce" attr
    # ... returns None for dict inputs
```

## Affected Components

1. **CLI tx encoding** (`python/animica/cli/tx.py`):
   - `_build_tx_body()` (line 223-249): Creates flat body structure
   - `_build_raw_tx()` (line 252-271): Creates envelope with "sig" and "body" keys
   
2. **RPC tx decoding** (`rpc/methods/tx.py`):
   - `_decode_tx()` (line 746): Tries Tx.from_obj() but falls back to dict
   - `_tx_send_raw_transaction()` (line 1331): Passes dict to mempool if Tx construction fails
   
3. **Mempool service** (`rpc/mempool_service.py`):
   - `_sender_bytes()` (line 40): Expects Tx dataclass, not dict
   - `_tx_nonce()` (line 65): Expects Tx dataclass, not dict

## Proposed Solution

**Option 1: Fix CLI to send correct nested structure** (RECOMMENDED)

Update `_build_tx_body()` and `_build_raw_tx()` in the CLI to produce the nested `{"tx": {...}, "sigs": [...]}` structure that `Tx.from_obj()` expects. This ensures the RPC can construct a proper Tx dataclass.

**Option 2: Fix mempool service to handle dicts**

Update `_sender_bytes()` and `_tx_nonce()` to also extract values from dict envelopes with `body` key. This is less ideal because it creates a dual-path architecture.

**Option 3: Add RPC compatibility layer**

Add a converter in the RPC that transforms `{"body": {...}, "sig": {...}}` envelopes to `{"tx": {...}, "sigs": [...]}` before calling `Tx.from_obj()`.

## Recommendation

After further investigation, I discovered that **the SDK also sends flat body structures** via `pack_signed()` which creates `{"body": {...flat...}, "sig": {...}}` envelopes. This suggests that:

1. The RPC is supposed to support flat body structures
2. The mempool service helper functions should handle dicts, not just Tx dataclasses

**Implement Option 2**: Fix mempool service to handle flat body dicts (RECOMMENDED)

Update `_sender_bytes()` and `_tx_nonce()` in `rpc/mempool_service.py` to extract values from dict envelopes:
- Check if `tx` is a dict with `body` key
- Extract `from`/`sender` and `nonce` from the body dict
- Fallback to the existing dataclass attribute access for backwards compatibility

This solution:
1. Aligns with the SDK's envelope format
2. Maintains backward compatibility with Tx dataclasses
3. Fixes the bug without requiring CLI changes
4. Matches the documented RPC API structure

**Optional Enhancement**: Also update CLI to use "pubkey" instead of "pk" for consistency with SDK, although this is not strictly necessary since `_extract_sig()` tolerates both.

---

## Implementation

### Changes Made

**File: `rpc/mempool_service.py`**

Updated four helper functions to handle dict envelopes with a `body` key:

1. **`_sender_bytes(tx)` (lines 40-67)**
   - Added check for `isinstance(tx, dict)` with `body` key
   - Extracts `from` or `sender` from `body` dict (CLI uses "from")
   - Maintains backward compatibility with Tx dataclass format

2. **`_tx_nonce(tx)` (lines 77-97)**
   - Added check for dict envelope with `body` key
   - Extracts `nonce` from `body` dict
   - Maintains backward compatibility with Tx dataclass format

3. **`_tx_gas_limit(tx)` (lines 100-118)**
   - Added check for dict envelope with `body` key
   - Extracts `gasLimit` or `gas_limit` from `body` dict (CLI uses "gasLimit")
   - Maintains backward compatibility with Tx dataclass format

4. **`_tx_chain_id(tx)` (lines 121-142)**
   - Added check for dict envelope with `body` key
   - Extracts `chainId` or `chain_id` from `body` dict (CLI uses "chainId")
   - Maintains backward compatibility with Tx dataclass format

**File: `rpc/tests/test_cli_envelope_dict_body.py` (NEW)**

Created comprehensive regression test suite:

1. **`_build_cli_style_envelope()`**
   - Helper function that constructs transaction in exact CLI format
   - Uses Dilithium3 PQ signatures (same as CLI default)
   - Creates envelope: `{"body": {...flat fields...}, "sig": {...}}`

2. **`test_cli_dict_envelope_with_flat_body_is_accepted()`**
   - Regression test that verifies CLI-style envelope is accepted
   - Confirms transaction appears in mempool (no "missing sender or nonce" error)

3. **`test_cli_dict_envelope_appears_in_block_template()`**
   - Verifies CLI-style transactions appear in miner block templates
   - Confirms end-to-end flow works correctly

### Why This Fix Works

The mempool service helper functions now support **both** transaction formats:

1. **Tx dataclass format** (existing SDK, internal use):
   ```python
   tx.unsigned.sender, tx.unsigned.nonce, etc.
   ```

2. **Dict envelope format** (CLI, SDK's pack_signed()):
   ```python
   tx["body"]["from"], tx["body"]["nonce"], etc.
   ```

This dual-path approach:
- ✅ Fixes the CLI bug immediately
- ✅ Maintains backward compatibility with all existing code
- ✅ Aligns with the SDK's envelope format
- ✅ Requires no changes to CLI tx encoding
- ✅ Follows the documented RPC API structure

### Testing

**Regression Test**: `rpc/tests/test_cli_envelope_dict_body.py`

The test creates a transaction using the exact same format as the CLI:
- Flat body dict with "from", "to", "nonce", "gasLimit", etc.
- Signature envelope with "pk" (not "pubkey")
- Domain "tx", prehash "sha3-512"
- Canonical CBOR encoding

The test verifies:
1. Transaction is accepted without "missing sender or nonce" error
2. Transaction appears in `mempool.getPending()`
3. Transaction appears in `miner.getBlockTemplate()`

**Manual Testing Command**:
```bash
# After fix, this command should work:
animica miner mine-blocks --address <ADDR> --count 5
animica tx send --from <FROM> --to <TO> --value 17

# Verify transaction appears in mempool:
animica mempool list

# Mine block to include transaction:
animica miner mine-blocks --address <ADDR> --count 1
```

### Potential Future Improvements

1. **Normalize other code paths**: Several other files use `getattr(tx, "sender")` and `getattr(tx, "nonce")` patterns. While they may not be hit by CLI transactions, they could benefit from the same dict envelope support.

2. **Standardize envelope format**: Consider standardizing on a single envelope format across CLI, SDK, and RPC to avoid dual-path complexity.

3. **Add envelope format documentation**: Document the two supported envelope formats in RPC API specifications.
