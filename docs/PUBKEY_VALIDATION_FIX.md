# PQ Signature Public Key Validation Fix

## Problem

Transactions were being rejected with the error:
```
Invalid post-quantum signature: verification failed
scheme_id: 4098
pubkey_len: 32
sig_len: 7856
```

## Root Cause

The transaction signature contained a **32-byte address hash instead of the full 64-byte public key**.

### Background

In Animica's PQ cryptography system:
- **Addresses** are derived from public keys using: `alg_id(2 bytes) || sha3_256(pubkey)(32 bytes)`
- The 32-byte hash is used for address encoding (bech32m format)
- **Signatures** must contain the **full public key**, not the address hash

#### Public Key Sizes by Algorithm

| Algorithm | ID (hex) | ID (decimal) | Public Key Size | Signature Size |
|-----------|----------|--------------|-----------------|----------------|
| Dilithium3 | 0x1001 | 4097 | 1952 bytes | 3293 bytes |
| SPHINCS+ SHAKE-128s | 0x1002 | 4098 | 64 bytes | 7856 bytes |

The error showed `pubkey_len: 32` for scheme_id 4098 (SPHINCS+), which should have been 64 bytes.

## Solution

Added validation in `core/utils/tx.py` to check that public key sizes match algorithm requirements:

1. **New validation function**: `_validate_pubkey_size(alg_id, pubkey)`
   - Validates pubkey size matches expected size for the algorithm
   - Provides helpful error messages when 32-byte address hashes are detected
   - Raises `TxNormalizationError` with clear diagnostics

2. **Integration point**: `_normalize_sig_entry()`
   - Called during transaction normalization (from CBOR, JSON, or dict formats)
   - Catches invalid pubkeys before they reach signature verification
   - Provides early, clear error messages

3. **Error handling**: Special case detection
   - If a 32-byte value is found where a larger pubkey is expected
   - Error message specifically mentions "address hash" and suggests using full pubkey
   - Helps developers quickly identify and fix the issue

## How to Fix Affected Code

### If You're Sending Transactions

Ensure your transaction signature includes the **full public key**, not an address hash:

```python
# ❌ WRONG: Using address hash
sig = {
    "alg": 4098,  # SPHINCS+
    "pubkey": address_hash,  # 32 bytes - WRONG!
    "sig": signature_bytes,
}

# ✅ CORRECT: Using full public key
sig = {
    "alg": 4098,  # SPHINCS+
    "pubkey": full_public_key,  # 64 bytes for SPHINCS+
    "sig": signature_bytes,
}
```

### Common Mistakes

1. **Extracting pubkey from address**: Addresses only contain a hash of the pubkey, not the full key
   ```python
   # ❌ WRONG
   rec = decode_address("anim1...")
   pubkey = rec.digest  # This is only the hash!
   ```

2. **Using keystore digest field**: Some keystores store both full pubkey and address hash
   ```python
   # ❌ WRONG
   pubkey = wallet["address_hash"]  # 32 bytes
   
   # ✅ CORRECT
   pubkey = wallet["public_key"]  # Full size
   ```

3. **Truncating pubkey**: Never truncate public keys
   ```python
   # ❌ WRONG
   pubkey = full_pubkey[:32]  # Truncation breaks signatures
   ```

## Testing

### Unit Tests

Created comprehensive tests in `core/utils/test_tx_pubkey_validation.py`:
- Test rejection of 32-byte address hashes for SPHINCS+
- Test rejection of 32-byte address hashes for Dilithium3
- Test acceptance of correct-sized pubkeys
- Test handling of unknown algorithm IDs

### Manual Verification

```python
from core.utils.tx import normalize_tx_envelope
from core.encoding.cbor import dumps as cbor_dumps

# This will now raise TxNormalizationError with helpful message
bad_sig = {
    "alg": 4098,
    "pubkey": b"\x01" * 32,  # Too short!
    "sig": b"\x02" * 7856,
}
envelope = {"tx": {...}, "sigs": [bad_sig]}
normalize_tx_envelope(cbor_dumps(envelope))  # Raises error
```

## Migration Notes

### Backward Compatibility

- **Unknown algorithms**: Validation is skipped for unknown algorithm IDs
- **Legacy tests**: Updated all existing tests to use correct pubkey sizes
- **Error messages**: Clear and actionable, helping developers quickly identify issues

### Updated Test Files

Fixed pubkey sizes in:
- `rpc/tests/test_tx_send_pq_optional.py`
- `rpc/tests/test_tx_chainid2_inclusion.py`
- `p2p/tests/test_tx_gossip_integration.py`
- `tests/test_nonce_mainnet_scenario.py`

## Verification

After this fix:
1. Transactions with incorrect pubkey sizes are rejected at normalization
2. Clear error messages guide developers to the fix
3. Valid transactions pass through unchanged
4. No performance impact (validation is O(1) dictionary lookup)

## Related Documentation

- `spec/alg_policy.yaml` - Algorithm sizes and metadata
- `pq/alg_ids.yaml` - Canonical algorithm IDs
- `pq/py/registry.py` - Runtime algorithm registry
- `pq/py/address.py` - Address encoding (why addresses are 32 bytes)
