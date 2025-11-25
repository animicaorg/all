# PQ POLICY — Algorithm Set, Rotations, Deprecation Windows

**Status:** Stable (v1)  
**Scope:** Canonical post-quantum (PQ) signature & KEM policy used by the Animica stack (wallets, node RPC, P2P, SDKs).  
**Artifacts:** `spec/alg_policy.schema.json`, `pq/alg_ids.yaml`, `pq/alg_policy/example_policy.json`, `pq/cli/pq_alg_policy_root.py`.

This document defines:
- Which PQ algorithms are **allowed** for *signatures* and the P2P *handshake KEM*.
- How we **rotate** preferred algorithms.
- How **deprecation windows** work and how breaking changes are staged.
- How a **Merkle root** of the policy is computed and pinned on-chain.

> TL;DR  
> - **Signatures:** `dilithium3` (preferred) with `sphincs_shake_128s` (backup).  
> - **KEM (P2P):** `kyber768`.  
> - Deprecation windows: **Active → Deprecating (≥ 12 mo) → Sunset (hard reject)**.  
> - Policy is hashed into a stable **alg-policy Merkle root** and published by chain params.

---

## 1) Algorithms & IDs

Machine-readable IDs live in `pq/alg_ids.yaml`. Human overview:

| Kind       | Canonical name            | ID (string)      | Security class | Notes                                   |
|------------|---------------------------|------------------|----------------|-----------------------------------------|
| Signature  | Dilithium3                | `dilithium3`     | ~NIST L2–L3    | Preferred signing scheme                |
| Signature  | SPHINCS+ SHAKE-128s       | `sphincs_128s`   | ~NIST L1–L2    | Stateless hash-based backup             |
| KEM        | Kyber-768                 | `kyber768`       | ~NIST L2–L3    | Used for P2P handshake (KEM + HKDF)     |

> Exact byte formats, key sizes, and encodings are enforced by the `pq/py/algs/*` modules and the schema in `spec/alg_policy.schema.json`.

---

## 2) Policy Object (normalized)

The canonical **alg-policy object** follows `spec/alg_policy.schema.json`:

