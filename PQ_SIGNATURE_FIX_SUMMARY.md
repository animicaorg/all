# PQ Signature Verification Fix Summary

## Problem

The CLI/SDK and node had mismatched PQ signature signing and verification paths, causing transactions to fail with error `-32012 Invalid post-quantum signature: verification failed` when using `animica tx send --chain-id 1`.

### Root Cause Analysis

The mismatch occurred at three levels:

1. **Domain separation mismatch**: 
   - SDK was signing with `domain="generic"` (or None)
   - Node was verifying with `domain="tx/sign"`

2. **Double-domaining issue**:
   - Node's `_sign_bytes()` was calling `canonical.tx_sign_bytes()` which adds domain="animica/tx/sign/v1"
   - Then passing this to `pq.verify.verify_detached()` which adds ANOTHER layer of domain separation
   - This resulted in double-wrapping the message

3. **Missing chain_id in signature**:
   - SDK was not passing `chain_id` to the signing function
   - Node verification was not extracting or checking `chain_id`

## Solution

### 1. SDK Changes (`sdk/python/omni_sdk/`)

#### `tx/encode.py:sign_bytes()`
- **Before**: Returned raw CBOR of body dict
- **After**: Still returns raw CBOR of body dict (no change needed)
- **Reason**: The PQ layer will add domain separation, so we pass raw message

#### `wallet/signer.py:PQSigner.sign_tx()`
- **Added**: New method specifically for signing transactions
- **Implementation**: Calls `pq.sign.sign_detached(msg, alg, sk, domain="tx", chain_id=chain_id)`
- **Benefit**: Properly includes domain and chain_id in signature construction

### 2. CLI Changes (`python/animica/cli/tx.py`)

#### `send()` command
- **Changed**: Use `signer.sign_tx(msg, chain_id)` instead of `signer.sign(msg)`
- **Added**: Verbose debug output with `-v` flag showing:
  - Algorithm name and ID
  - Public key length
  - Signature length
  - Message length and prefix
  - Chain ID

### 3. Node Changes (`rpc/methods/tx.py`)

#### `_sign_bytes()`
- **Before**: Called `canonical.tx_sign_bytes()` which added domain wrapping
- **After**: Extracts body from envelope and returns raw CBOR
- **Reason**: Avoid double-domaining when passing to `pq.verify.verify_detached()`

#### `_verify_pq_signature()`
- **Changed**: Domain from `"tx/sign"` to `"tx"` (matches SDK)
- **Added**: Extract and pass `chain_id` to verification
- **Added**: Debug logging showing verification parameters
- **Fixed**: Pass `chain_id` to `verify_detached()`

## Architecture

### Correct Signing Flow

```
1. CLI builds tx → canonical body dict
2. SDK encodes body → CBOR bytes (raw message)
3. SDK calls signer.sign_tx(msg, chain_id)
4. PQSigner calls pq.sign.sign_detached(msg, alg, sk, domain="tx", chain_id=chain_id)
5. PQ layer builds canonical SignBytes:
   TAG="animica:sign/v1" || DOMAIN="tx" || CHAIN_ID || ALG_ID || MESSAGE
6. PQ layer prehashes with SHA3-512
7. PQ layer signs the prehash digest
8. Returns raw signature bytes
```

### Correct Verification Flow

```
1. Node receives CBOR envelope: {body: {...}, sig: {algId, pubkey, sig}}
2. Node extracts body (raw CBOR) and chain_id
3. Node extracts signature components
4. Node calls verify_detached(msg=body_cbor, sig_env, pk, chain_id=chain_id)
5. PQ verify rebuilds same canonical SignBytes:
   TAG="animica:sign/v1" || DOMAIN="tx" || CHAIN_ID || ALG_ID || MESSAGE
6. PQ verify prehashes with SHA3-512
7. PQ verify checks signature against prehash digest
```

### Key Consistency Points

- **Same message**: Both sign and verify use raw CBOR of body dict
- **Same domain**: Both use `domain="tx"`
- **Same chain_id**: Both include chain_id in SignBytes construction
- **Same prehash**: Both use SHA3-512
- **Same algorithm**: algId is consistently passed through

## Testing

Added comprehensive tests to ensure round-trip correctness:

### SDK Tests (`sdk/python/tests/test_pq_signature_roundtrip.py`)

1. `test_pq_signer_sign_tx_with_chain_id` - Verify sign_tx method works
2. `test_sdk_sign_bytes_returns_cbor_body` - Verify sign_bytes format
3. `test_node_verification_matches_sdk_signature` - **KEY TEST**: Round-trip verification
4. `test_node_verification_rejects_flipped_signature` - Security test
5. `test_node_verification_rejects_wrong_chain_id` - Chain ID validation
6. `test_packed_signed_envelope_has_required_fields` - Envelope structure

### RPC Tests (`rpc/tests/test_tx_pq_signatures.py`)

1. `test_sendRawTransaction_accepts_valid_pq_signature` - Happy path
2. `test_sendRawTransaction_rejects_tampered_signature` - Security test
3. `test_sendRawTransaction_rejects_wrong_chain_id` - Chain ID validation
4. `test_sendRawTransaction_requires_sig_field` - Envelope validation

## Files Changed

1. `sdk/python/omni_sdk/wallet/signer.py` - Added `sign_tx()` method
2. `sdk/python/omni_sdk/tx/encode.py` - Updated `sign_bytes()` docstring
3. `python/animica/cli/tx.py` - Use `sign_tx()`, add verbose debug
4. `rpc/methods/tx.py` - Fix `_sign_bytes()`, update `_verify_pq_signature()`
5. `sdk/python/tests/test_pq_signature_roundtrip.py` - New test file
6. `rpc/tests/test_tx_pq_signatures.py` - New test file

## Verification

To verify the fix works:

```bash
# 1. Build and sign a transaction
animica tx send --from alice --to anim1dest --value 1.0 --dry-run -v

# Expected verbose output:
# PQ SIGNATURE DEBUG
#   algorithm: dilithium3 (id=1)
#   pubkey_len: 1952 bytes
#   sig_len: 2420 bytes
#   message_len: 82 bytes
#   message_prefix: a867636861696e4964...
#   chain_id: 1

# 2. Broadcast transaction
animica tx send --from alice --to anim1dest --value 1.0

# Expected: Transaction hash, no -32012 error
# Tx Hash: 0x...
# ✓ Transaction broadcast successfully
```

## Benefits

1. **Consistency**: SDK and node use identical signing/verification paths
2. **Security**: Proper domain separation prevents cross-protocol signature reuse
3. **Chain ID enforcement**: Signatures are bound to specific chain IDs
4. **Debuggability**: Verbose mode shows signature details
5. **Testability**: Comprehensive tests ensure correctness
6. **Maintainability**: Clear separation between layers (SDK → PQ → Backend)
