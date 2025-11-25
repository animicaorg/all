# Compute Capabilities — AI & Quantum
Design notes for deterministic off-chain compute flows, attestation, trap-circuit soundness, SLAs, and settlement. This complements the contract-facing API in **SYSCALLS.md** and the AICF module.

> TL;DR: Contracts enqueue AI/Quantum jobs. A deterministic **task_id** is derived. Providers execute off-chain. Completed work is proven back on-chain using the **proofs/** formats (AI or Quantum). Nodes validate, AICF prices/settles, and results become readable via `read_result(task_id)` in the next block.

---

## 1) End-to-end lifecycle

### 1.1 Enqueue (Tx time; deterministic)
- Contract calls `ai_enqueue` or `quantum_enqueue` (see _SYSCALLS.md_).
- Inputs are schema-checked & normalized; `task_id = H(chainId|height|txHash|caller|payload_digest)` (see `capabilities/jobs/id.py`).
- A `JobReceipt` is returned (CBOR), including `task_id`, `kind`, and `reserved_units`.
- Host reserves Treasury units deterministically (no I/O).

### 1.2 Matching & execution (off-chain; non-consensus)
- The **AICF** queue/service (off-chain component) pulls enqueued jobs via the adapters:
  - `capabilities/adapters/aicf.py` → `aicf/queue/*`
  - Providers are registered & staked (`aicf/registry/*`).
- The scheduler assigns jobs to eligible providers (stake, health, region, policy) with quotas (`aicf/queue/assignment.py`).
- Providers execute:
  - **AI**: model runtime inside a TEE (SGX/TDX | SEV-SNP | Arm CCA).
  - **Quantum**: trap-circuit experiment on a QPU with instrumented telemetry.

### 1.3 Proof publication (consensus)
- Provider (or relayer) assembles a **proof envelope** per `proofs/*`:
  - `proofs/ai.py` (TEE evidence, redundancy receipts, QoS)
  - `proofs/quantum.py` (provider cert, traps outcomes, QoS)
- Miners include these proofs in blocks; validators verify them using the `proofs/` registry and policies.
- `capabilities/jobs/resolver.py` consumes verified proofs → writes `ResultRecord` keyed by `task_id`.

### 1.4 Result availability (next block)
- After the block with the proof finalizes, `read_result(task_id)` returns the deterministic CBOR `ResultRecord`.
- AICF performs settlement (pricing, split) against Treasury (see §6).

---

## 2) Inputs & normalization (determinism)

_All inputs are bounded and canonicalized before hashing to `task_id`._

### 2.1 AI job
```cddl
; capabilities/schemas/job_request.cddl (AI subset)
AIReq = {
  kind: "ai",
  model: tstr / bytes .size (1..AI_MODEL_ID_MAX_BYTES),
  prompt: bytes .size (0..AI_PROMPT_MAX_BYTES),
  params: ? bstr .size (0..AI_PARAMS_MAX_BYTES), ; CBOR map (canonical)
  policy: { caps: { max_units: uint }, ? qos_min: uint } ; optional
}

	•	Canonicalization: model coerced to UTF-8 bytes; params normalized to canonical CBOR map; prompt left opaque.
	•	Digest: payload_digest = H(model | prompt | params_cbor | policy_cbor).

2.2 Quantum job

QReq = {
  kind: "quantum",
  circuit: bytes .size (1..Q_CIRCUIT_MAX_BYTES),  ; canonical JSON/CBOR
  shots: uint .le Q_MAX_SHOTS,
  params: ? bstr .size (0..Q_PARAMS_MAX_BYTES),
  traps: { fraction: float16, seed: bytes .size 32 } ; if client dictates
}

	•	Canonicalization: circuit JSON normalized (sorted keys, UTF-8, no NaN/Inf); or CBOR canonical.
	•	Digest: H(circuit_norm | shots | params_cbor | traps_cbor).

Rejection on non-canonical encodings → NotDeterministic.

⸻

3) Attestation (trust roots & structure)

3.1 AI (TEE-backed)

Evidence bundle (validated in proofs/ai.py):
	•	Platform: one of SGX/TDX, SEV-SNP, or Arm CCA
	•	Quote/Report + certificate chain validated against vendor roots under proofs/attestations/vendor_roots/*.
	•	Measurements (MRENCLAVE/TCB) matched to policy allowlist; configuration flags (debug/production) enforced.
	•	Work binding:
	•	workload_digest = H(model_id | prompt | params | code_hash | runtime_version)
	•	Appears in the TEE report’s user-data/claims.
	•	Redundancy & traps:
	•	Optional N-of-M redundant runs with pairwise digest agreement.
	•	Trap prompts: small, known challenges mixed into the batch; success ratio contributes to QoS.
	•	QoS: provider-reported metrics (latency, throughput) are bounded & cross-checked.

Verification re-computes digests and enforces policy (caps, allowlists). On success, ProofMetrics are emitted (see proofs/metrics.py).

3.2 Quantum (trap circuits)

Evidence bundle (validated in proofs/quantum.py):
	•	Provider identity: X.509/EdDSA/PQ-hybrid certificate, signed by configured root(s); maps to ProviderId in AICF.
	•	Trap-circuit outcomes:
	•	A fraction of submitted circuits are traps with known statistical signatures.
	•	The verifier checks empirical traps_ratio_pass against policy thresholds with confidence bounds (Clopper-Pearson/Beta).
	•	Scaling:
	•	Depth × width × shots → standardized quantum_units using proofs/quantum_attest/benchmarks.py (reference curves).
	•	QoS: timing windows, error rates, queue latency.

On success, metrics feed PoIES via proofs/policy_adapter.py.

⸻

4) Mapping to ψ-units (PoIES)

ProofMetrics  →  ψ-inputs adapter (proofs/policy_adapter.py) produces:
	•	AI: ai_units, redundancy_score, traps_ratio, qos_score
	•	Quantum: quantum_units, traps_ratio, qos_score

Consensus then applies per-proof caps, per-type caps, and the global Γ cap (see consensus/policy.py, consensus/caps.py) and aggregates into Σψ.

⸻

5) SLA evaluation

The AICF SLA engine (aicf/sla/*) computes pass/fail and trends:

5.1 Dimensions
	•	Traps ratio: min pass threshold t_min, with rolling window W_traps.
	•	QoS score: composite of latency, availability, reproducibility.
	•	Latency: target p95 within L_target.
	•	Success rate: share of jobs completed within SLA window.

5.2 Evaluation & penalties
	•	Windowed metrics → SLA verdict each epoch.
	•	Failures → SlashEvent via aicf/sla/slash_engine.py, magnitude per aicf/economics/slashing_rules.py.
	•	Repeated failures → jailing/cooldown in aicf/registry/penalties.py.

Note: SLA outcomes do not alter block validity; they affect provider economics and future eligibility.

⸻

6) Pricing & settlement

6.1 Pricing

aicf/economics/pricing.py converts units to base reward:
	•	AI: units = f(model, prompt_len, params_size, qos) → reward = R_ai(units)
	•	Quantum: units = g(depth, width, shots, traps_fraction) → reward = R_q(units)

6.2 Splits & treasury

aicf/economics/split.py applies (provider / treasury / miner) split. Settlement references on-chain proofs via aicf/integration/proofs_bridge.py:
	•	proof → task_id → JobRecord → Payout
	•	aicf/economics/settlement.py aggregates payouts per epoch; treasury/rewards.py credits balances.

6.3 Reserves & refunds
	•	The reserve returned at enqueue (reserved_units) is reconciled:
	•	If actual_units < reserve: refund difference.
	•	If actual_units > reserve: charge up to configured cap; otherwise reject job at queue time.

⸻

7) Security considerations
	•	Deterministic boundaries: All contract-visible effects (task_id, receipts, results) are determined solely by normalized payloads and verified proofs.
	•	Non-equivocation: task_id binds caller & height; replay across heights yields different ids.
	•	Proof replay: nullifiers in proofs/nullifiers.py prevent reuse across windows or blocks.
	•	Policy roots: PQ algorithm policy & compute policy roots are embedded in headers; mismatches cause validation failure.
	•	Data availability: Large outputs (if any) must be pinned via DA (blob_pin); read_result should only return bounded summaries or commitments.

⸻

8) Configuration knobs (non-consensus vs consensus)
	•	Consensus-level (must match network):
	•	Accepted attestation roots and versions (AI/Quantum)
	•	Trap thresholds & confidence bounds
	•	ψ mapping weights and caps; Γ cap
	•	Node-ops (locally configurable; cannot alter validity):
	•	AICF queue sizes, lease durations, retries
	•	Per-provider quotas
	•	Off-chain transport endpoints

⸻

9) Conformance & tests
	•	Unit tests:
	•	proofs/tests/test_ai_attestation.py, test_ai_traps_qos.py
	•	proofs/tests/test_quantum_attest.py
	•	aicf/tests/* (matcher, SLA, settlement)
	•	capabilities/tests/test_enqueue_* and test_result_resolver_from_proofs.py
	•	Vectors:
	•	proofs/test_vectors/ai.json, quantum.json
	•	aicf/test_vectors/assignments.json, settlement.json, slashing.json

⸻

10) Future extensions
	•	Multi-model attest (chain-of-tools) with weighted unit composition.
	•	ZK-proof-of-inference hooks: integrate optional verifier costs into ψ via zk_verify.
	•	Confidential results: return commitments + DA blobs with per-caller access policies (app-layer).

⸻

Normative references
	•	proofs/ (attestation, metrics, adapters)
	•	consensus/ (caps, scorer, difficulty)
	•	aicf/ (registry, queue, SLA, economics)
	•	capabilities/ (runtime, host, cbor, schemas)
	•	da/ (blob commitment, availability)

