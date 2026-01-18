# PQ Identity — Post-Quantum Cryptography on the Critical Path

**Status:** Stable (v1)  
**Audience:** Wallet developers, node operators, protocol engineers  
**Related:** `pq/py/`, `docs/pq/KEYS.md`, `docs/pq/POLICY.md`, `spec/pq_policy.yaml`

---

## 1) Overview

Animica uses **post-quantum (PQ) cryptography** for all critical path operations:
- **Transaction signatures** - Every transaction MUST be signed with a PQ algorithm
- **Validator identities** - Block proposers use PQ keys
- **Wallet accounts** - Default account type uses PQ keys
- **Node-to-node handshakes** - P2P uses Kyber768 KEM

This document describes how PQ identity works in practice, how it's enforced, and how to use it.

---

## 2) Supported Algorithms

### 2.1 Signature Algorithms

| Algorithm | Usage | Public Key | Signature | Address Tag |
|-----------|-------|------------|-----------|-------------|
| **Dilithium3** | Primary | ~1472 bytes | ~2701 bytes | `0x01` |
| **SPHINCS+ 128s** | Backup | ~32 bytes | ~7856 bytes | `0x02` |

**Dilithium3** is the **recommended default** for:
- High security level (NIST Level 3)
- Reasonable signature size
- Fast verification

**SPHINCS+ 128s** is available as:
- Backup if Dilithium3 is deprecated
- Stateless alternative (no secret key state)
- Smaller public keys

### 2.2 KEM Algorithm

| Algorithm | Usage | Public Key | Ciphertext |
|-----------|-------|------------|------------|
| **Kyber768** | P2P handshake | ~1184 bytes | ~1088 bytes |

Used exclusively for **node-to-node session key establishment**. NOT used for transaction signing or account addresses.

---

## 3) PQ-Only Account Type

### 3.1 Address Format

Animica addresses use **bech32m encoding** with PQ algorithm identification:

```
Payload (33 bytes) = alg_id_byte || sha3_256(pubkey)
Address = bech32m(HRP, payload)
```

**Example:**
```python
# Dilithium3 public key (1472 bytes)
pubkey = keygen.keygen_sig("dilithium3").public_key

# Compute address
alg_id = 0x01  # Dilithium3
hash = sha3_256(pubkey)
payload = bytes([alg_id]) + hash  # 33 bytes

# Encode as bech32m
address = bech32m("anim", payload)  # "anim1q..."
```

**HRP (Human-Readable Part) by network:**
- Mainnet: `anim` → `anim1...`
- Testnet: `anit` → `anit1...`
- Local/dev: `anil` → `anil1...`

### 3.2 Address Validation

To validate an address:

1. **Decode bech32m** → extract HRP and payload
2. **Check length** → payload MUST be exactly 33 bytes
3. **Extract alg_id** → first byte identifies algorithm
4. **Verify alg_id** → MUST be in current policy's `sign.allowed` list
5. **Optional:** Re-hash pubkey and verify last 32 bytes match

**Invalid examples:**
- Wrong HRP for chain
- Wrong payload length
- Deprecated algorithm
- Unknown algorithm ID

---

## 4) Transaction Signature Validation

### 4.1 Transaction Format

Every transaction includes:

```json
{
  "from": "anim1q...",      // 33-byte address payload (bech32m decoded)
  "to": "anim1z...",
  "value": 1000,
  "nonce": 42,
  "gas": 21000,
  "data": "0x...",
  "signature": {
    "alg_id": 1,            // Dilithium3
    "signature_bytes": "..."
  }
}
```

### 4.2 Validation Steps

When a transaction arrives at a node:

1. **Extract alg_id from address** → `alg_id = from_address[0]`
2. **Check policy** → Verify `alg_id` is allowed
3. **Build canonical SignBytes**:
   ```python
   sign_bytes = build_sign_bytes(
       msg=canonical_tx_bytes,
       domain="tx/sign",
       chain_id=1,
       alg_id=alg_id,
       prehash="sha3-512"
   )
   ```
4. **Verify signature**:
   ```python
   is_valid = verify_detached(
       msg=canonical_tx_bytes,
       sig=signature,
       pk=public_key,
       domain="tx/sign",
       chain_id=chain_id
   )
   ```
