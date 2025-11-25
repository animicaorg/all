# AI_V1 Proofs — TEE attestations, redundancy & trap audits

**Status:** Draft, production-intent  
**Scope:** Verifier-facing specification for the AI useful-work proof (version 1).  
**Consumes:** CBOR envelope per `proofs/schemas/proof_envelope.cddl`, AI attestation bundle per `proofs/schemas/ai_attestation.schema.json`.  
**Produces:** `ProofMetrics.AI` used by PoIES scorer; compact receipt for `proofsRoot`.

> PoIES acceptance remains \( S = -\ln u + \sum \psi \ge \Theta \).  
> The AI_V1 proof contributes through a non-negative term \( \psi_{\text{AI}} \) derived from attested execution, trap audits, redundancy, and QoS. Caps and diversity rules are enforced by consensus policy (see `consensus/caps.py`).

---

## 1) Goals & threat model

**Goals**
- Attest that a specified AI task ran inside a trusted compute environment (TEE) *as specified*.
- Provide *auditable evidence* that (a) outputs correspond to inputs, (b) sufficient compute was actually performed, and (c) the run respected SLAs.
- Resist **result spoofing**, **lazy evaluation**, and **replay**.

**Adversary model**
- A malicious or misconfigured provider may return fabricated results, recycle prior runs, or attempt to bias outputs.  
- TEEs can fail or be out-of-date; quotes may be stale or revoked.  
- The protocol relies on vendor root CAs shipped in `proofs/attestations/vendor_roots/*` and on trap/ redundancy checks to push cost of forgery high.

---

## 2) Body — CBOR layout (informative)

Canonicalized by `proofs/cbor.py`. Precise JSON-Schema: `proofs/schemas/ai_attestation.schema.json`.

AIBodyV1 = {

Task & bindings

“chainId”:        uint,                  # CAIP-2
“height”:         uint,                  # block height the job binds to
“task_id”:        bstr .size 32,         # H(chainId|height|txHash|caller|payload) (capabilities/jobs/id.py)
“model_id”:       tstr,                  # declarative model/version id
“model_hash”:     bstr .size 32,         # code+weights digest (content address)
“input_commit”:   bstr .size 32,         # H(request payload), preimage optional
“output_commit”:  bstr .size 32,         # H(result bytes), preimage optional

TEE evidence bundle (one of SGX | SEV-SNP | CCA)

“tee”: {
“type”:         tstr,                  # “SGX” | “SEV_SNP” | “ARM_CCA”
“quote”:        bstr,                  # binary quote/report/token (per vendor)
“user_data”:    bstr,                  # reportdata/userData/claim binding
“mrenclave”?:   bstr .size 32,
“mrsigner”?:    bstr .size 32,
“policy”: {                            # min TCB, required features, revocation lists
“min_tcb”:    tstr,
“features”:   [tstr, …],
“crls”?:      [bstr, …]
}
},

Redundancy & traps

“redundancy”: {
“run_id”:       bstr .size 16,         # provider-local
“k”:            uint,                  # min agree
“n”:            uint,                  # runs issued
“digests”:      [ bstr .size 32, … ] # per-run result hash (≥ n)
},
“traps”: {
“seed”:         bstr .size 32,         # deterministic seed (from chain beacons)
“count”:        uint,                  # # hidden tests mixed into workload
“passes”:       uint,                  # observed pass count
“receipt”:      bstr                   # provider-signed trap receipt (optional)
},

QoS

“qos”: {
“started_at”:   uint,                  # unix ms
“finished_at”:  uint,                  # unix ms
“latency_ms”:   uint,
“availability”: uint                   # percentage × 100 (0–10000)
},

Domain/versioning

“domain”:         tstr,                  # “AI_V1”
}

**Envelope:**
- `type_id = 0x02` (AI_V1)  
- `body = cbor(AIBodyV1)`  
- `nullifier = H("PROOF_NULLIFIER/AI_V1" || body)[:32]`

All hashes are SHA3-256 unless otherwise noted; domain tags are ASCII, exact-match.

---

## 3) Deterministic bindings

To prevent replay and cross-context reuse:
- **Task binding:** `task_id` is derived deterministically (see `capabilities/jobs/id.py`):  
  `task_id = H(chainId || height || txHash || caller || payload)`  
  The TEE ties `task_id`, `model_id`, `model_hash`, and `input_commit` into `user_data`.
- **Round anchoring:** `height` pins the attestation to a block interval; policy may enforce a **freshness window** Δ (e.g., ≤ 2 epochs).
- **Trap seed:** `seed = H("AI_TRAP_SEED" || beacon(height-1) || task_id)`.

