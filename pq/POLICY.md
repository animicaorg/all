# Animica Post-Quantum (PQ) Policy

This document defines **which PQ algorithms are allowed**, how **addresses** are formed, how **KEM handshakes** are executed, and how we **rotate/deprecate** algorithms without breaking users.

It complements:
- `spec/pq_policy.yaml` (consensus parameters & roots)
- `spec/alg_policy.schema.json` (alg-policy Merkle tree schema)
- `pq/alg_ids.yaml` (canonical algorithm IDs & sizes)

---

## 1) Goals & non-goals

**Goals**
- Strong, NIST-track post-quantum security by default (no classical fallback needed for consensus).
- Clear, deterministic address format that **binds the algorithm** used by the key.
- Seamless **P2P PQ handshake** for encryption (KEM→KDF→AEAD) with transcript binding.
- Upgradable via **alg-policy Merkle root** anchored in headers (consensus-visible).
- Pragmatic UX in wallets/SDKs: mnemonic-based derivation, algorithm-aware accounts.

**Non-goals**
- Classical ECDSA/Ed25519 signatures are not part of consensus keys.
- Multi-sig/hardware wallet UX beyond basic rules is handled in wallet specs.

---

## 2) Canonical algorithms (current)

From `pq/alg_ids.yaml`:

| ID     | Name                  | Kind        | NIST lvl | Status  | Notes                                  |
|--------|-----------------------|-------------|----------|---------|----------------------------------------|
| 0x1001 | dilithium3            | Signature   | 3        | active  | **Default** signature for accounts/nodes |
| 0x1002 | sphincs_shake_128s    | Signature   | 1        | active  | Hash-based fallback, large sigs        |
| 0x2001 | kyber768              | KEM         | 3        | active  | **Default** P2P KEM                    |

Future additions (e.g., Dilithium5, ML-KEM-1024) are added by policy root updates (see §7).

---

## 3) Address rules

**Address payload** = `alg_id (u16, big-endian)` **||** `sha3_256(pubkey)` (32 bytes) → Bech32m.

- **HRP** (human-readable prefix):
  - Mainnet: `anim`
  - Testnet: `tnim`
  - Devnet: `dnim`
- Example (Dilithium3):

payload = 0x10 0x01 || sha3_256(pubkey_dil3)
bech32m(anim, payload) → anim1…

**Consequences**
- The **algorithm is part of the address**. A SPHINCS+ address is distinct from a Dilithium3 address.
- Wallet UIs **must display** the algorithm next to the address.
- **No algorithm swapping**: funds sent to an alg-tagged address must be spent with that algorithm’s signature.

**Multisig & hybrids**
- Contract-level multisig is preferred. Example: 2-of-2 (Dilithium3, SPHINCS+).
- For “dual control”, publish two addresses (alg-specific); spending policy enforced by contract.

---

## 4) Signature policy (Tx/blocks)

- **Default signature for accounts & nodes**: `dilithium3 (0x1001)`.
- **Supported alternative**: `sphincs_shake_128s (0x1002)`; recommended for archival or special compliance profiles.

Tx `SignBytes` (see `spec/tx_format.cddl`) includes:
- `alg_id`: must match the **sender’s address alg_id**.
- `chain_id`, `domain`, and canonical CBOR map order (domain sep per `spec/domains.yaml`).

**Verifier rules**
1. Decode CBOR → extract `alg_id` and signature bytes.
2. Resolve **allowed set** from current **alg-policy Merkle root** (see §7).
3. Verify signature with the extracted public key bytes (recovered or included per schema).
4. **Reject** if:
 - `alg_id` not in allowed set or is explicitly **deprecated** at the current height.
 - Signature length mismatches the canonical size for that algorithm.
 - Domain separation tags mismatch (`spec/domains.yaml`).

**Node identity keys**
- Nodes publish an **identity signature key** (Dilithium3 preferred; SPHINCS+ allowed) used for P2P identity and RPC auth where applicable. This key is **separate** from wallet accounts.

---

## 5) KEM handshake policy (P2P)

- **KEM**: `kyber768 (0x2001)` for P2P handshakes.
- **KDF**: `HKDF-SHA3-256` with transcript-bound salt & context.
- **AEAD**: `ChaCha20-Poly1305` (default). `AES-GCM` may be enabled via feature flag where HW acceleration exists.
- **Transcript binding**:
- Mix the following into the transcript hash: KEM IDs, versions, peer IDs (hash of PQ identity pubkey + alg_id), chainId, and network magic.
- Derive distinct send/recv keys via HKDF info labels (`"animica:p2p:k1"`, `"animica:p2p:k2"`).

