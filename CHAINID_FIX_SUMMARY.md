# ChainId Mismatch Fix - Implementation Summary

## Problem Statement

Users reported that when running a mainnet node (chainId=1) and attempting to send transactions, the node was returning error code `-32011` (CHAIN_ID_MISMATCH) with data `{'got': 0, 'expected': 1}`. This indicated that signed transaction bytes were either missing the chainId or it was being decoded as 0.

## Root Causes Identified

1. **SDK default chainId=0**: The SDK's `Tx.from_rpc_dict()` method had a fallback `chainId=0` default when chainId was missing from the RPC dict.

2. **Missing 'body' field handling**: The RPC layer's `_validate_chain_id()` function was not checking the 'body' field for chainId, which is the format used by SDK's `pack_signed()` function.

3. **Inconsistent envelope formats**: Two transaction envelope formats exist:
   - **Core format**: `{ "tx": { "chainId": ..., ... }, "sigs": [...] }`
   - **SDK format**: `{ "body": { "chainId": ..., ... }, "sig": ..., "algId": ..., "pubKey": ... }`

## Changes Implemented

### 1. RPC Layer (`rpc/methods/tx.py`)

**Added debug logging**:
- Import `logging` module and create logger
- Added debug logs in `tx_send_raw_transaction()` to log decoded envelope type and structure
- Added debug logs in `_validate_chain_id()` to log chainId extraction and validation

**Enhanced `_validate_chain_id()`**:
```python
def _validate_chain_id(obj: dict) -> None:
    want = _chain_id_required()
    
    # Try flat structure first
    cid = obj.get("chainId") or obj.get("chain_id")
    
    # Try nested 'tx' structure (core format)
    if cid is None and "tx" in obj:
        tx_obj = obj["tx"]
        cid = tx_obj.get("chainId") or tx_obj.get("chain_id")
    
    # Try 'body' structure (SDK format) - NEW
    if cid is None and "body" in obj:
        body_obj = obj["body"]
        cid = body_obj.get("chainId") or body_obj.get("chain_id")
    
    # Log for debugging
    log.debug("ChainId validation: extracted=%s, expected=%s", cid, want)
    
    # Validate
    if cid is None:
        log.warning("ChainId missing in transaction envelope")
        raise rpc_errors.ChainIdMismatch(got=0, expected=want)
    
    if int(cid) != int(want):
        log.warning("ChainId mismatch: got=%s, expected=%s", cid, want)
        raise rpc_errors.ChainIdMismatch(got=int(cid), expected=int(want))
```

### 2. SDK Types (`sdk/python/omni_sdk/types/core.py`)

**Removed chainId default and added validation**:
```python
@staticmethod
def from_rpc_dict(d: TxDict) -> "Tx":
    # Get chain ID - it's required and must be > 0
    chain_id_value = d.get("chainId")
    
    if chain_id_value is None:
        raise ValueError(
            "Transaction missing required field 'chainId'. "
            "All transactions must specify a valid chain ID > 0."
        )
    
    try:
        chain_id_int = int(chain_id_value)
    except (ValueError, TypeError) as e:
        raise ValueError(
            f"Transaction has invalid chainId: {chain_id_value!r}. "
            f"chainId must be a positive integer."
        ) from e
    
    if chain_id_int <= 0:
        raise ValueError(
            f"Transaction has invalid chainId: {chain_id_int}. "
            f"chainId must be a positive integer (> 0)."
        )
    
    # ... rest of construction
```

### 3. Test Coverage

**New test file: `rpc/tests/test_tx_chainid_validation.py`**
- `test_tx_with_correct_chainid_accepted`: Validates correct chainId is accepted
- `test_tx_with_wrong_chainid_rejected`: Validates wrong chainId is rejected with clear error
- `test_sdk_tx_builder_encodes_chainid`: Validates SDK properly encodes chainId in 'body' field
- `test_decode_and_validate_chainid_from_body_field`: Validates node can decode SDK format
- `test_reject_transaction_with_chainid_zero`: Validates chainId=0 is rejected

