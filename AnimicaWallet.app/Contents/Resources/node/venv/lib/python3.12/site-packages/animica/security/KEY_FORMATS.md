# Dilithium3/ML-DSA-65 Key Format Documentation

## Overview

This document describes the key formats used for Dilithium3 (ML-DSA-65) signatures in Animica, including normalization rules for backward compatibility between different implementations.

## Algorithm: Dilithium3 (ML-DSA-65)

Dilithium3 is the NIST ML-DSA-65 (Module-Lattice-Based Digital Signature Algorithm) standard as defined in FIPS 204.

### Key and Signature Sizes

| Component | Canonical Size (FIPS 204) | Notes |
|-----------|---------------------------|-------|
| Public Key (pk) | 1952 bytes | Standard across implementations |
| Secret Key (sk) | 4000 bytes | FIPS 204 standard |
| Signature (sig) | 3293 bytes (max) | Variable, up to this maximum |

## Secret Key Format Variations

### 1. Canonical Format (4000 bytes) - FIPS 204

The **canonical** format follows FIPS 204 exactly: **4000 bytes**.

- Used by: Pure-Python reference implementations (animica._vendor.dilithium_py)
- Storage: This is the preferred format for new wallets
- Structure: Contains seed, polynomial coefficients, and internal state as per FIPS 204 section 5

### 2. Legacy Format (4032 bytes) - liboqs Extended

The **legacy** format includes 32 extra bytes: **4032 bytes**.

- Used by: liboqs library (both < 0.15.0 Dilithium3 and >= 0.15.0 ML-DSA-65)
- Origin: liboqs adds 32 bytes of metadata/padding for internal bookkeeping
- Structure: 
  - Bytes 0-4031: Same content as canonical format + 32 bytes metadata
  - The extra 32 bytes are at the **end** of the key material
- Backward compatibility: Existing wallets created with liboqs contain 4032-byte keys

## Wallet Storage Format

### Current Storage (wallets.json)

Wallets are stored in `~/.animica/wallets.json` with the following structure:

```json
{
  "version": 1,
  "wallets": [
    {
      "label": "my-wallet",
      "address": "anim1...",
      "alg_id": 4097,
      "alg_name": "dilithium3",
      "public_key_hex": "...",
      "secret_key_hex": "...",
      "created_at": "2024-..."
    }
  ]
}
```

- **secret_key_hex**: Hex-encoded secret key
  - Legacy wallets (created with liboqs): 8064 hex chars = 4032 bytes
  - New wallets (created with pure-Python): 8000 hex chars = 4000 bytes
  - Both formats are supported via normalization

## Key Normalization Rules

### For Signing Operations

When a secret key is loaded for signing, the following normalization is applied:

1. **If length == 4000 bytes** → Use as-is (canonical)
2. **If length == 4032 bytes** → Strip last 32 bytes to get canonical 4000 bytes (legacy liboqs)
3. **Any other length** → **Raise ValueError** with clear error message

### Normalization Function Location

- Primary implementation: `pq/py/sign.py:_normalize_dilithium3_sk()`
- Applied automatically in: `_backend_sign()` before calling algorithm backend
- No action required by callers; normalization is transparent

### Rationale for Stripping Last 32 Bytes

The liboqs library stores the extra 32 bytes at the **end** of the secret key. Testing and analysis shows:

1. The first 4000 bytes of a liboqs 4032-byte key match the FIPS 204 canonical structure
2. The last 32 bytes are liboqs-specific metadata/padding
3. Stripping the last 32 bytes produces a valid FIPS 204-compliant secret key
4. Signatures produced with the normalized key are valid and verify correctly

## Signer Expectations

### Pure-Python Backend (animica._vendor.dilithium_py)

- **Expects**: Exactly 4000 bytes
- **Behavior**: Raises `ValueError` if length != 4000
- **Used by**: Default signing path when liboqs is unavailable

### liboqs Backend (pq/py/algs/oqs_backend.py)

- **Accepts**: 4032 bytes (native)
- **Behavior**: Uses key as-is if 4032 bytes
- **Note**: When using pure-Python backend with liboqs-generated keys, normalization is required

## Migration Strategy

### For New Wallets

- Generated keys are stored in canonical 4000-byte format
- Uses pure-Python implementation by default
- Ensures forward compatibility

### For Existing Wallets

- Legacy 4032-byte keys continue to work via automatic normalization
- No migration required
- Users can optionally recreate wallets to use canonical format

### Detecting Wallet Issues

If a wallet is corrupted or has an invalid key length:

```
ValueError: Invalid dilithium3 secret key length 3999; expected 4000 or legacy 4032.
Wallet may be corrupted. Run: animica wallet doctor
```

Recommended action: Use `animica wallet create` to generate a new wallet.

## Implementation Notes

### Domain Separation

All signing operations use domain-separated prehashing (see `pq/py/sign.py`):

1. Canonical SignBytes construction with domain, chain_id, alg_id, context, message
2. SHA3-512 prehash to 64-byte digest
3. Sign the digest with normalized secret key

This ensures consistent behavior across key formats.

### Logging

When legacy key normalization occurs, a debug log message is emitted:

```
DEBUG: Normalizing legacy Dilithium3 secret key: 4032 → 4000 bytes
```

This helps with troubleshooting but does not leak key material.

## References

- **FIPS 204**: Module-Lattice-Based Digital Signature Standard (ML-DSA)
  - https://csrc.nist.gov/pubs/fips/204/final
- **liboqs**: Open Quantum Safe library
  - https://github.com/open-quantum-safe/liboqs
- **Animica PQ Spec**: `spec/pq/` (repository-specific)
- **Domain Separation**: `spec/domains.yaml`

## Version History

- **2024-12-13**: Initial documentation
  - Documented 4000 vs 4032 byte secret key formats
  - Defined normalization rules for backward compatibility
  - Clarified wallet storage and migration strategy
