# Animica Post-Quantum Cryptography Discovery

**Date:** 2026-02-11  
**Purpose:** Document the authoritative custom PQ implementation in Animica for browser wallet + Dapp IDE integration.

## ⚠️ HARD RULE: NO LIBOQS ALLOWED

This document describes the **custom PQ code** implemented in the Animica repository. The browser wallet and Dapp IDE **MUST NEVER** use liboqs, oqs, or any third-party PQ libraries. All PQ operations must use the repo's own implementations.

---

## 1. Signing Algorithms

### Supported Algorithms

| Algorithm | Alg ID | Public Key | Secret Key | Signature | Security Level | Status |
|-----------|--------|------------|------------|-----------|----------------|--------|
| **Dilithium3** | `0x1001` | 1952 bytes | 4000 bytes | 3293 bytes | 128-bit | ✅ Preferred |
| **SPHINCS+ SHAKE-128s** | `0x1002` | 64 bytes | 64 bytes | 7856 bytes | 128-bit | ✅ Fallback |
| **Kyber768 (KEM)** | `0x2001` | 1184 bytes | 2400 bytes | CT: 1088 B, SS: 32 B | 128-bit | ⚠️ P2P only |

**Registry:** `pq/py/registry.py`

```python
# Get algorithm info
from pq.py.registry import get_sig, default_signature_alg, ALG_ID, ALG_NAME

alg_info = get_sig("dilithium3")  # or 0x1001
# alg_info.alg_id == 0x1001
# alg_info.name == "dilithium3"
# alg_info.pk_len == 1952
# alg_info.sk_len == 4000
# alg_info.sig_len == 3293
```

### Implementation Files

- **Dilithium3 backend:** `pq/py/algs/dilithium3.py`
  - Pure-Python implementation (no liboqs)
  - Imported from: `python/animica/_vendor/dilithium_py/dilithium3.py`
  - Functions: `keypair(seed=None)`, `sign(sk, msg, pk=None)`, `verify(pk, msg, sig)`

- **SPHINCS+ backend:** `pq/py/algs/sphincs_shake_128s.py`
  - Pure-Python implementation
  - Functions: `keypair(seed=None)`, `sign(sk, msg, pk=None)`, `verify(pk, msg, sig)`