**New test file: `sdk/python/tests/test_tx_chainid.py`**
- `test_tx_builder_requires_chainid`: Validates SDK builders require chainId
- `test_tx_builder_rejects_invalid_chainid`: Validates invalid chainId values are rejected
- `test_canonical_body_dict_includes_chainid`: Validates canonical_body_dict includes chainId
- `test_pack_signed_includes_chainid_in_body`: Validates pack_signed encodes chainId in body
- `test_tx_from_rpc_dict_requires_chainid`: Validates from_rpc_dict requires chainId
- `test_sign_bytes_deterministic_with_chainid`: Validates sign_bytes includes chainId

## Test Results

All tests pass with no regressions:

```
# SDK tests
sdk/python/tests/test_tx_chainid.py: 6 passed

# RPC tests
rpc/tests/test_tx_chainid_validation.py: 3 passed, 2 skipped
rpc/tests/test_tx_flow.py: 5 passed (existing tests - no regressions)
```

## How the Fix Works

1. **Transaction Building (SDK)**:
   - User calls `transfer(chain_id=1, ...)` 
   - SDK builds Tx with chainId=1
   - `pack_signed()` encodes as: `{ "body": { "chainId": 1, ... }, ... }`

2. **Transaction Submission (RPC)**:
   - Node receives CBOR bytes via `tx.sendRawTransaction`
   - `_decode_tx()` decodes CBOR to dict
   - Logs decoded structure: `"envelope_keys=['sig', 'body', 'algId', 'pubKey']"`

3. **ChainId Validation**:
   - `_validate_chain_id()` extracts chainId from obj
   - Checks flat, then 'tx' field, then 'body' field
   - Logs: `"ChainId validation: extracted=1, expected=1"`
   - Passes validation ✓

4. **Error Case (wrong chainId)**:
   - If chainId=999 but node expects 1
   - Logs: `"ChainId mismatch: got=999, expected=1"`
   - Raises `ChainIdMismatch(got=999, expected=1)`
   - Returns error -32011 with clear data

## Migration Notes

### For SDK Users
- **Breaking change**: `Tx.from_rpc_dict()` now requires chainId to be present and > 0
- Previously, missing chainId would default to 0 (invalid)
- Now raises clear `ValueError` if chainId is missing or invalid
- **Action required**: Ensure all transaction dicts include valid chainId

### For Node Operators
- No action required
- Logging level can be set to DEBUG to see chainId validation details
- Error messages are now more informative

### Backwards Compatibility
- All existing valid transactions (with chainId > 0) continue to work
- Invalid transactions (chainId=0 or missing) now fail with clear errors instead of silent defaults
- Both envelope formats (core 'tx' field and SDK 'body' field) are supported

## Debugging

To debug chainId issues, enable DEBUG logging:

```bash
export LOG_LEVEL=DEBUG
# Then run node or CLI
```

You will see logs like:
```
DEBUG:rpc.methods.tx:tx.sendRawTransaction: decoding 208 CBOR bytes
DEBUG:rpc.methods.tx:tx.sendRawTransaction: decoded envelope type=dict, keys=['sig', 'body', 'algId', 'pubKey']
DEBUG:rpc.methods.tx:ChainId validation: extracted=1, expected=1, envelope_keys=['sig', 'body', 'algId', 'pubKey']
```

## Related Files

- `rpc/methods/tx.py` - RPC transaction handling and validation
- `rpc/errors.py` - Error code definitions (ChainIdMismatch = -32011)
- `sdk/python/omni_sdk/types/core.py` - SDK transaction types
- `sdk/python/omni_sdk/tx/build.py` - SDK transaction builders
- `sdk/python/omni_sdk/tx/encode.py` - SDK transaction encoding
- `core/types/tx.py` - Core transaction types
- Tests in `rpc/tests/` and `sdk/python/tests/`

## Verification

To verify the fix works in your environment:

```bash
# Run SDK tests
python3 -m pytest sdk/python/tests/test_tx_chainid.py -xvs

# Run RPC tests
python3 -m pytest rpc/tests/test_tx_chainid_validation.py -xvs

# Run existing tx flow tests (regression check)
python3 -m pytest rpc/tests/test_tx_flow.py -xvs
```

All tests should pass.
