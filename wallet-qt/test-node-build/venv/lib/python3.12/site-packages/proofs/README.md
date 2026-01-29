# Animica `proofs/`

This module defines **useful-work proofs** for Animica and the **verifier side** logic that
turns each submitted proof into **objective, bounded metrics**. Those metrics are then
mapped into **ψ inputs** for the PoIES scorer (see `consensus/scorer.py`) and, after
caps/fairness are applied, into **block acceptance weight**.

It provides:

- **Canonical envelopes & schemas** (CBOR / JSON-Schema) for all proof kinds.
- **Verifiers** for each proof kind with **deterministic results** (no network I/O, no clocks).
- **Nullifiers** and **domain separation** to prevent replay/duplication.
- **Metrics extraction** used by consensus-level weighting.
- **Policy adapter** to transform metrics → ψ-inputs consistent with `spec/poies_policy.yaml`.
- **Fixtures & test vectors** and **CLIs** for local inspection/build.

> Consensus-critical: everything that influences ψ, nullifiers, or envelope hashing lives here.
> Non-consensus helpers (pretty printing, tracing) are kept outside scoring paths.

---

## Proof kinds (overview)

| Kind                | File                             | What it proves (one-liner)                                                                                   |
|---------------------|----------------------------------|---------------------------------------------------------------------------------------------------------------|
| **HashShare**       | `proofs/hashshare.py`            | You sampled a nonce \(u\) (via header domain) whose transformed score \(d\_ratio\) contributes toward Θ.     |
| **AIProof v1**      | `proofs/ai.py`                   | A **TEE-attested** AI job was executed with redundancy and traps; outputs match commitments & QoS thresholds. |
| **QuantumProof v1** | `proofs/quantum.py`              | A **QPU-attested** circuit batch was executed; **trap-circuit** outcomes validate claimed fidelity/QoS.      |
| **Storage v0**      | `proofs/storage.py`              | A **PoSt heartbeat**: sectors were sealed/kept online; optional retrieval-ticket bonus path.                 |
| **VDF (bonus)**     | `proofs/vdf.py`                  | A **Wesolowski** proof over the beacon input verifying wall-clock hardness (seconds-equivalent).             |

Each proof kind returns a **`ProofMetrics`** object capturing the consensus-relevant
numbers only (see `proofs/metrics.py`). These are later mapped to ψ inputs.

---

## Data model

### Envelopes

All proofs are carried in a canonical **envelope**:

type_id : u16            # enumerates proof kind (see consensus/types.py)
body    : bytes          # CBOR-encoded proof body, schema per kind
nullifier : bytes32      # H(domain | canonical(body fields …))

- Envelope schema: `proofs/schemas/proof_envelope.cddl`
- Body schemas: `proofs/schemas/*.cddl` (CBOR) or `*.schema.json` (JSON)

### Hashing & domains

Domain-separated hashing is used everywhere (see `spec/domains.yaml`). Every verifier
computes the same **canonical hash** of the **decoded** structure, never over raw buffers.
This ensures replay-protection & cross-implementation stability.

---

## Trust roots & policy roots

- **TEE roots** (SGX/TDX, SEV-SNP, Arm CCA, TPM/DICE) are bundled under
  `proofs/attestations/vendor_roots/*`. These are **examples**; real networks must pin
  the exact PEMs and versions via governance.
- **Quantum provider roots** (`proofs/attestations/vendor_roots/example_qpu_root.pem`) model
  a QPU vendor/provider identity chain.
- **PQ alg-policy root**: verifiers can require that signatures & KEM handshakes respect the
  current **alg-policy Merkle root** (see `spec/alg_policy.schema.json`, `pq/alg_policy/*`).
- **Network params** (caps, weights, Γ) come from `spec/poies_policy.yaml` and are loaded by
  consensus; this module is policy-agnostic except in the **policy adapter**.

> **Important:** Shipping/updating trust roots changes consensus only if those roots are part of
> the consensus configuration (pinned in chain params). Keep clear release notes.

---

## Security model (per kind)

### HashShare

- **Binding:** The nonce domain includes header fields (height, prev hash, Θ, mixSeed, chainId).
- **Score:** The verifier recomputes \(d\_ratio = \frac{\text{target}}{\text{observed}}\) from the
  header template → shares target and the actual digest threshold.
- **Edge cases:** Reject stale templates (epoch drift), wrong header binding, or non-monotone targets.

### AIProof v1