---

## 4) TEE attestation verification

Verifier (`proofs/attestations/*`) performs:

1. **Parse & root-of-trust**
   - Select backend by `tee.type`.
   - Validate quote/report chain against vendor roots at `proofs/attestations/vendor_roots/*`.
   - Enforce `policy.min_tcb`, `policy.features`, CRLs/issuer identities.

2. **Measurement checks**
   - Check `mrenclave/mrsigner` (SGX) or `measurement` equivalents (SEV/CCA) against **allowlist** for `model_id` runtime container.
   - Reject stale/blacklisted measurements.

3. **User-data binding**
   - Extract `user_data` / `REPORTDATA` / CCA claims. Expect:
     ```
     user_data = H("AI_V1/BIND" ||
                   task_id || model_id || model_hash ||
                   input_commit || output_commit)
     ```
   - Reject if mismatch.

4. **Time / nonce / revocation**
   - Enforce quote freshness (e.g., ≤ 10 minutes since `started_at`) and nonces if present.
   - For SGX/TDX: validate QE/QvE identities; for SEV-SNP: check TCB version, VCEK; for CCA: COSE signatures & claims.

**Failure modes:** `AttestationError::<Reason>` (exposed via `ProofError`).

---

## 5) Redundancy (k-of-n agreement)

To raise the cost of fabrication, providers may run **n** replicas (possibly across distinct TEEs/regions) and report per-run digests.

Verification:
- Check `n ≥ k ≥ 1`, length of `digests` ≥ n, and that **at least k** digests equal `output_commit`.
- Optional *independence hint*: if multiple quotes are provided, enforce that their platform IDs / attester identities are *not identical* (policy-gated).
- Metric: `redundancy_score = min(n, n_max)` and `agreement = (#equal_to_output_commit) / n`.

---

## 6) Trap audits

**Idea:** Mix **hidden test prompts** (or canary inputs) deterministically seeded from chain randomness. Provider must pass a target ratio to avoid slashing/deweighting.

- Trap set generated off-chain by the network’s trap curator using `seed`.  
- The TEE receives both *user inputs* and *trap inputs*; only trap answers are *verifiable off-chain*.
- Body records `count` and `passes`. Verifier recomputes expected **answer keys** from the public trap corpus and `seed`, and checks the pass ratio.

Let:
- \( r = \frac{\text{passes}}{\text{count}} \)
- Policy threshold \( r_{\min} \) and confidence window \(m\).

Decision:

accept_traps := (count >= m) and (r >= r_min)

Metric emitted: `traps_ratio = r`. Failures degrade or nullify ψ contribution per policy.

---

## 7) QoS metrics

Computed from `qos`:
- `latency_ms`
- `availability` (0–10000 ⇒ 0.00–100.00%)
- Optional: throughput hints (`tokens_per_s`, `gpu_time_ms`) if present (ignored by verifier but may be used by AICF pricing).

Policy maps these into a *bounded multiplier* \( w_{\text{qos}} \in [w_{\min}, 1] \).

---

## 8) Metrics → ψ mapping (policy adapter)

`proofs/policy_adapter.py` converts verified evidence to **PoIES inputs**:

ProofMetrics.AI = {
ai_units:        float,  # abstract compute units (calibrated per model_id)
traps_ratio:     float,  # 0..1
redundancy_n:    uint,
redundancy_k:    uint,
agreement:       float,  # 0..1
latency_ms:      uint,
availability:    float,  # 0..100
}

A network policy (see `consensus/policy.py`) computes:

\[
\psi_{\text{AI}} = \min\Big( w_{\text{model}} \cdot U \cdot f_{\text{traps}}(r) \cdot f_{\text{red}}(k,n,a) \cdot f_{\text{qos}}(\ell, A),\; \text{cap}_{\text{AI}} \Big)
\]

- \(U\) = `ai_units` scaled by model-specific weights (from policy).  
- \(f_{\text{traps}}\) penalizes below-threshold ratios (zero if failed).  
- \(f_{\text{red}}\) rewards k-of-n agreement with diminishing returns.  
- \(f_{\text{qos}}\) reduces credit when latency/availability fall outside SLOs.  
- `cap_AI` is the per-type cap; also contributes to total Γ cap.

**Note:** ψ mapping is policy-only; the verifier only outputs metrics.

---

## 9) Nullifier, receipts, proofsRoot

