# Proofs Module — Overview

> This document introduces the **proofs/** module, the canonical envelope format,
> and the five proof kinds used by Animica's PoIES consensus:
> **HashShare**, **AIProof**, **QuantumProof**, **StorageHeartbeat**, and **VDFProof**.
> It explains how proofs are produced, validated, converted into measurable
> metrics, and mapped into consensus scoring inputs \(\psi\) used by block
> acceptance \(S = -\ln u + \sum \psi \ge \Theta\).

---

## 1) Scope & goals

- Provide **deterministic** parsers, schema checks, and verifiers for each proof kind.
- Emit **ProofMetrics** (objective, numeric) that the consensus layer can consume.
- Enforce **domain separation**, **header binding**, **nullifier/anti-replay**, and
  **policy caps** (indirectly, via consensus).
- Keep all data flows **auditable** via canonical hash domains and round-trip
  encodings (CBOR/msgspec & JSON-Schema types).

Related code:
- `proofs/types.py` — envelopes and per-proof bodies (dataclasses).
- `proofs/cbor.py` — canonical encoding/decoding & schema checks.
- `proofs/registry.py` — type dispatch; checksum type→schema mapping.
- `proofs/metrics.py` — measurable metrics emitted by verifiers.
- `proofs/nullifiers.py` — per-kind nullifier derivation.
- `proofs/hashshare.py`, `proofs/ai.py`, `proofs/quantum.py`, `proofs/storage.py`, `proofs/vdf.py` — verifiers.
- `proofs/policy_adapter.py` — map ProofMetrics → \(\psi\) inputs (no caps here).

---

## 2) Canonical objects & envelope

All proof kinds are carried in a **generic envelope**:

ProofEnvelope {
type_id: uint16,        # Enumerates HashShare / AI / Quantum / Storage / VDF
body: bytes,            # Well-typed, canonical CBOR for the specific proof kind
nullifier: bytes32,     # Domain-separated anti-replay tag derived from body
}

- **type_id**: defined in `proofs/types.py` and mirrored in `consensus/types.py`.
- **body**: verified against a per-type schema:
  - CBOR CDDL: `proofs/schemas/*.cddl` (HashShare, Storage, VDF, envelope).
  - JSON-Schema: `proofs/schemas/*schema.json` (AI/Quantum attest bundles).
- **nullifier**: computed via `proofs/nullifiers.py` and **must** match the recomputation during verification.

### Domain separation

Each hashing step uses explicit domain tags (see `proofs/utils/hash.py`):
- `PROOF_NULLIFIER/<ProofKind>`
- `HEADER_BIND/<fields>`
- `AI_ATTEST/<provider>`
- `QPU_ATTEST/<provider>`

This prevents cross-context collisions and replay across proof families.

---

## 3) Verification pipeline

The top-level verification performs strict schema & bounds checks before intensive work:

```text
Envelope → (parse, schema) → type dispatch
        → per-kind verifier (attestations / math / target checks)
        → ProofMetrics (objective numbers)
        → policy_adapter (convert → ψ inputs; no caps)
        → consensus/scorer applies caps/Γ/diversity → ψ

Sequence (illustrative)

Miner/Provider                       Node / Verifier                     Consensus
--------------------------------------------------------------------------------------------
Build body  ─────┐
Compute nullifier├─► Envelope ──► Parse & schema ──► Verify(kind) ──► ProofMetrics ──► map→ψ_inputs
                 └─────────────────────────────────────────────────────────────────► caps/diversity
                                                                                └─► Σψ
Header/nonce bind ─────────────────────────────────────────────────────────────────► S = -ln u + Σψ


⸻

4) Proof kinds

4.1 HashShare
	•	Purpose: classic PoW-style u-draw with a target check derived from the current header template.
	•	Security: relies on preimage resistance; bound to header fields (height, mixSeed, nonce domain).
	•	Metrics: d_ratio (difficulty/share ratio), pass/fail target, timing info may be recorded.
	•	Linking: includes a header binding to prevent transplanting across templates.

4.2 AIProof
	•	Purpose: attest that a useful AI job (model inference/training shards) was performed under a secure/externalized
trust base.
	•	Security: TEE attestation (SGX/TDX/SEV-SNP/CCA) proof of code/measurement; traps receipts and QoS/redundancy controls.
	•	Metrics: ai_units, traps_ratio, redundancy, qos_score, latency buckets.
	•	Notes: evidence bundles validated via vendor roots in proofs/attestations/vendor_roots/.

4.3 QuantumProof
	•	Purpose: attest quantum job execution with trap-circuit checks and provider identity certs.
	•	Security: provider X.509/EdDSA/PQ hybrid identity; trap outcomes & benchmark scaling.
	•	Metrics: quantum_units, traps_ratio, qos_score, success/failure bounds.

4.4 StorageHeartbeat
	•	Purpose: periodic proof-of-storage (PoSt-style) heartbeats, optionally with retrieval tickets.
	•	Security: proofs reference epochs/windows; optional retrieval checks (future extension).
	•	Metrics: redundancy, availability, qos, capacity indicators.

4.5 VDFProof (Wesolowski)
	•	Purpose: time-delay computation witness; useful as a verifiable time anchor / beacon mix input.
	•	Security: relies on class group/modular arithmetic hardness and verifier fast check.
	•	Metrics: vdf_seconds_equiv or iteration counts mapped to seconds via calibrated params.

⸻

5) Metrics → ψ mapping

Verifiers emit ProofMetrics that are free of policy judgements. The policy adapter:
	•	Normalizes across kinds (e.g., scale ai_units or quantum_units to a ψ-candidate).
	•	Adds no caps — those are enforced in consensus/caps.py after summation and diversity escort rules.
	•	Produces a traceable breakdown so operators can audit why a proof contributed ( \psi ).

⸻

6) Header binding & anti-replay
	•	Header binding (when applicable) seals a proof to specific header template material (e.g., mixSeed, chainId,
height). A proof built for header (H) cannot be reused on (H’).
	•	Nullifier: a deterministic digest of the body (and optionally public salts) prevents reuse across blocks even
under reorgs; consensus keeps a TTL set via consensus/nullifiers.py.

⸻

7) Size/time limits & DoS controls
	•	Strict schema sizes per field (byte arrays, vector lengths).
	•	Global envelope size ceiling; per-kind body ceilings.
	•	Verifier budget: wall-clock/time or instruction counters (implementation dependent) ensure progress under adversarial inputs.
	•	Early reject on:
	•	Schema mismatch / overflows
	•	Bad attest roots or chains
	•	Trap ratios below policy thresholds
	•	Unbound header / mismatched chain

⸻

8) Encoding & determinism
	•	CBOR encoding uses canonical map ordering and stable integer encodings (core/encoding/cbor.py, mirrored utilities).
	•	JSON inputs (for attest/evidence) are normalized before hashing (sorted keys, UTF-8, no insignificant whitespace).
	•	Round-trip guarantees are tested in proofs/tests/test_cbor_roundtrip.py.

⸻

9) P2P & DA interactions
	•	Gossip: lightweight validators run before decoding (topic-level checks in p2p/gossip/validator.py).
	•	Data Availability: large AI/Quantum artifacts are not in proofs; only compact digests + attest bundles.
Optional DA commitments may be referenced for audits.

⸻

10) Error taxonomy

Verifiers raise typed errors from proofs/errors.py, mapped to user-facing codes:
	•	SchemaError, AttestationError, ProofError, NullifierReuseError, with machine-readable reasons.
	•	RPC surfaces map these to structured JSON-RPC errors.

⸻

11) Test vectors & fixtures
	•	Focused per-kind vectors in proofs/test_vectors/*.json.
	•	Cross-module vectors in spec/test_vectors/proofs.json.
	•	Vendor fixtures are illustrative and not production roots; operators must update via documented processes.

⸻

12) Extending with new proof kinds
	1.	Define schema (CDDL or JSON-Schema).
	2.	Implement verifier that returns ProofMetrics.
	3.	Add nullifier derivation & domains.
	4.	Register in proofs/registry.py (type_id ↔ schema).
	5.	Map metrics in proofs/policy_adapter.py.
	6.	Add tests (schema, failure modes, vectors).
	7.	Update docs & policy guidance.

⸻

13) Reference layout

proofs/
├─ types.py
├─ metrics.py
├─ registry.py
├─ cbor.py
├─ nullifiers.py
├─ hashshare.py
├─ ai.py
├─ quantum.py
├─ storage.py
├─ vdf.py
├─ policy_adapter.py
├─ attestations/
│  ├─ tee/{sgx.py, sev_snp.py, cca.py, ...}
│  └─ vendor_roots/*.pem
└─ schemas/
   ├─ proof_envelope.cddl
   ├─ hashshare.cddl
   ├─ storage.cddl
   ├─ vdf.cddl
   ├─ ai_attestation.schema.json
   └─ quantum_attestation.schema.json


⸻

14) Security considerations (high level)
	•	Production deployments must pin vendor roots and rotate on vendor updates.
	•	Replay & malleability are mitigated by nullifiers, canonical encodings, and explicit domains.
	•	Fairness: ψ caps and diversity rules prevent a single proof family from monopolizing acceptance.
	•	Observability: Prometheus metrics in proofs/metrics.py surface verification rates and failures.

⸻

Further reading
	•	docs/spec/poies/POIES_OVERVIEW.md, SCORING.md, ACCEPTANCE_S.md, RETARGET.md
	•	docs/spec/ENCODING.md, docs/spec/RECEIPTS_EVENTS.md
	•	docs/spec/LIGHT_CLIENT.md (for VDF/beacon interplay)
	•	docs/spec/DA_ERASURE.md and docs/spec/MERKLE_NMT.md

