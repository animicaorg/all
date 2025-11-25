# Post-Quantum (PQ) Algorithm Policy

**Status:** Adopted  
**Related:** `spec/pq_policy.yaml`, `pq/alg_ids.yaml`, `pq/alg_policy/*.json`, `pq/alg_policy/build_root.py`, `spec/alg_policy.schema.json`, `pq/py/*`, `p2p/crypto/*`, `wallet-extension/*`, `sdk/*`

This document defines the *allowed* PQ algorithms (signatures and KEM), how the active set is represented on-chain, and the procedures for **rotation** and **deprecation** under governance.

> TL;DR — We standardize on **Dilithium3** and **SPHINCS+ (SHAKE-128s)** for signatures and **Kyber-768** for KEM. Changes are governed via **PQRotation** proposals that update a Merkle-rooted **alg-policy** object pinned in headers and enforced network-wide, with planned and emergency paths.

---

## 1) Scope & Source of Truth

- **Normative sources**
  - Canonical policy object: `spec/pq_policy.yaml` (human-readable), and
  - Canonical machine object: `pq/alg_policy/*.json` (validated by `spec/alg_policy.schema.json`)
  - Merkle root of the machine object is computed by `pq/alg_policy/build_root.py` and pinned in:
    - **Genesis** parameters, and
    - **Header** field `algPolicyRoot` (via upgrades).
- **Enforcement surfaces**
  - Wallets: address derivation & signing,
  - RPC/mempool: signature algorithm admission,
  - P2P: handshake KEM set,
  - Node identity: peer-ID signing algorithm,
  - Contracts & light clients (optional): may check the `algPolicyRoot` for on-chain validation.

---

## 2) Algorithm Set (current)

| Kind | Alg ID (see `pq/alg_ids.yaml`) | Canonical Name | Role | Default Status |
|---|---|---|---|---|
| Signature | `dilithium3` | CRYSTALS-Dilithium Level 3 | Accounts, Node identity | **Enabled** |
| Signature | `sphincs_shake_128s` | SPHINCS+ SHAKE-128s | Accounts (backup/long-term) | **Enabled** |
| KEM | `kyber768` | CRYSTALS-Kyber-768 | P2P handshake (KEM) | **Enabled** |

**Rationale**
- **Dilithium3**: strong performance & security; mainline choice for hot accounts.
- **SPHINCS+ (SHAKE-128s)**: stateless hash-based; slower, but conservative fallback for long-term keys.
- **Kyber-768**: widely adopted for KEM; used in P2P to establish AEAD session keys.

> Additional algorithms MAY be added as *experimental* behind feature flags and **disabled** by default until standardized and tested (benchmarks, vectors, interop).

---

## 3) Address Rules & Encodings

- Address payload: `payload = alg_id || sha3_256(pubkey)` (see `pq/py/address.py`).
- Human format: **bech32m** with HRP `anim` → e.g., `anim1…` (see `pq/py/utils/bech32.py`).
- **Rule:** A given address **encodes** which signature scheme it expects. A signature using a different scheme **MUST be rejected**.

---

## 4) Policy Object & Root

- The machine policy is a tree of **alg entries** (name, id, status, weights, cutoff epochs) serialized as JSON per `spec/alg_policy.schema.json`.
- The **Merkle root** (SHA3-512) of that tree (stable layout) is computed by:

python -m pq.alg_policy.build_root pq/alg_policy/example_policy.json

- This root is **pinned** in the chain config and in block headers. Nodes **MUST** enforce the active set that corresponds to the pinned root.

---

## 5) Rotations & Deprecations

### 5.1 Planned Rotation (normal)
- **Cadence:** Review **every 12 months**. Rotate only with strong motivation (cryptanalysis, standard updates, performance wins).
- **Process:** Governance **PQRotation** proposal including:
- New/changed entries and statuses,
- Updated machine policy JSON, computed **root**,
- Test vectors (sign/verify, KEM encaps/decaps),
- Benchmarks on reference hardware,
- Compatibility and migration plan (wallets, SDKs, P2P).
- **Activation:** Height-gated activation with **minimum notice**:
- **Testnet:** ≥ 2 weeks before mainnet activation,
- **Mainnet:** notice ≥ 4 weeks; code released ≥ 2 weeks prior.

### 5.2 Deprecation Windows (signatures)
- **Advertised (soft)**: At **T0**, mark algorithm as `deprecated: true` in policy; wallets **warn** on new key creation.
- **Dual-accept (grace)**: From **T0 → T0 + 6 months**, nodes accept signatures from both the old and replacement algorithms *for existing addresses*. New addresses SHOULD use the replacement.
- **Disable issuance**: At **T0 + 6 months**, wallet UIs MUST hide the deprecated algo for new keys by default (override requires expert mode).
- **Enforcement (hard off)**: At **T0 + 12 months**, mempool/rpc MUST reject signatures using the deprecated algorithm (unless an explicit, narrowly-scoped exception is passed by another governance action).