- **Kyber768 backend:** `python/animica/_vendor/kyber_py/kyber768.py`
  - Pure-Python KEM implementation
  - Used for P2P handshakes (wallet likely doesn't need this)

---

## 2. Key Generation

**Location:** `pq/py/keygen.py`

```python
from pq.py.keygen import keygen_sig

# Generate a keypair
keypair = keygen_sig("dilithium3")  # or 0x1001

# Returns KeyPair dataclass:
# - keypair.alg_id: int (0x1001)
# - keypair.alg_name: str ("dilithium3")
# - keypair.public_key: bytes (1952 bytes)
# - keypair.secret_key: bytes (4000 bytes)
# - keypair.address: str (bech32m address, derived automatically)
```

### Key Format Notes

**Dilithium3:**
- **Canonical format:** 4000 bytes (used by pure-Python)
- **Legacy format:** 4032 bytes (from liboqs, includes 32-byte metadata)
- The signing code automatically normalizes 4032→4000 bytes via `_normalize_dilithium3_sk()`

**SPHINCS+:**
- Both public and secret keys are 64 bytes
- No format variations

---

## 3. Address Derivation

**Location:** `pq/py/address.py`

### Animica Address Format

```
Address = bech32m_encode(
    hrp="anim",
    data=convertbits(
        alg_id.to_bytes(2, "big") + sha3_256(pubkey),
        from_bits=8,
        to_bits=5
    )
)
```

**Components:**
- **HRP:** `"anim"` (human-readable part)
- **Payload:** 34 bytes = 2-byte alg_id (big-endian) + 32-byte SHA3-256 digest
- **Encoding:** bech32m (not bech32)
- **Hash function:** SHA3-256 (Keccak-256, **not** SHA-256)

### Public API

```python
from pq.py.address import address_from_pubkey, decode_address

# Derive address from public key
address = address_from_pubkey(
    pubkey=public_key_bytes,
    alg_id=0x1001,
    hrp="anim"
)
# Example: "anim1qq9a7x3k4l2m5n8p9r2s3t4v5w6x7y8z9a0b1c2d3e4f5g6h7j8"

# Parse address back to components
record = decode_address(address)
# record.hrp == "anim"
# record.alg_id == 0x1001
# record.digest == sha3_256(pubkey)  # 32 bytes
```

### Address Validation

```python
from pq.py.address import validate_address

# Returns True or raises AddressError
validate_address(
    addr="anim1qq...",
    expect_hrp="anim",
    allowed_alg_ids={0x1001, 0x1002}
)
```

---

## 4. Message Signing (Domain-Separated)

**Location:** `pq/py/sign.py`

### Signing API

```python
from pq.py.sign import sign_detached, Signature

# Sign a message with domain separation
sig_envelope = sign_detached(
    msg=message_bytes,
    alg="dilithium3",  # or 0x1001
    sk=secret_key_bytes,
    domain="tx/sign",  # MANDATORY: prevents cross-domain attacks
    chain_id=1337,     # Optional but recommended for transactions
    fork_id=None,      # Optional
    context=b"",       # Optional domain-specific data
    prehash="sha3-512" # "sha3-512" | "sha3-256"
)

# Returns Signature dataclass:
# - sig_envelope.alg_id: int
# - sig_envelope.alg_name: str
# - sig_envelope.domain: str
# - sig_envelope.prehash: str
# - sig_envelope.sig: bytes (3293 bytes for Dilithium3)
```

### Canonical SignBytes Construction

The signing process creates a **canonical SignBytes** before signing:

```python
from pq.py.sign import build_sign_bytes

sign_bytes = build_sign_bytes(
    msg=message_bytes,
    domain="tx/sign",
    chain_id=1337,
    fork_id=None,
    alg_id=0x1001,
    context=b"",
    prehash="sha3-512"
)

# SignBytes structure (before prehashing):
# TAG = "animica:sign/v1"
# 
# sign_bytes_raw =
#     len(TAG)||TAG
#   ||len(DOMAIN)||DOMAIN
#   ||len(CHAIN_ID_enc)||CHAIN_ID_enc  (uvarint, optional)
#   ||len(FORK_ID_enc)||FORK_ID_enc    (uvarint, optional)
#   ||len(ALG_ID_enc)||ALG_ID_enc      (uvarint)
#   ||len(CONTEXT)||CONTEXT
#   ||len(MESSAGE)||MESSAGE
#
# All lengths are LEB128 uvarints.
# Final sign_bytes = SHA3-512(sign_bytes_raw)
```

**Critical:** The wallet MUST use the same domain, chain_id, and prehash as the node expects, or verification will fail.

### Common Domains

| Domain | Purpose |
|--------|---------|
| `"tx"` or `"animica.tx.v1"` | Transaction signing |
| `"p2p/identity"` | P2P node identity |
| `"header/proposer"` | Block header signing |
| `"da/receipt"` | Data availability receipts |

---

## 5. Wallet Format (wallets.json)

**Location:** `python/animica/cli/wallet.py` (lines 108-151)

### Schema

```json
{
  "version": 1,
  "wallets": [
    {
      "label": "my-wallet",
      "address": "anim1qq9a7x3k4l2m5n8p9r2s3t4v5w6x7y8z9a0b1c2d3e4f5g6h7j8",
      "alg_id": 4097,
      "alg_name": "dilithium3",
      "public_key_hex": "abcd1234567890abcdef...",
      "secret_key_hex": "fedcba9876543210abcd...",
      "created_at": "2026-02-11T21:00:00.000000+00:00"
    }
  ]
}
```

### WalletEntry Fields

| Field | Type | Description |
|-------|------|-------------|
| `label` | string | Human-readable wallet name (e.g., "premine", "Alice") |
| `address` | string | Bech32m address (starts with "anim1") |
| `alg_id` | integer | Algorithm ID (4097 = 0x1001 for Dilithium3) |
| `alg_name` | string | Algorithm name ("dilithium3" or "sphincs_shake_128s") |
| `public_key_hex` | string | Hex-encoded public key (no "0x" prefix) |
| `secret_key_hex` | string | Hex-encoded secret key (no "0x" prefix) |
| `created_at` | string | ISO8601 timestamp with timezone (UTC) |

### File Location

- **Default:** `~/.animica/wallets.json`
- **Environment override:** `ANIMICA_WALLETS_FILE`

### Wallet Lookup

Wallets can be identified by:
- Address: `"anim1qq..."`
- Label: `"my-wallet"`
- Public key hex: `"abcd1234..."`

---

## 6. Transaction Signing

**Location:** `python/animica/tx/signing.py`

### Transaction SignBytes

For transactions, the signing preimage is **CBOR-encoded**:

```python
from python.animica.tx.signing import tx_signing_preimage, ChainContext

ctx = ChainContext(
    chain_id=1337,
    genesis_hash=b"...",  # Optional
    network="devnet",
    fork_id=None,
    domain="animica.tx.v1",
    prehash="sha3-512"
)

preimage_bytes = tx_signing_preimage(
    tx=unsigned_tx_dict,
    ctx=ctx,
    domain="animica.tx.v1",
    message_type="tx"
)

# preimage_bytes is CBOR-encoded map:
# {
#   1: "animica.tx.v1",  # domain
#   2: 1337,             # chain_id
#   3: b"...",           # genesis_hash (optional)
#   4: "devnet",         # network
#   5: "tx",             # message_type
#   6: 1,                # tx_version
#   7: {                 # tx_body (normalized, sigs removed)
#       "from": "anim1...",
#       "to": "anim1...",
#       "value": 1000000000000000000,
#       "nonce": 42,
#       "gasLimit": {...},
#       "data": b"..."
#   }
# }
```

### Transaction Normalization

Before signing, the transaction body is normalized:
1. Remove signature fields
2. Normalize "from"/"to" addresses to canonical form
3. Convert "data" hex strings to bytes
4. Coerce numeric fields to proper types

**Function:** `core.utils.tx.normalize_tx_body(tx)` (if available)

### Sign Transaction

```python
from python.animica.tx.signing import pq_sign_tx

signature = pq_sign_tx(
    tx=unsigned_tx_dict,
    privkey=secret_key_bytes,
    pubkey=public_key_bytes,
    alg_id=0x1001,
    ctx=chain_ctx
)

# Returns Signature envelope (same as sign_detached)
```

### Transaction Hash

```python
from python.animica.tx.signing import tx_sign_hash

sign_hash = tx_sign_hash(tx=unsigned_tx_dict, ctx=chain_ctx)
# sign_hash == SHA3-512(tx_signing_preimage(...))
```

---

## 7. Signature Verification

**Location:** `pq/py/sign.py` + `python/animica/tx/signing.py`

### Generic Verification

```python
from pq.py.sign import verify_detached

is_valid = verify_detached(
    msg=message_bytes,
    sig=signature_envelope,  # Signature dataclass from sign_detached
    pk=public_key_bytes,
    domain="tx/sign",        # Must match signing domain
    chain_id=1337,           # Must match signing chain_id
    fork_id=None,
    context=b"",
    prehash="sha3-512",      # Must match signing prehash
    strict_domain=True,      # Enforce domain match
    strict_prehash=True,     # Enforce prehash match
    strict_alg=True          # Enforce alg_id match
)
# Returns: bool
```

### Transaction Verification

```python
from python.animica.tx.signing import pq_verify_tx

result = pq_verify_tx(
    tx=signed_tx_dict,
    signature=signature_envelope,
    pubkey=public_key_bytes,
    ctx=chain_ctx,
    from_addr="anim1qq..."  # Optional: verify address derivation
)

# Returns VerifyResult dataclass:
# - result.ok: bool
# - result.reason: Optional[str]  # Error reason if ok=False
# - result.sign_hash_hex: str     # SHA3-512 of preimage
# - result.pub_fingerprint: str   # First 16 hex chars of SHA3-256(pk)
# - result.scheme_id: int         # alg_id
```

---

## 8. Browser Integration Plan

### Step 1: Port Pure-Python to Browser

Since the custom PQ implementations are in pure Python, we have two options:

**Option A: TypeScript Port**
- Reimplement Dilithium3/SPHINCS+ in TypeScript
- Match byte-for-byte output with Python implementation
- Validate with golden test vectors

**Option B: WASM Compilation**
- Compile Python implementations to WASM using Pyodide or similar
- Bundle WASM module in wallet extension
- Deterministic execution

### Step 2: Shared Crypto Package

Create `packages/animica-crypto/` with:

```typescript
// packages/animica-crypto/src/index.ts

export interface KeyPair {
  algId: number;
  algName: string;
  publicKey: Uint8Array;
  secretKey: Uint8Array;
  address: string;
}

export interface Signature {
  algId: number;
  algName: string;
  domain: string;
  prehash: string;
  sig: Uint8Array;
}

export function generateKeypair(alg: number | string): KeyPair;
export function sign(alg: number | string, secretKey: Uint8Array, message: Uint8Array, options: SignOptions): Signature;
export function verify(publicKey: Uint8Array, message: Uint8Array, signature: Signature, options: VerifyOptions): boolean;
export function addressFromPubkey(alg: number, publicKey: Uint8Array): string;
export function txSignBytes(tx: any, chainId: number): Uint8Array;
```

### Step 3: Test Vectors

Generate golden test vectors from CLI:

```bash
# Generate test keypair
animica wallet create --label test-vector-dilithium3 --alg dilithium3

# Sign test message
echo -n "test message" | animica sign --wallet test-vector-dilithium3 --domain "test" --output test-sig.json

# Export test vectors
animica wallet export --label test-vector-dilithium3 --output test-vector.json
```

Store in: `packages/animica-crypto/test-vectors/`

### Step 4: Validation Tests

```typescript
import { generateKeypair, sign, verify, addressFromPubkey } from '@animica/crypto';
import dilithium3Vector from './test-vectors/dilithium3.json';

test('dilithium3 matches Python output', () => {
  const { publicKey, secretKey, address } = dilithium3Vector;
  
  // Test address derivation
  const derivedAddress = addressFromPubkey(0x1001, Buffer.from(publicKey, 'hex'));
  expect(derivedAddress).toBe(address);
  
  // Test signing
  const message = Buffer.from('test message', 'utf8');
  const sig = sign(0x1001, Buffer.from(secretKey, 'hex'), message, {
    domain: 'test',
    chainId: 1337
  });
  
  // Test verification
  const isValid = verify(Buffer.from(publicKey, 'hex'), message, sig, {
    domain: 'test',
    chainId: 1337
  });
  expect(isValid).toBe(true);
});
```

---

## 9. Critical Implementation Notes

### ⚠️ Dilithium3 Key Format Handling

The Dilithium3 secret key exists in two formats:
- **Canonical (4000 bytes):** Used by pure-Python implementation
- **Legacy (4032 bytes):** From liboqs (adds 32-byte seed)

The signing code in `pq/py/algs/dilithium3.py` has `_normalize_dilithium3_sk()` that automatically strips the 32-byte suffix when needed. The browser wallet should:
1. Store keys in 4000-byte canonical format
2. Accept 4032-byte keys for import (auto-normalize)
3. Always use 4000-byte format for signing

### ⚠️ Domain Separation

ALL signatures MUST include explicit domain strings. Common domains:
- Transactions: `"animica.tx.v1"` or `"tx"`
- Off-chain messages: `"animica.offchain.v1"`
- Custom domains: Follow convention `"app/feature/version"`

**Never reuse signatures across domains!**

### ⚠️ Hash Functions

- **Address derivation:** SHA3-256 (Keccak-256)
- **Signing prehash:** SHA3-512 (Keccak-512)
- **NOT SHA-256/SHA-512** (these are NIST SHA-2, not Keccak)

### ⚠️ Bech32m vs Bech32

Animica uses **bech32m** (not bech32). Ensure your implementation uses:
- `bech32_encode(hrp, data, spec="bech32m")`
- NOT `bech32_encode(hrp, data, spec="bech32")`

The checksum algorithm differs between bech32 and bech32m.

---

## 10. File Reference

### Core PQ Implementation

| Purpose | File Path |
|---------|-----------|
| Algorithm registry | `pq/py/registry.py` |
| Key generation | `pq/py/keygen.py` |
| Address derivation | `pq/py/address.py` |
| Message signing | `pq/py/sign.py` |
| Bech32m encoding | `pq/py/utils/bech32.py` |
| SHA3 hashing | `pq/py/utils/hash.py` |

### Algorithm Backends

| Algorithm | Implementation |
|-----------|----------------|
| Dilithium3 | `pq/py/algs/dilithium3.py` → `python/animica/_vendor/dilithium_py/dilithium3.py` |
| SPHINCS+ SHAKE-128s | `pq/py/algs/sphincs_shake_128s.py` |
| Kyber768 (KEM) | `python/animica/_vendor/kyber_py/kyber768.py` |

### Transaction Handling

| Purpose | File Path |
|---------|-----------|
| TX signing | `python/animica/tx/signing.py` |
| TX verification | `python/animica/tx/crypto.py` |
| TX normalization | `core/utils/tx.py` (if present) |

### CLI Integration

| Purpose | File Path |
|---------|-----------|
| Wallet management | `python/animica/cli/wallet.py` |
| Transaction sending | `python/animica/cli/tx.py` |
| PQ diagnostics | `python/animica/cli/pq_utils.py` |

---

## 11. Next Steps

### For Browser Wallet Developer

1. **Read this document thoroughly**
2. **Port Dilithium3** from `python/animica/_vendor/dilithium_py/` to TypeScript or compile to WASM
3. **Implement bech32m + SHA3** in browser-safe way (use `@noble/hashes` for SHA3)
4. **Generate test vectors** from CLI and store in `packages/animica-crypto/test-vectors/`
5. **Write unit tests** that match byte-for-byte with Python outputs
6. **Implement wallet storage** using wallets.json format in extension storage
7. **Build transaction signing** that produces identical signBytes to node
8. **Expose `window.animica` provider** API for Dapp IDE

### For Dapp IDE Developer

1. **Use `packages/animica-crypto`** for verification and diagnostics (NOT for signing)
2. **Connect to wallet** via `window.animica` provider
3. **Build transaction objects** following node schema
4. **Request wallet signature** via `animica_signTx`
5. **Verify signatures locally** (optional, for diagnostics page)
6. **Submit signed txs** to node RPC

---

## 12. Test Vector Generation Guide

### Generate Dilithium3 Test Vector

```bash
# Create test wallet
animica wallet create --label test-dilithium3 --alg dilithium3

# Export wallet
animica wallet list --show-secret --label test-dilithium3 > test-dilithium3.json

# Sign test messages
echo -n "hello animica" | \
  animica wallet sign --label test-dilithium3 --domain "test" \
  --chain-id 1337 --output sig1.json

# Verify locally
animica wallet verify --signature sig1.json --message "hello animica"
```

### Generate Transaction Test Vector

```bash
# Create and sign a transaction
animica tx send \
  --from test-dilithium3 \
  --to anim1qq9a7x3k4l2m5n8p9r2s3t4v5w6x7y8z9a0b1c2d3e4f5g6h7j8 \
  --value 1000000000000000000 \
  --chain-id 1337 \
  --nonce 42 \
  --dry-run \
  --output tx-unsigned.json

# Sign transaction
animica tx sign \
  --input tx-unsigned.json \
  --wallet test-dilithium3 \
  --output tx-signed.json

# Extract signature bytes
jq '.signature' tx-signed.json > tx-signature.json
```

---

## 13. Security Checklist

Before deploying to production:

- [ ] Verify address derivation matches node exactly
- [ ] Verify signBytes construction matches node exactly
- [ ] Test signature verification with node-generated signatures
- [ ] Test node verification with wallet-generated signatures
- [ ] Verify domain separation prevents cross-domain attacks
- [ ] Test key format handling (4000 vs 4032 bytes for Dilithium3)
- [ ] Ensure secret keys are never logged or transmitted
- [ ] Implement proper key storage (encrypted extension storage)
- [ ] Add rate limiting for signing requests
- [ ] Implement transaction approval UI with clear field display
- [ ] Test against malicious Dapp scenarios
- [ ] Verify no liboqs dependencies in package.json or build artifacts

---

## Appendix A: Example Code Snippets

### Python: Generate and Use Wallet

```python
from pq.py.keygen import keygen_sig
from pq.py.sign import sign_detached, verify_detached

# Generate keypair
kp = keygen_sig("dilithium3")
print(f"Address: {kp.address}")
print(f"Public key: {kp.public_key.hex()}")
print(f"Secret key: {kp.secret_key.hex()}")

# Sign message
message = b"hello world"
sig = sign_detached(
    msg=message,
    alg=kp.alg_id,
    sk=kp.secret_key,
    domain="test",
    chain_id=1337
)

# Verify signature
is_valid = verify_detached(
    msg=message,
    sig=sig,
    pk=kp.public_key,
    domain="test",
    chain_id=1337
)
assert is_valid
```

### TypeScript: Target Browser Implementation

```typescript
import { sha3_256, sha3_512 } from '@noble/hashes/sha3';
import { bech32m } from 'bech32';

// Address derivation
export function addressFromPubkey(algId: number, pubkey: Uint8Array): string {
  // Compute SHA3-256 digest
  const digest = sha3_256(pubkey);
  
  // Build payload: alg_id (2 bytes BE) + digest (32 bytes)
  const payload = new Uint8Array(34);
  payload[0] = (algId >> 8) & 0xff;
  payload[1] = algId & 0xff;
  payload.set(digest, 2);
  
  // Convert to 5-bit and encode bech32m
  const words = bech32m.toWords(payload);
  return bech32m.encode('anim', words);
}

// SignBytes builder
export function buildSignBytes(
  msg: Uint8Array,
  domain: string,
  chainId: number,
  algId: number
): Uint8Array {
  // Implement length-prefixed encoding
  const tag = new TextEncoder().encode('animica:sign/v1');
  const domainBytes = new TextEncoder().encode(domain);
  
  // Build raw bytes with uvarint lengths
  const parts: Uint8Array[] = [];
  parts.push(encodeUvarint(tag.length), tag);
  parts.push(encodeUvarint(domainBytes.length), domainBytes);
  
  const chainIdBytes = encodeUvarint(chainId);
  parts.push(encodeUvarint(chainIdBytes.length), chainIdBytes);
  
  const algIdBytes = encodeUvarint(algId);
  parts.push(encodeUvarint(algIdBytes.length), algIdBytes);
  
  parts.push(encodeUvarint(0)); // empty context
  parts.push(encodeUvarint(msg.length), msg);
  
  // Concatenate and hash
  const raw = concatUint8Arrays(parts);
  return sha3_512(raw);
}

function encodeUvarint(n: number): Uint8Array {
  const bytes: number[] = [];
  while (true) {
    let b = n & 0x7f;
    n >>= 7;
    if (n === 0) {
      bytes.push(b);
      break;
    }
    bytes.push(b | 0x80);
  }
  return new Uint8Array(bytes);
}

function concatUint8Arrays(arrays: Uint8Array[]): Uint8Array {
  const totalLength = arrays.reduce((sum, arr) => sum + arr.length, 0);
  const result = new Uint8Array(totalLength);
  let offset = 0;
  for (const arr of arrays) {
    result.set(arr, offset);
    offset += arr.length;
  }
  return result;
}
```

---

## Appendix B: Network Configuration

### RPC Endpoints

| Network | RPC URL | Chain ID | Notes |
|---------|---------|----------|-------|
| **Mainnet** | `http://144.126.133.21:8545/rpc` | TBD | **MUST** be included as default |
| **Devnet** | `http://127.0.0.1:8545/rpc` | 1337 | Local development |
| **Testnet** | TBD | TBD | Future use |

### Wallet Configuration

The wallet should allow users to:
1. Select active network (mainnet/devnet/custom)
2. Add custom RPC endpoints
3. Override chain ID (with warning)
4. View network status (block height, peers, etc.)

---

## End of Discovery Document

**Document Version:** 1.0  
**Last Updated:** 2026-02-11  
**Maintainer:** Animica Development Team

For questions or updates, refer to:
- Main repo: `animicaorg/all`
- Spec directory: `spec/`
- PQ implementation: `pq/py/` and `python/animica/pq/`