- **Inputs:** TEE attestation bundle (platform quote+TCB+PCK/QE/ARK chain), measurement of the
  runtime, output digest(s), **redundancy receipts** (M-of-N replicas), and **trap receipts**.
- **Checks:** 
  1) Parse & validate quote/report against **vendor roots**.  
  2) Bind measurement to job payload & model hash; verify **no debug flags** unless policy allows.  
  3) Verify redundancy set consistency; ensure *independent* enclaves/providers where required.  
  4) Validate **trap correctness ratio** ≥ threshold; traps are deterministically generated from
     the job id seed (see `capabilities/jobs/id.py`).  
  5) QoS (latency/availability) is measured in **coarse buckets** (deterministic transcript).
- **Outputs → metrics:** `ai_units`, `traps_ratio`, `redundancy`, `qos_bucket`.

### QuantumProof v1

- **Inputs:** Provider identity certificate (PQ+classic hybrid acceptable), attested job transcript,
  **trap-circuit** outcomes, circuit size/depth/shot counts, and environment flags (temperature, etc.)
- **Checks:** 
  - Validate provider cert chain against **QPU roots**.
  - Recompute expected traps success probability under claimed noise model bounds.
  - Check consistency across repetitions; apply **confidence bounds** on trap ratios.
- **Outputs → metrics:** `quantum_units` (function of depth×width×shots), `traps_ratio`,
  `qos_bucket` (uptime/latency/queueing).

### Storage v0 (Heartbeat)

- **Inputs:** Sealed sector commitments, proving time window, PoSt proof snippet or signed heartbeat,
  optional **retrieval ticket** attestation.
- **Checks:** Sector set freshness, window timing, commitment linkage; optional extra credit for
  successful retrieval challenge.
- **Outputs → metrics:** `storage_pledge`, `heartbeat_quality`, `retrieval_bonus?`.

### VDF (Wesolowski)

- **Inputs:** Input element (from beacon round), claimed iterations, proof \(\pi\).
- **Checks:** Standard Wesolowski verification; map iterations to **seconds-equivalent** via calibrated
  parameters (consensus-pinned).
- **Outputs → metrics:** `vdf_seconds`.

---

## Verification pipeline (call graph)

1. **Envelope decode** → `proofs/cbor.py` (schema-checked).
2. **Dispatch** by `type_id` → `proofs/registry.py`.
3. **Per-kind verify**:
   - HashShare → `hashshare.verify(...)`
   - AIProof → `ai.verify(...)`
   - QuantumProof → `quantum.verify(...)`
   - Storage → `storage.verify(...)`
   - VDF → `vdf.verify(...)`
4. **Nullifier** (anti-reuse) → `proofs/nullifiers.py` (domain-separated).
5. **Metrics** → `proofs/metrics.py` (strict, dimensioned units).
6. **Policy adapter** → `proofs/policy_adapter.py` (metrics → ψ-inputs: pure function).
7. **Receipt** material → `proofs/receipts.py` (for `proofsRoot`).

All verifiers are **pure**: no network calls, no time reads, no randomness; all evidence is in the body.

---

## Nullifiers & replay resistance

Each proof kind has a deterministic **nullifier**:

nullifier = H(
“ProofNullifier” |
chainId | type_id | height_hint |
canonical(body-fields)
)

- **Chain-bound**: includes `chainId` to prevent cross-network replay.
- **Height-bounded** (where applicable): includes a bounded height hint; `consensus/nullifiers.py`
  enforces a TTL **sliding window** (see policy).
- **Uniqueness**: re-submission of the same proof is detected & rejected.

---

## From metrics to ψ inputs

The **policy adapter** maps `ProofMetrics` to **ψ-inputs** consumed by the PoIES scorer.
These mappings are deterministic and **policy-agnostic** (no caps/weights here).

### Canonical inputs (per kind)

- **HashShare**:  
  - `d_ratio ∈ (0, 1]` — observed share difficulty / target share difficulty.  
  - ψ-inputs: `{ d_ratio }`.

- **AIProof v1**:  
  - `ai_units ≥ 0` — normalized compute units.  
  - `traps_ratio ∈ [0,1]`, `redundancy ∈ ℕ`, `qos_bucket ∈ {0..k}`.  
  - ψ-inputs: `{ units: ai_units, traps_ratio, redundancy, qos }`.

- **QuantumProof v1**:  
  - `quantum_units ≥ 0`, `traps_ratio ∈ [0,1]`, `qos_bucket`.  
  - ψ-inputs: `{ units: quantum_units, traps_ratio, qos }`.

