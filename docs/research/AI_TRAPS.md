# AI “Traps”: Red-Team Methodology & Metrics

This note specifies the **trap-based red-team framework** used to harden and measure the integrity of AI compute in Animica’s PoIES model. “Traps” are **audit queries**—small tasks with *cryptographically blinded* ground truth—that are interleaved with normal AI jobs to detect dishonest execution (e.g., copying, shortcutting, or ignoring prompts), poor fidelity, or policy-violating behavior. Results feed into the **proofs/ai** verifier as inputs to ψ (utility) via the `traps_ratio`, `redundancy`, and `QoS` metrics, and into AICF SLA evaluation.

> TL;DR — We randomly seed **trap prompts with hidden answers** and require providers to return correct, timely outputs. We estimate an honest success rate \(p_\mathrm{honest}\) via calibration, then require a **per-batch lower confidence bound** on trap accuracy to exceed thresholds. Failing providers are de-weighted (ψ↓), slashed (AICF), or excluded.

---

## 1) Goals

1. **Integrity detection.** Identify low-effort/cheating execution (e.g., parroting, dummy text, “always pass” rigs).
2. **Quality guardrails.** Enforce minimal task fidelity for the claimed capability (model class, hardware).
3. **Market signal.** Provide verifiable metrics for provider ranking, pricing, and payouts under AICF.
4. **Composability.** Expose simple scalars (`traps_ratio`, `qos`, `latency`) and compact receipts consumable by on-chain PoIES scorers.

---

## 2) Threat Model

- **Adversaries:** Providers faking or degrading results, using faster but lossy heuristics, or reusing stale outputs. Collusion across providers is in-scope.
- **Capabilities:** See only plaintext prompts; do not know trap identity or ground truth. Can attempt *memorization* over time.
- **Assumptions:** Enclave/attestation (TEE) is present for AI v1, but traps remain necessary (attestation ≠ correctness). Random seeds derive from the **randomness beacon** for unpredictability.

---

## 3) Trap Design Principles

**T1. Blind ground truth.** Answers derive from secrets not present in the prompt (e.g., keyed checksum, salted hash, stego hints) or from deterministic programs (oracle).  
**T2. Distribution matching.** Traps resemble the job distribution (task types, difficulty, modalities).  
**T3. Cheap verification.** On verifier side, O(1) to score (e.g., hash compare, scorer function), no expensive model inference.  
**T4. Differential probing.** Mix:
- **Deterministic traps:** e.g., “compute SHA3-256 of this string” (should be exact).  
- **In-context traps:** subtle instructions requiring reasoning (“return the 7th prime after X”).  
- **Style traps:** structured formats; schema conformance.  
- **Policy traps:** prohibited output if prompt forbids it (safety adherence).

**T5. Adaptive sets.** Refresh trap families and seeds to resist memorization; versioned catalogs.

---

## 4) Generation & Catalog

- **Catalog** \( \mathcal{C} = \{c_i\} \): trap families with generator \(G_i(s, \phi)\to (prompt, oracle)\), where \(s\) is seed, \(\phi\) family parameters.  
- **Seed source:** \(s = \mathrm{H}(\text{beacon}_h \| \text{jobId} \| \text{providerId})\).  
- **Balance:** Choose family weights to match target mix; record family id & version.  
- **Namespace:** Traps are standard jobs with a **trap bit** concealed; providers cannot distinguish pre-execution.

