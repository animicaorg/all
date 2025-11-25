# ENVELOPE — Generic Proof Envelope & Nullifiers (Anti-Reuse)

**Status:** Stable (V1)  
**Scope:** Canonical, type-tagged wrapper around a proof **body** with a **nullifier** to prevent re-use. Applies to all proof kinds (HashShare, AI, Quantum, Storage, VDF, …).

This document defines the *generic* envelope carried in blocks/headers and over P2P. Individual proof bodies are specified in sibling docs (e.g., `HASHSHARE.md`, `AI_V1.md`, `VDF_BONUS.md`). The envelope is deliberately small and uniform across proof kinds.

---

## 1) Design goals

- **Uniformity.** One wrapper for all proofs, independent of body schema.
- **Canonicality.** *Byte-exact* hashing via deterministic CBOR for stable IDs.
- **Anti-reuse.** A **nullifier** derived from a type-separated domain and the *canonical body bytes* prevents crediting the same proof twice.
- **Hash agility.** Hash domain tags are explicit; the default hash is SHA3-256.
- **Minimal surface.** No signatures or policy data live in the envelope; those live in proof bodies.

---

## 2) CDDL (informative)

The canonical CBOR schema for the envelope lives in `proofs/schemas/proof_envelope.cddl`. For convenience:

; proofs/schemas/proof_envelope.cddl (excerpt)

ProofEnvelope = {
type_id:   uint,          ; proof kind discriminator (see §3)
body:      bstr,          ; canonical CBOR encoding of the kind-specific body
nullifier: bstr .size 32  ; SHA3-256(domain || body)[:32]
}

> **Body is CBOR-in-bytes.** The `body` field is a **byte string** that contains the canonical CBOR encoding of the proof’s body map. This avoids double-encoding ambiguities and makes hashing trivial and stable.

---

## 3) Type IDs & domains

Each proof kind has:
- a **`type_id`** (small `uint`), and
- a **hash domain tag** (ASCII).

| Proof kind        | `type_id` | Hash domain (ASCII)           | Body spec                     |
|-------------------|-----------|-------------------------------|-------------------------------|
| HashShare (u-draw)| `0x01`    | `PROOF_NULLIFIER/HASH_V1`     | `HASHSHARE.md`                |
| AI v1             | `0x02`    | `PROOF_NULLIFIER/AI_V1`       | `AI_V1.md`                    |
| Quantum v1        | `0x03`    | `PROOF_NULLIFIER/QUANTUM_V1`  | `QUANTUM_V1.md`               |
| Storage v0        | `0x04`    | `PROOF_NULLIFIER/STORAGE_V0`  | `STORAGE_V0.md`               |
| VDF bonus v1      | `0x05`    | `PROOF_NULLIFIER/VDF_V1`      | `VDF_BONUS.md`                |

> Future kinds MUST reserve a new `type_id` and a fresh domain tag. Never reuse tags across kinds or versions.

---

## 4) Canonicalization rules (CBOR)

All *body* maps must be encoded using **deterministic CBOR**:
- **Key order:** bytewise lexicographic order of the canonical encodings of keys.
- **Integer form:** shortest-length encoding (no leading zero octets).
- **Indefinite-length:** disallowed.
- **Floating-point:** disallowed unless explicitly permitted; prefer integers/byte strings.
- **Bools/Null:** standard CBOR values.
- **Byte strings:** big-endian for integers represented as octet strings; no leading zeros.
- **Text:** UTF-8, NFC recommended.

The envelope itself is encoded deterministically as well, but the **nullifier** depends only on the `body` bytes (and the domain).

---

## 5) Nullifier construction

The **nullifier** is a 32-byte tag to prevent *re-use* of the exact same body:

nullifier = SHA3_256( domain_tag || body_bytes )[:32]

- `domain_tag` is the ASCII tag for the proof kind/version (see table).
- `body_bytes` is the **exact** deterministic CBOR encoding of the body map.
- The nullifier **must not** include additional context (e.g., block hash) to remain stable under relay; instead, the body itself MUST bind any replay-sensitive fields (e.g., chainId, height, seed).

> Rationale: Reuse is prevented because bodies include binding fields (chainId, height/epoch windows, seeds, provider nonces). If a prover changes any of these, the body changes and so does the nullifier.

---

## 6) Anti-reuse semantics (consensus)

Consensus maintains a **nullifier set** with **TTL**:
- **Accept** a proof if `nullifier` is **not present** in the set and all other body/policy checks pass.
- **Insert** the nullifier into the set on acceptance.
- **Reject** any subsequent envelope with the same `nullifier` until the TTL window expires.

Typical TTL: long enough to cover reorg horizons (e.g., `k` blocks or an epoch). See `consensus/nullifiers.py` and `consensus/validator.py`.

> Kind-specific limits (e.g., per-provider cool-downs) are enforced in body verifiers and the policy layer.

---

## 7) Required body bindings

Each proof body **must** include:
- `chainId` — network binding,
- a **freshness anchor** — e.g., `height`, `seed`, or template hash,
- any **provider identity & nonce** (when applicable),
- any **policy roots/ids** needed for acceptance.