5. **Reject if invalid** → Transaction is dropped from mempool

**Code reference:** `pq/py/verify.py::verify_detached()`

### 4.3 Domain Separation

All signatures use **explicit domain tags** to prevent cross-context replay:

| Domain | Usage |
|--------|-------|
| `tx/sign` | Transaction signatures |
| `header/proposer` | Block proposal signatures |
| `p2p/identity` | Node identity proofs |
| `da/receipt` | Data availability receipts |

**Never reuse signatures across domains** - the signature will fail verification.

---

## 5) Critical Path Enforcement

### 5.1 Where PQ is Required

PQ signatures are **mandatory** for:

1. **Transaction admission** - Every tx MUST have valid PQ signature
2. **Block proposals** - Validator signatures use PQ
3. **Governance votes** - On-chain proposals require PQ signatures
4. **Contract deployment** - Creator signature must be PQ
5. **Wallet operations** - All user-initiated actions

**No ECDSA/Ed25519 fallback on mainnet** - Legacy algorithms are not accepted.

### 5.2 Mempool Validation

When a transaction enters the mempool:

```python
def validate_transaction(tx):
    # 1. Extract address and verify format
    from_addr = decode_bech32m(tx.from)
    if len(from_addr) != 33:
        raise ValueError("Invalid address length")
    
    # 2. Check algorithm is allowed
    alg_id = from_addr[0]
    if not is_allowed(alg_id):
        raise ValueError(f"Algorithm {alg_id} not allowed")
    
    # 3. Verify signature
    pubkey = recover_pubkey(from_addr, tx)  # From state or witness
    if not verify_pq_signature(tx, pubkey, alg_id):
        raise ValueError("Invalid PQ signature")
    
    # 4. Check nonce, balance, gas
    # ...
    
    return True
```

### 5.3 Block Validation

When a block is validated:

```python
def validate_block(block):
    # 1. Verify proposer PQ signature on header
    if not verify_pq_signature(
        block.header,
        block.proposer_pubkey,
        domain="header/proposer"
    ):
        raise InvalidBlock("Invalid proposer signature")
    
    # 2. Validate all transactions
    for tx in block.transactions:
        validate_transaction(tx)
    
    # 3. Verify PoIES score (includes quantum proofs)
    if not validate_poies_score(block):
        raise InvalidBlock("Insufficient PoIES score")
    
    return True
```

**Code reference:** `consensus/validator.py`

---

## 6) Key Generation

### 6.1 Deterministic (from mnemonic)

Wallets derive PQ keys from a mnemonic:

```python
from pq.py import keygen
from pq.py.utils.hash import sha3_256

# 1. Mnemonic → seed (PBKDF2-HMAC-SHA3-256)
mnemonic = "word1 word2 ... word24"
seed = pbkdf2(mnemonic, salt="animica:mnemonic:v1", rounds=2048)

# 2. HKDF to derive per-algorithm subkey
info = f"alg:dilithium3|purpose:SIGN|path:m/0/0"
derived_seed = hkdf_sha3_256(seed, info=info)

# 3. Generate keypair from derived seed
kp = keygen.keygen_sig("dilithium3", seed=derived_seed)

print(f"Address: {kp.address}")
```

### 6.2 Random (for testing)

Generate a random keypair:

```python
from pq.py import keygen

# Uses OS randomness
kp = keygen.keygen_sig("dilithium3")

print(f"Public key: {kp.public_key.hex()}")
print(f"Address: {kp.address}")
```

---

## 7) Signing and Verification

### 7.1 Sign a Transaction

```python
from pq.py import sign

# Transaction payload
tx_payload = canonical_cbor_encode({
    "from": from_address,
    "to": to_address,
    "value": 1000,
    "nonce": 42,
    "gas": 21000
})

# Sign with domain separation
signature = sign.sign_detached(
    msg=tx_payload,
    alg="dilithium3",
    sk=secret_key,
    domain="tx/sign",
    chain_id=1
)

# Attach to transaction
tx = {
    **tx_fields,
    "signature": {
        "alg_id": signature.alg_id,
        "bytes": signature.signature_bytes
    }
}
```

### 7.2 Verify a Signature