- **Nullifier:** `H("PROOF_NULLIFIER/AI_V1" || body)[:32]` prevents replay within the *nullifier window*.  
- **Receipt (conceptual):**

AIReceipt = {
“nullifier”:   bstr .size 32,
“task_id”:     bstr .size 32,
“ai_units”:    u16.16,     # fixed-point
“traps_ratio”: u0.16,      # fixed-point
“k”:           uint,
“n”:           uint,
}

- **Leaf hash:** `H("PROOF_RECEIPT/AI_V1" || cbor(AIReceipt))`.  
- Receipts from all proof kinds are deterministically ordered → `proofsRoot`.

---

## 10) Verification algorithm (reference)

Pseudocode (see `proofs/ai.py` for authoritative behavior):

```python
def verify_ai_v1(env):
  body = decode_cbor(env.body, schema="ai_attestation.schema.json")
  # 1) schema/size
  assert body["domain"] == "AI_V1"
  # 2) bindings
  expect_ud = H(b"AI_V1/BIND" + body.task_id + enc(body.model_id)
                + body.model_hash + body.input_commit + body.output_commit)
  att = parse_tee(body.tee)
  verify_attestation(att, policy=body.tee["policy"])
  assert att.user_data == expect_ud
  # 3) redundancy
  k, n = body.redundancy.k, body.redundancy.n
  digests = body.redundancy.digests
  assert n >= k >= 1 and len(digests) >= n
  agreement = sum(1 for d in digests if d == body.output_commit) / n
  # 4) traps
  seed = H(b"AI_TRAP_SEED" + beacon(body.height - 1) + body.task_id)
  r = recompute_trap_ratio(seed, body.traps)
  assert body.traps.count >= POLICY.min_traps and r >= POLICY.min_ratio
  # 5) metrics
  units = model_units(body.model_id, body.model_hash)   # policy table
  metrics = ProofMetrics.AI(units, r, n, k, agreement,
                            body.qos.latency_ms, body.qos.availability/100.0)
  # 6) nullifier
  assert env.nullifier == H(b"PROOF_NULLIFIER/AI_V1" + env.body)[:32]
  return metrics

Failures raise ProofError::{Schema,Attestation,TrapFail,Redundancy,Binding}.

⸻

11) Privacy
	•	Only commitments to inputs/outputs are required on-chain. Preimages may be provided off-chain for audits or to the requester via capabilities.
	•	Trap corpora are public but selection is seeded per-round; do not publish which inputs were traps until after settlements if frontrunning is a concern.

⸻

12) Interactions & economics
	•	Capabilities bridge: capabilities/jobs/attest_bridge.py normalizes provider bundles to AIBodyV1.
	•	AICF pricing: aicf/economics/pricing.py converts ai_units (+ QoS) into rewards; aicf/sla/* re-evaluates traps/QoS for slashing.
	•	Settlement: See aicf/economics/settlement.py and aicf/integration/proofs_bridge.py.

⸻

13) Parameters & policy

Configured in network params:
	•	Allowlisted model_id → model_hash(es) → measurement(s).
	•	Minimum trap count & threshold (m, r_min).
	•	Freshness windows for quotes and binding heights (Δ).
	•	Per-model unit tables and per-type caps (Γ_AI).
	•	Redundancy caps: n_max, diminishing weights.

⸻

14) Security considerations
	•	Quote freshness & revocation: Enforce short windows; update vendor roots/CRLs via governance.
	•	Binding completeness: All of task_id, model_id, model_hash, input_commit, output_commit must be hashed into user_data.
	•	Lazy providers: Failing traps yields zero ψ and may trigger slashing.
	•	Correlated redundancy: Enforce independence hints where feasible (different TEEs/hosts/regions).
	•	Side channels: This spec does not address confidentiality side channels; deployments should set TEE configuration to recommended hardening levels.

⸻

15) Test vectors
	•	proofs/test_vectors/ai.json — parse/verify success & failure modes.
	•	aicf/tests/test_ai_traps_qos.py — trap/QoS thresholds.
	•	proofs/tests/test_ai_attestation.py — SGX/SEV/CCA parsing & policy failures.

⸻

16) Versioning
	•	domain = "AI_V1" and type_id = 0x02 are fixed for this version.
	•	Future versions (AI_V2) may introduce:
	•	Inline succinct attested logs (e.g., event transcripts).
	•	SNARK-wrapped traces for certain models.
	•	Stronger multi-party redundancy attestations.

⸻

Notation & hashing
	•	H(x) = SHA3-256; all CBOR is canonical.
	•	Fixed-point encodings follow consensus/math.py.