This ensures the same work can’t be replayed across heights, chains, or policy epochs.

---

## 8) Pseudocode (construct & verify)

**Construct envelope (prover / builder):**
```python
# body is a Python dict that follows the proof-kind schema
body_bytes = cbor_deterministic_encode(body)
domain = DOMAIN_FOR_TYPE[type_id]  # ASCII bytes

nullifier = sha3_256(domain + body_bytes)[:32]

envelope = {
  "type_id": type_id,
  "body": body_bytes,
  "nullifier": nullifier,
}

Verify envelope (validator):

def verify_envelope(env):
    assert isinstance(env["type_id"], int)
    body_bytes = env["body"]
    nf = env["nullifier"]
    domain = DOMAIN_FOR_TYPE[env["type_id"]]

    # Recompute nullifier
    expect_nf = sha3_256(domain + body_bytes)[:32]
    if nf != expect_nf:
        raise ProofError("NullifierMismatch")

    # Decode & kind-verify
    body = cbor_strict_decode(body_bytes)
    verify_body_by_kind(env["type_id"], body)  # semantics & policy checks

    # Check nullifier set (consensus state)
    if nullifier_seen(nf):
        raise ProofError("NullifierReuse")

    # OK
    return True


⸻

9) Malleability & stability
	•	Canonical encoder required. Any non-canonical or equivalent map with different byte layout will yield a different body_bytes and thus a different nullifier. Use the repository’s canonical encoder (proofs/cbor.py).
	•	Text vs bytes. Don’t interchange integers and textual hex. Addresses, hashes, and large integers MUST follow the body schema (typed integers vs bstr).
	•	Key naming. Keys are fixed strings in each body schema; renames are breaking changes.

⸻

10) Size limits & gas

Policy may impose size caps per kind:
	•	|body_bytes| ≤ MAX_BODY_SIZE_KIND.
	•	Larger bodies incur higher gas or are rejected.
	•	Envelope overhead is constant; fee/costing logic uses kind-specific metrics extracted during body verification.

See zk/integration/policy.py and consensus/caps.py.

⸻

11) Worked example (annotated)

(Illustrative; hex is truncated)

type_id   = 0x05                    ; VDF bonus v1
body_map  = {
  "domain":    "VDF_V1/Wesolowski",
  "chainId":   1,
  "height":    123456,
  "seed":      h'8f…21',            ; 32 bytes
  "modulus_id":"rsa2048-netA",
  "g":         h'02…7b',
  "y":         h'43…19',
  "pi":        h'a9…55',
  "T":         33554432,
  "params": {"lambda":128, "l_bits":256},
  "provider":{"id":"prov-01", "pubkey":h'…', "nonce":h'00112233445566778899aabbccddeeff'},
  "bind_sig":  h'30…01'
}

body_bytes = CBOR_CANON(body_map)   ; bstr of ~ 300–800 bytes
domain     = "PROOF_NULLIFIER/VDF_V1"
nullifier  = SHA3_256(domain || body_bytes)[:32] = h'c4…5a'

envelope   = { type_id: 5, body: h'58…', nullifier: h'c4…5a' }


⸻

12) Interop: inclusion in headers
	•	Envelopes are aggregated into a proofs receipt set and summarized by a Merkle root (proofs/receipts.py), which contributes to the block’s proofsRoot (see docs/spec/BLOCK_FORMAT.md).
	•	Full bodies may be carried in the block body or fetched via DA channels depending on policy; the envelope remains the minimal verification unit.

⸻

13) Versioning
	•	Envelope is V1 and intentionally tiny; evolution happens in body schemas and type_id assignments.
	•	A future envelope version would only add fields if necessary (e.g., flags), never altering the nullifier definition for V1 entries.

⸻

14) Security notes
	•	Domain separation. Prevents cross-kind collisions even if body bytes collide (impractical under SHA3-256).
	•	Replay hardness. Achieved by embedding freshness parameters (height/seed) and identities in the body, not by making the nullifier context-dependent.
	•	Hash agility. If the network ever migrates hash functions, it must be done per-kind with new domain tags and body domain strings; mixing functions under the same tag is forbidden.

⸻

15) Compliance checklist
	•	Body encodes with deterministic CBOR (repository encoder).
	•	type_id matches the body’s declared domain (e.g., VDF_V1/* ↔ 0x05).
	•	Body contains chain binding (chainId) and freshness anchor (e.g., height/seed).
	•	nullifier == H(domain || body_bytes)[:32] with domain from the table.
	•	Body passes kind-specific verification.
	•	Nullifier not present in consensus nullifier set (within TTL).
	•	Size ≤ policy cap.

⸻

References
	•	proofs/schemas/proof_envelope.cddl — authoritative schema.
	•	proofs/cbor.py, proofs/nullifiers.py — canonical encoding & nullifier helpers.
	•	consensus/nullifiers.py, consensus/validator.py — rejection on reuse & acceptance flow.
	•	Proof-kind specs: HASHSHARE.md, AI_V1.md, QUANTUM_V1.md, STORAGE_V0.md, VDF_BONUS.md.