**Example family (deterministic math)**
```text
G_math(s): 
  draw a,b from PRF(s)
  prompt: "Return (a * b + 17) % 997, digits only."
  oracle:  ((a*b + 17) mod 997) as string


⸻

5) Injection Policy

Define trap rate ( r \in (0,1) ) as fraction of jobs per batch that are traps.
Guideline:
	•	Low-load: ( r=0.10{-}0.20 ) for tight confidence with small batches.
	•	High-load: ( r=0.02{-}0.05 ) with rolling windows.

Choose window size (W) (jobs) and maintain moving counts ((T, C)) = (#traps, #correct).

⸻

6) Metrics & Statistical Tests

6.1 Core scalars
	•	Trap accuracy: ( \hat{p} = C/T ) if (T>0); else undefined.
	•	Lower confidence bound (LCB) using Wilson score at confidence (1-\alpha):
[
\mathrm{LCB}_\alpha(\hat{p},T) =
\frac{\hat{p} + \frac{z^2}{2T} - z\sqrt{\frac{\hat{p}(1-\hat{p})}{T} + \frac{z^2}{4T^2}}}{1 + \frac{z^2}{T}}
]
where (z = \Phi^{-1}(1-\alpha/2)) (e.g., (z=3.29) for 99.9%).
	•	Latency QoS: median/percentile latency vs SLO; convert to [0,1] score by a soft clamp:
[
\mathrm{qos}_\mathrm{lat} = \exp(-\lambda \cdot \max(0, \mathrm{p95} - \mathrm{SLO}))
]
	•	Format QoS: fraction of outputs passing schema/contract checks.
	•	Redundancy agreement: when K-way parallelism is used, pairwise agreement rate after canonicalization.

6.2 Decision thresholds
	•	Accept if ( \mathrm{LCB}{0.001}(\hat{p},T) \ge \tau\mathrm{trap} ) (e.g., 0.90) and QoS scores ≥ policy minima.
	•	De-weight if ( \mathrm{LCB} < \tau_\mathrm{trap} ) but not catastrophic (ψ scaled by factor (<1)).
	•	Slash/Jail if sustained failures across windows or explicit policy traps violated.

6.3 Mapping to PoIES ψ

Let (u_\mathrm{ai}) be base AI utility (units from benchmarks). Define penalty factor:
[
\pi = \min\Big(1,\ \max\big(0,\ a \cdot \mathrm{LCB} + b \cdot \mathrm{qos} + c \cdot \mathrm{agree} - d\big)\Big)
]
Then ( \psi_\mathrm{AI} = u_\mathrm{ai} \cdot \pi ). Coefficients ((a,b,c,d)) are network policy.

⸻

7) Calibration (Honest Baselines)
	•	Run trusted reference providers to estimate (p_\mathrm{honest}) per family & difficulty.
	•	Choose (\tau_\mathrm{trap}) such that honest providers pass with high probability (≥ 99.5%) at chosen (T).
	•	Maintain per-family drift monitors; re-fit if model/hardware classes change.

⸻

8) Red-Team Campaigns

Periodic campaigns expand and evaluate the catalog.

Cycle
	1.	Design new families targeting observed evasions (e.g., regex-only solvers, pattern mimics).
	2.	Adversarial prompting to exploit model weaknesses (instruction-following edge cases, tool-use confusion).
	3.	Offline bake-off across open weights and vendor APIs; compute discriminative power (AUROC) per family.
	4.	Shadow deploy at low weight; observe ROC vs false positives in production.
	5.	Promotion if stable; otherwise iterate.

KPIs
	•	Detection power at 1% FPR (≥ 95%).
	•	Provider cost overhead (trap generation + scoring ≤ 1% of job cost).
	•	Catalog freshness (≥ 20% families updated quarterly).

⸻

9) Redundancy & Consistency

For high-stakes jobs, request (K\ge 2) independent providers:
	•	Majority check for non-trap jobs: canonicalize output and require majority agreement, else downgrade ψ.
	•	Gold traps (deterministic): must be exact; disagreement is strong evidence of cheating or bugs.

⸻

10) Receipts & Attestations

Each trap yields a compact TrapReceipt:
	•	family_id, version, seed commitment (H(s))
	•	prompt hash, oracle hash, provider output hash
	•	verdict (pass/fail), latency, size
	•	TEE quote (if present), chain height, job id

Receipts are aggregated into the AIProof body (see proofs/ai.py) where only hashes and counts are needed for on-chain policy.

⸻

11) Privacy & Safety
	•	Do not embed user PII in traps. Prefer synthetic data or public corpora with clean licensing.
	•	Avoid prompt content that causes unsafe completions; policy traps should check for refusal compliance rather than elicit unsafe content.

⸻

12) Implementation Sketch

Scoring pseudo-code

def score_traps(trap_results, alpha=1e-3, tau=0.90):
    # trap_results: list[(family_id, ok: bool, latency_ms: int, schema_ok: bool)]
    T = len(trap_results)
    C = sum(1 for _, ok, *_ in trap_results if ok)
    if T == 0:
        return {"status": "indeterminate", "psi_scale": 0.0}

    p_hat = C / T
    z = 3.29  # ~99.9% Wilson
    denom = 1 + z*z/T
    center = p_hat + z*z/(2*T)
    rad = z * ((p_hat*(1-p_hat)/T + z*z/(4*T*T))**0.5)
    lcb = (center - rad) / denom

    # QoS proxies
    p_schema = sum(1 for *_, schema_ok in trap_results if schema_ok) / T
    p95_lat = percentile([lat for *_, lat, _ in trap_results], 95)
    qos_lat = math.exp(-0.001 * max(0, p95_lat - 2000))  # 2s SLO example
    qos = 0.5 * p_schema + 0.5 * qos_lat

    # Policy
    if lcb < tau:
        status = "fail"
    else:
        status = "pass"

    # Map to psi scale
    a,b,c,d = 0.7, 0.3, 0.0, 0.6
    psi_scale = min(1.0, max(0.0, a*lcb + b*qos - d))
    return {"status": status, "lcb": lcb, "qos": qos, "psi_scale": psi_scale}


⸻

13) Operational Policy (AICF & Proofs)

AICF SLA
	•	Window (W=500) jobs, trap rate (r=0.05) ⇒ (T\approx25) traps/window.
	•	Require ( \mathrm{LCB}_{0.001} \ge 0.90 ) and QoS ≥ 0.8.
	•	Two consecutive failures ⇒ temporary jail; stake slashing per policy.
	•	Publish per-provider dashboards: trap family breakdown, latencies, agreement.

Proof Acceptance (consensus)
	•	AIProof includes: total traps (T), passes (C), Wilson LCB, QoS scalars, and receipt root.
	•	Validator recomputes hashes and verifies bounds from spec/poies_policy.yaml.

⸻

14) Evasion & Counter-Evasion
	•	Memorization: Rotate seeds (beacon-derived), version families, include one-shot generators.
	•	Format-only bots: Include traps requiring content-dependent correctness, not just JSON shape.
	•	Timing spoofing: Compare declared runtime vs enclave timers (if TEE); cross-check network RTTs.
	•	Selective compute: Use canary prompts with withheld reward if detection occurs (economic disincentive).
	•	Proxy to stronger model: Allowed if QoS meets or exceeds SLO; economic pricing handles cost—but provider must still pass traps.

⸻

15) Governance & Transparency
	•	Publish non-sensitive family descriptions and historical thresholds.
	•	Keep seeds, specific instances, and selection logic private until after windows finalize.
	•	Allow third-party audit by releasing hashed catalogs and beacons; reveal trap instances post-epoch.

⸻

16) Checklists

Design
	•	Blind ground truth and cheap verification
	•	Difficulty calibrated to target model class
	•	Schema and safety dimensions covered
	•	Regeneration & rotation plan

Ops
	•	Seed via unbiased beacon
	•	Windowed Wilson bounds computed and stored
	•	Receipts aggregated with proofs, roots pinned
	•	Dashboards & alerts on LCB/QoS dips

⸻

17) Example Numbers

With (T=25), (C=24) ⇒ (\hat{p}=0.96). Wilson LCB(_{0.1%}) ≈ 0.87 fails τ=0.90.
With (T=40), (C=38) ⇒ (\hat{p}=0.95). LCB ≈ 0.91 passes.
Implication: increase trap count or improve accuracy for robust pass.

⸻

Appendix A: Family Ideas (Non-exhaustive)
	•	Hash-math (exact numeric answers)
	•	Keyed extraction (return token at index f(s) in provided data)
	•	Instruction-priority (obey low-salience constraint at the end)
	•	Refusal-required (policy trap: must refuse)
	•	Program synthesis micro-tasks (tiny regex/JSON patch with verifiable oracle)
	•	Vision-text (OCR of synthetic CAPTCHAs with known ground truth; when modality enabled)

⸻

Summary. Trap-based auditing gives a statistically strong, cheap-to-verify signal about AI job honesty and fidelity. Combined with TEE attestations, redundancy, and QoS measures, the framework makes cheating economically unattractive and measurably reduces the risk of low-quality outputs affecting consensus utility (ψ).