```jsonc
{
  "version": 1,
  "valid_from": "2025-01-01T00:00:00Z",
  "valid_until": null,                // optional
  "sign": {
    "allowed": ["dilithium3", "sphincs_128s"],
    "preferred": "dilithium3",
    "min_required": 1,               // multi-sig threshold across same-alg signatures
    "address_rules": {
      "hrp": "anim",                 // bech32m hrp
      "payload": "alg_id||sha3_256(pubkey)"
    },
    "deprecation": {
      "grace_months": 12,            // min time from Deprecating→Sunset
      "legacy_accept_cutoff": null   // optional hard date override
    }
  },
  "kem": {
    "allowed": ["kyber768"],
    "preferred": "kyber768",
    "deprecation": {
      "grace_months": 12
    }
  },
  "weights": {
    "sign": { "dilithium3": 100, "sphincs_128s": 30 },
    "kem":  { "kyber768": 100 }
  },
  "notes": "Dilithium3 preferred for signatures; SPHINCS+ as fallback."
}

Normalization rules
	•	Keys are lexicographically ordered and encoded via deterministic CBOR for hashing.
	•	allowed lists are sorted; strings are lowercase canonical names from pq/alg_ids.yaml.

⸻

3) Policy Merkle Root

To pin a policy on-chain we compute a Merkle root over normalized leaves:
	•	Leaf set (ordered):
["version", "valid_from", "valid_until", "sign.allowed[]", "sign.preferred", "sign.min_required", "sign.address_rules.*", "sign.deprecation.*", "kem.allowed[]", "kem.preferred", "kem.deprecation.*", "weights.*.*", "notes"]
	•	Leaf hash: H_i = SHA3-512( tag || canonical_value_bytes )
where tag = "ALG_POLICY/V1/<path>" (ASCII), and canonical_value_bytes is deterministic CBOR of the value.
	•	Merkle: binary, left-then-right, node = SHA3-512(0x01 || L || R); leaf prefix 0x00.

Tooling

# Build & print Merkle root from JSON policy
python -m pq.cli.pq_alg_policy_root --in pq/alg_policy/example_policy.json

The resulting root is:
	•	Placed in chain parameters (spec/params.yaml) and
	•	Emitted by RPC chain.getParams so wallets and nodes can enforce accept/reject.

⸻

4) Lifecycle States

Each algorithm transitions through:
	1.	Active — allowed & fully supported.
	2.	Preferred — a cosmetic flag: wallets should default to this when creating new keys.
	3.	Deprecating — still accepted for verification, but no longer recommended for new keys.
	•	Minimum grace: sign.deprecation.grace_months (default: 12 months).
	4.	Sunset — rejected at the consensus & RPC layers (hard fail).

We never silently auto-migrate user keys. Rotation guidance is communicated in release notes and wallet UX.

⸻

5) Rotations

5.1 Signature rotations
	•	Add new scheme to sign.allowed, set it Preferred, and keep previous Active.
	•	After a minimum soak (e.g., 6 months), mark older scheme Deprecating.
	•	After ≥ 12 months in Deprecating, schedule Sunset in a network upgrade (feature flag) and ship a policy with legacy_accept_cutoff.

Address compatibility
	•	Addresses embed alg_id in the bech32m payload; rotating scheme implies new addresses for new accounts.
	•	Existing accounts remain valid while their scheme is Active/Deprecating; after Sunset, transactions signed with the deprecated alg are rejected.

5.2 KEM rotations
	•	Nodes must support both old and new KEMs during overlaps.
	•	Preferred KEM is used when both peers support it; otherwise negotiate the highest common in kem.allowed.
	•	After deprecation grace, disable the old KEM in handshakes and P2P config.

⸻

6) Emergency Responses

In case of credible cryptanalytic break or critical implementation vulnerability:
	1.	Publish advisory with a patched policy JSON (valid_from as soon as possible).
	2.	Move affected algorithm to Sunset immediately (or with minimal grace if feasible).
	3.	Enable server-side denylist at RPC/mempool admission for deprecated algs.
	4.	Wallet UX: block new signing; prompt key rotation to Preferred alg.
	5.	Consider chain flag to hard-reject at consensus after a short window.

⸻

7) Enforcement Points
	•	RPC (tx.sendRawTransaction): pre-admission checks verify sig.alg_id ∈ sign.allowed.
	•	Mempool: rejects tx if signer alg is Sunset or not in policy.
	•	P2P: handshake must negotiate a KEM in kem.allowed.
	•	Wallets/SDKs: default to sign.preferred; warn on Deprecating.
	•	Policy root: clients must ensure the observed root matches chain.getParams.

⸻

8) Gas & Size Considerations
	•	Signature sizes differ (Dilithium3 vs SPHINCS+). Fee market accounts for byte cost; gas is proportional to tx size.
	•	weights.sign.* may be used by UX and light heuristics; not consensus.

⸻

9) Worked Examples

9.1 Promote a new signature (e.g., foobar512)
	1.	Ship code: pq/py/algs/foobar512.py, add to pq/py/registry.py.
	2.	Update policy JSON:

{
  "sign": {
    "allowed": ["dilithium3", "sphincs_128s", "foobar512"],
    "preferred": "foobar512"
  }
}


	3.	Recompute Merkle root; publish via governance/params.
	4.	After 6–12 months, mark dilithium3 as Deprecating (if desired).

9.2 Deprecate sphincs_128s

Set:

{
  "sign": { "deprecation": { "grace_months": 12, "legacy_accept_cutoff": "2027-03-01T00:00:00Z" } }
}

Ship a feature flag to hard-reject at/after cutoff.

⸻

10) Testing & Validation
	•	pq/tests/test_registry.py — sanity on id/name/size mappings.
	•	pq/tests/test_alg_policy_root.py — policy → root reproducibility.
	•	CI must re-hash policy and compare against spec/params.yaml pin.

⸻

11) Governance & Publication
	•	Policy changes are reviewed and merged with a signed release.
	•	The policy Merkle root is pinned in genesis or a parameter upgrade and exposed via:
	•	spec/openrpc.json → chain.getParams
	•	Wallet update channels (release notes)

⸻

12) Appendix — Minimal Policy (current default)

{
  "version": 1,
  "valid_from": "2025-01-01T00:00:00Z",
  "sign": {
    "allowed": ["dilithium3", "sphincs_128s"],
    "preferred": "dilithium3",
    "min_required": 1,
    "address_rules": { "hrp": "anim", "payload": "alg_id||sha3_256(pubkey)" },
    "deprecation": { "grace_months": 12, "legacy_accept_cutoff": null }
  },
  "kem": {
    "allowed": ["kyber768"],
    "preferred": "kyber768",
    "deprecation": { "grace_months": 12 }
  },
  "weights": {
    "sign": { "dilithium3": 100, "sphincs_128s": 30 },
    "kem": { "kyber768": 100 }
  },
  "notes": "Default Animica PQ policy: Dilithium3 preferred; SPHINCS+ backup; Kyber768 for P2P."
}

Compute root

python -m pq.cli.pq_alg_policy_root --in pq/alg_policy/example_policy.json