### 5.3 KEM (P2P) rotations
- **Parallel support**: Nodes SHOULD support **multiple KEMs** during a transition; handshake negotiates the highest-priority common KEM.
- **Cutover window**: ≥ 3 months with mixed support. After expiry, nodes MAY drop legacy KEM from accept set.

### 5.4 Emergency Rotation
- Trigger: credible cryptanalytic break, catastrophic implementation bug, or key-recovery vulnerability.
- **Expedited proposal** (24–72h) updating policy root to **disable** the affected algorithm.
- **Actions:**
- Node updates with hotfix; mempool rejects affected signatures immediately.
- Wallets display **blocker** banner; prompt key migration.
- P2P can flip KEM preference instantly with a minor release.
- **Rollback plan** MUST be stated, even if “not applicable”.

---

## 6) Migration Guidance

- **Account migration:** Provide wallets with `export → import` and **dual-sig permit** flows (contract permits signed with both old and new keys for a fixed window).
- **Contract allowlists:** Contracts that verify signatures SHOULD reference the **policy root** (or a registry mirror) to avoid hardcoding algorithms.
- **Node identity:** Nodes SHOULD provision both Dilithium3 and SPHINCS+ identities during transition; peer-ID derives from the active identity per policy.

---

## 7) Admission & Validation Rules

- **Mempool/RPC:**
- Verify `sig.alg_id` ∈ **Enabled** set from the pinned policy root.
- Verify domain separation tags match `spec/tx_format.cddl`.
- Reject deprecated algs after the **hard-off** date.
- **P2P:**
- Handshake **MUST** advertise supported KEMs ordered by policy preference.
- AEAD keys derived via `HKDF-SHA3-256`, transcript hash includes `algPolicyRoot`.
- **SDKs/Wallets:**
- Embed the **human** policy and validate against the **on-chain root**; warn if drift.
- Expose feature flags to enable experimental algs only on testnets/devnets.

---

## 8) Versioning & Compatibility

- Policy object includes:
- `version`: semver of the policy format,
- `updated_at`: RFC3339,
- `network`: chain label (e.g., `animica:1`),
- `entries[]`: `{kind, alg_id, name, status, priority, deprecated_at?, disable_at?}`.
- **Compatibility**: Minor version increments allow additive changes (new alg enabled). Major version required for breaking layout changes.

---

## 9) Quality Gates (before enabling an algorithm)

- **Spec conformance:** KATs/vectors pass (sign/verify or KEM enc/dec).
- **Interoperability:** SDKs (TS, PY, RS), wallet-extension, and P2P handshake pass integration tests.
- **Performance:** Benchmarks show acceptable latency/size vs policy budgets.
- **Security review:** Implementation vetted (constant-time where applicable, no secret-dependent branches, side-channel notes).

---

## 10) Governance Checklist (PQRotation)

- [ ] Machine policy JSON updated and validated (`spec/alg_policy.schema.json`).
- [ ] Merkle root computed; posted in proposal.
- [ ] Test vectors for each affected algorithm.
- [ ] Benchmarks on ref hardware; deltas vs previous release.
- [ ] SDKs/wallet updated; UI states (enabled/deprecated/disabled) aligned.
- [ ] P2P compatibility matrix updated.
- [ ] Communications plan (T0/T+6m/T+12m) published.

---

## 11) Worked Example

**Proposal:** Add `falcon-1024` (signature) as *Experimental (Disabled)* on testnet.

- Update `pq/alg_policy/example_policy.json` with entry:
```json
{
  "kind": "signature",
  "alg_id": "falcon1024",
  "name": "Falcon-1024",
  "status": "disabled",
  "priority": 30,
  "network": "animica:2"
}

	•	Compute root; deploy to testnet only; run cross-SDK vectors and capture benches.
	•	After 3 months of soak and review, a second proposal may enable it on testnet; mainnet remains untouched until a future cycle.

⸻

12) Files & Tools
	•	Human policy (authoritative prose): spec/pq_policy.yaml
	•	Machine policy (authoritative data): pq/alg_policy/*.json
	•	Schema: spec/alg_policy.schema.json
	•	Root builder: pq/alg_policy/build_root.py
	•	IDs: pq/alg_ids.yaml
	•	Runtimes: pq/py/*, p2p/crypto/*, wallet-extension/src/background/pq/*
	•	Tests: pq/tests/*, cross-SDK tests under sdk/*/tests

⸻

13) Appendix — Policy JSON Skeleton

{
  "version": "1.0.0",
  "network": "animica:1",
  "updated_at": "2025-01-01T00:00:00Z",
  "entries": [
    { "kind": "signature", "alg_id": "dilithium3", "name": "CRYSTALS-Dilithium Level 3", "status": "enabled", "priority": 10 },
    { "kind": "signature", "alg_id": "sphincs_shake_128s", "name": "SPHINCS+ SHAKE-128s", "status": "enabled", "priority": 20 },
    { "kind": "kem", "alg_id": "kyber768", "name": "CRYSTALS-Kyber-768", "status": "enabled", "priority": 10 }
  ]
}

Statuses: enabled | disabled | deprecated
Priority: lower is preferred when multiple algs are enabled for the same role.