- **Storage v0**:  
  - `storage_pledge ≥ 0`, `heartbeat_quality ∈ [0,1]`, `retrieval_bonus ∈ {0,1}`.  
  - ψ-inputs: `{ pledge, quality, bonus }`.

- **VDF**:  
  - `vdf_seconds ≥ 0`.  
  - ψ-inputs: `{ seconds }`.

### Example (purely illustrative; actual weights/caps in `spec/poies_policy.yaml`)

Let \(w\_{\text{AI}}\), \(w\_{\text{Q}}\), … be per-type weights and `cap_*` be caps:

- HashShare contribution (pre-caps):  
  \( \psi\_{\text{hash}} = d\_{ratio} \)

- AI contribution:  
  \( \psi\_{\text{ai}} = w\_{\text{AI}} \cdot \text{ai\_units} \cdot g(\text{traps\_ratio}) \cdot h(\text{qos}) \cdot r(\text{redundancy}) \)

- Quantum contribution:  
  \( \psi\_{\text{q}} = w\_{\text{Q}} \cdot \text{quantum\_units} \cdot g(\text{traps\_ratio}) \cdot h(\text{qos}) \)

- Storage contribution:  
  \( \psi\_{\text{st}} = w\_{\text{S}} \cdot \text{pledge} \cdot \text{quality} \cdot (1+\beta \cdot \text{bonus}) \)

- VDF contribution:  
  \( \psi\_{\text{vdf}} = w\_{\text{VDF}} \cdot \text{vdf\_seconds} \)

**Caps & fairness** are applied **later** in `consensus/caps.py` and `consensus/scorer.py`
(total-Γ cap, per-type caps, escort/diversity rules, α tuner).

---

## Threat model (high level)

- **Evidence forgery:** Prevented by verifying attestation chains & proofs locally against pinned roots.
- **Replay/duplication:** Prevented by nullifiers + TTL + envelope binding to chainId/height/template.
- **Corner-case ambiguity:** Eliminated by schema canonicalization & CBOR map-order normalization.
- **Overclaiming units/QoS:** Detected via traps, redundancy, transcript checks, and coarse bucketization.
- **Algorithm agility:** PQ alg-policy root binds allowed sig/KEM suites. Upgrades governed.

---

## CLI tools

- `proofs/cli/proof_verify.py`: verify any file (auto-detect).  
  ```sh
  python -m proofs.cli.proof_verify --in path/to/proof.cbor --explain

	•	Builders (dev/demo):
	•	proofs/cli/proof_build_hashshare.py
	•	proofs/cli/proof_build_ai.py
	•	proofs/cli/proof_build_quantum.py
	•	proofs/cli/proof_nullifier.py

⸻

Tests & vectors
	•	Focused vectors per kind in proofs/test_vectors/.
	•	Cross-module vectors in spec/test_vectors/proofs.json.
	•	Run:

pytest -q proofs/tests



⸻

Performance & determinism
	•	Verifiers are pure functions over inputs; no wall-clock, network, or filesystem.
	•	Heavy crypto paths can optionally use native/ accelerators via pyo3; fall back to Python.
	•	All floating-point is avoided in consensus paths: ratios and probabilities are fixed-point.

⸻

Versioning & compatibility
	•	Schemas carry a schemaVersion field.
	•	Any change that affects hashing / nullifiers / metrics must bump consensus version and be gated
through governance with test vectors.

⸻

Directory quick map

proofs/
  ├─ types.py, metrics.py, registry.py, cbor.py, nullifiers.py
  ├─ hashshare.py, ai.py, quantum.py, storage.py, vdf.py
  ├─ policy_adapter.py, receipts.py
  ├─ utils/{hash.py, math.py, keccak_stream.py, schema.py}
  ├─ attestations/{tee/*, vendor_roots/*}
  ├─ quantum_attest/{provider_cert.py, traps.py, benchmarks.py}
  ├─ schemas/*.cddl + *.json
  ├─ cli/*, fixtures/*, test_vectors/*, tests/*
  └─ py.typed


⸻

Implementation notes
	•	Keep envelope → verify → metrics → ψ-inputs strictly layered.
	•	If you add a new proof kind:
	1.	Define schema(s) and a deterministic nullifier.
	2.	Implement verify() returning ProofMetrics.
	3.	Extend policy_adapter to expose ψ-inputs (no caps).
	4.	Add test vectors + negative cases.
	5.	Register the type in registry.py and consensus/types.py.