```python
from pq.py import verify

is_valid = verify.verify_detached(
    msg=tx_payload,
    sig=signature,
    pk=public_key,
    domain="tx/sign",
    chain_id=1
)

if not is_valid:
    raise ValueError("Invalid signature")
```

---

## 8) Wallet Integration

### 8.1 Recommended Flow

1. **Generate mnemonic** (24 words, BIP-39 compatible)
2. **Derive seed** using PBKDF2-HMAC-SHA3-256
3. **Derive Dilithium3 keypair** for account 0
4. **Display address** to user
5. **Store encrypted private key** (never plaintext on disk)
6. **Sign transactions** with domain="tx/sign", chain_id=<network>

### 8.2 Address Display

Show user a **shortened address** for convenience:

```
Full:  anim1q5j3k7m9n2p4r6t8v0w2x4y6z8a0b2c4d6e8f0
Short: anim1q5j...8f0
```

But **always verify against full address** internally.

### 8.3 Hardware Wallet Support

For hardware wallets:
- **Store only public key** on device
- **Derive child keys** on device using HKDF
- **Sign with domain tags** specified by host
- **Never export private key**

---

## 9) Policy and Deprecation

### 9.1 Algorithm Policy

The network maintains a PQ algorithm policy (`spec/pq_policy.yaml`):

```yaml
sign:
  allowed:
    - dilithium3      # 0x01
    - sphincs_shake_128s  # 0x02
  deprecating: []     # Algorithms being phased out
  sunset: []          # No longer accepted

kem:
  allowed:
    - kyber768        # 0x10
```

### 9.2 Deprecation Process

When an algorithm is deprecated:

1. **Deprecating** - New signatures discouraged but still accepted
2. **Sunset** - No longer accepted; transactions are rejected
3. **Grace period** - Users must migrate to new algorithm

**Timeline:** Typically 6-12 months from deprecation to sunset.

---

## 10) Testing

### 10.1 Unit Tests

PQ signature tests: `pq/tests/test_sign_verify.py`

```bash
pytest pq/tests/test_sign_verify.py -v
```

### 10.2 Integration Tests

Transaction validation tests: `tests/integration/test_pq_transaction_validation.py`

```bash
RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_pq_transaction_validation.py -v
```

**Test scenarios:**
- Sign and verify with Dilithium3 ✓
- Verify fails on corrupted signature ✓
- Verify fails on wrong message ✓
- Domain separation prevents replay ✓
- Chain ID separation prevents replay ✓

---

## 11) FAQ

### Why PQ-only? Why not allow ECDSA as fallback?

**Harvest-now-decrypt-later attacks** are real. Quantum computers capable of breaking ECDSA are estimated to arrive in 10-20 years. Data encrypted today with classical crypto could be decrypted retroactively. By using PQ-only:
- **Future-proof** - No migration pain later
- **Consistent security** - All users get same protection
- **No weak link** - Attack surface is uniform

### Are PQ keys larger?

Yes. Dilithium3:
- Public key: ~1472 bytes (vs 33 bytes for ECDSA)
- Signature: ~2701 bytes (vs 65 bytes for ECDSA)

But:
- **Bandwidth is cheap** - A few KB per transaction is acceptable
- **Verification is fast** - Dilithium3 is efficient
- **Storage is not on the critical path** - Only recent keys need to be hot

### Can I use the same key for multiple chains?

**No.** Chain ID is part of the signature domain. A signature for chain 1 will not verify on chain 2.

### What if Dilithium3 is broken?

The network will:
1. **Deprecate** Dilithium3 in policy
2. **Promote** SPHINCS+ or a new algorithm
3. **Give users time** to migrate (6-12 months)
4. **Sunset** Dilithium3 after grace period

Users keep the same mnemonic and derive new keys with the new algorithm.

---

## 12) References

- **Implementation:** `pq/py/sign.py`, `pq/py/verify.py`, `pq/py/keygen.py`
- **Address format:** `pq/py/address.py`, `docs/pq/KEYS.md`
- **Policy:** `spec/pq_policy.yaml`, `docs/pq/POLICY.md`
- **Test vectors:** `pq/test_vectors/`

---

## Changelog

- **v1.0** - Initial PQ identity documentation with critical path enforcement details