**Rotation**
- To introduce a new KEM (e.g., ML-KEM-1024), we:
1. Add it to the alg-policy tree (status=`candidate`).
2. Negotiate via HELLO capabilities; prefer new KEM only when both peers advertise support.
3. Flip default in a later policy epoch (see §7).

---

## 6) Wallet & SDK rules

- **Mnemonic → seed**: PBKDF2-HMAC-SHA3-256 or HKDF-SHA3-256 (see wallet docs). **Per-algorithm derivation paths** (distinct coin-type codes) to avoid cross-algorithm key reuse.
- **Key export/import** must carry algorithm metadata; importing a key into a mismatched address type is invalid.
- **On send**, SDK validates: `from.address.alg_id == tx.alg_id`.
- **On sign**, SDK uses domain constants from `spec/domains.yaml`, chainId, and canonical CBOR (no floating maps).

---

## 7) Alg-policy Merkle root (consensus)

The consensus header commits to an **alg-policy Merkle root** that encodes:
- Enabled algorithms (per **kind**: signature, KEM)
- Status: `active | candidate | deprecated`
- Activation/deprecation heights or epochs
- Size tables & IDs (to prevent downgrade/size ambiguity)
- Optional **weights** for migration guidance (non-consensus)

**Process**
1. Construct a JSON policy tree matching `spec/alg_policy.schema.json`.
2. Canonicalize (stable key order, no floating point).
3. Hash leaves with SHA3-512; compute a Merkle root.
4. Place the root in the header’s **policy field** (see `spec/header_format.cddl`).
5. Nodes must refuse signatures/KEMs that are not **active** at the current height.

**Epoch scheduling**
- Policy changes are scheduled at **well-announced epochs** with a grace period (e.g., 2 weeks on testnet, 1 month on mainnet).
- Deprecation path: `active → candidate (dual-run) → deprecated (reject)`.

---

## 8) Compatibility & implementation notes

- **liboqs** (preferred): C bindings (via `pq/py/algs/oqs_backend.py`) deliver performant Dilithium3/Kyber768.
- **Pure-Python fallbacks**: Provided for development only; **NOT for production**.
- **WASM**: Browser wallet uses WASM for PQ ops; feature-detect and fall back gracefully (never block chain usage).
- **Determinism**: Signatures must use deterministic sampling where the scheme allows; otherwise, RNG must be **OS CSPRNG** only (no userland seeds).
- **Side-channels**: Prefer constant-time implementations. Never log secret material.
- **Address length** is fixed: `2 (alg_id) + 32 (sha3 pubkey hash)` before Bech32m expansion; validate length strictly.

---

## 9) Operational guidance

**Key rotation**
- Rotate identity & account keys by **creating new addresses**; funds move on-chain; contracts update auth lists.
- Never attempt to “re-tag” an existing address with a new algorithm.

**Disaster recovery**
- If an algorithm is urgently deprecated:
- Policy root marks it `deprecated` at height H (after governance action).
- Wallets/SDKs warn & block fresh sends from old alg; guide migration tools.

**Testing**
- Use `pq/cli/pq_keygen.py`, `pq/cli/pq_sign.py`, `pq/cli/pq_verify.py` for vectors.
- Use `pq/cli/pq_handshake_demo.py` to validate P2P transcript derivation.

---

## 10) Examples (CLI)

Generate a Dilithium3 key:
```sh
python -m pq.cli.pq_keygen --alg dilithium3 --out sk_dil3.bin --pub pk_dil3.bin

Build an address:

python - <<'PY'
from pq.py.address import Address
alg_id = 0x1001
pub = open("pk_dil3.bin","rb").read()
print(Address.encode(alg_id, pub, hrp="dnim"))  # devnet
PY

Sign & verify:

python -m pq.cli.pq_sign --alg dilithium3 --sk sk_dil3.bin --in msg.bin --out sig.bin
python -m pq.cli.pq_verify --alg dilithium3 --pk pk_dil3.bin --in msg.bin --sig sig.bin

KEM handshake demo:

python -m pq.cli.pq_handshake_demo


⸻

11) Policy summary (current default)
	•	Signatures: Dilithium3 active & default; SPHINCS+ SHAKE-128s active (optional).
	•	KEM: Kyber768 active & default for P2P.
	•	Addresses: Bech32m; alg_id || sha3_256(pubkey) payload; HRP = anim/tnim/dnim.
	•	Alg-policy: Root committed in headers; nodes enforce active set; scheduled epochs for changes.

