# PoIES Security Notes
**Grinding analysis, multi-work manipulation costs, and policy hardenings**

Related:
- Acceptance rule: `docs/spec/poies/ACCEPTANCE_S.md`
- Scoring & caps: `docs/spec/poies/SCORING.md`
- Retarget: `docs/spec/poies/RETARGET.md`
- Implementation: `consensus/{scorer.py,caps.py,validator.py}`, proofs in `proofs/*`

---

## 1) Model Recap

A candidate block is accepted when
\[
S \;=\; -\ln u \;+\; \sum_{j} \psi_j \;\;\ge\;\; \Theta,
\]
with \(u \sim \mathrm{Uniform}(0,1)\) from the nonce search (“hash share”) and \(\psi_j \ge 0\) contributions from verified useful-work proofs (AI, Quantum, Storage, VDF, etc.), post policy clipping (per-type caps and total-\(\Gamma\) cap).

Let \(T_\mathrm{eff} = e^{-(\Theta - \sum \psi)}\). For any single nonce trial, \( \Pr[\text{accept}] = T_\mathrm{eff}\).

---

## 2) Grinding on the nonce (u): advantage vs trials

If an adversary performs \(k\) independent nonce trials per header template, they obtain \(u_{\min} = \min(u_1,\dots,u_k)\) with cdf \(F_{u_{\min}}(x) = 1-(1-x)^k\). Then
\[
-\ln u_{\min} \;\stackrel{d}{=}\; \max_{i\le k}(-\ln u_i),\qquad
\mathbb{E}[-\ln u_{\min}] \;=\; H_k \;=\; 1 + \tfrac12 + \dots + \tfrac1k,
\]
the \(k\)-th harmonic number. **Marginal gain is logarithmic**:
\[
H_k \approx \ln k + \gamma + \tfrac{1}{2k} + O(k^{-2}).
\]
Thus, doubling hash trials only adds \(\approx \ln 2 \simeq 0.693\) to \(S\). This recovers standard “best-of-k” PoW grinding: diminishing returns discourage extreme search per template.

**Policy implication.** We do not limit local trial counts (that is the essence of competition), but we do:
- rate-limit header template staleness (work expiry) and rollover,
- clamp timestamp manipulation (see Retarget),
- reject oversize blocks or malformed proof packs early to avoid wasted verification.

---

## 3) Grinding on useful-work (Σψ): bounded by caps and economics

An adversary may try to prepare many proof candidates and pick the subset maximizing \(\sum \psi\) under policy caps. Let proof “raw scores” be \(x_j\) with mapping \(x_j \mapsto \psi_j = g_\theta(x_j)\) (monotone, nonnegative), subject to:
- per-proof-type caps \(\psi_j \le C_\mathrm{type}\),
- total \(\sum \psi \le \Gamma\),
- escort/diversity rules (e.g., require mix or diminishing returns),
- nullifier reuse rules (each proof has a nullifier with TTL; double-use rejected).

**Upper bound:** No block can exceed \(\sum \psi \le \Gamma\). Past that, only nonce grinding helps. Therefore, **multi-work grinding saturates** once the \(\Gamma\) cap is hit.

**Cost model:** If producing the \(m\)-th proof of a type has cost \(c_m\) (latency, compute, stake) and marginal benefit \(\Delta S_m = \Delta \psi_m\) after clipping/diversity, then the optimal number \(m^*\) satisfies approximately
\[
\Delta \psi_m \lesssim \text{(value of success prob increase)} \;\propto\; \frac{\partial}{\partial S}\big(1-e^{-(S-\Theta)_+}\big) = e^{-(\Theta-\sum\psi)}.
\]
As \( \sum\psi \) rises, **marginal value decays exponentially**; caps make \( \Delta \psi_m = 0 \) after a point. Hence economically rational grinders do not bloat proofs indefinitely.

---

## 4) Combined strategy: best of k (nonce) + best subset of proofs

Let \(S_k = \max_{i\le k}(-\ln u_i) + \sum\psi\_*\) where \(\sum\psi\_*\) is the best feasible sum under policy. Then:
- Increasing \(k\) gives \(+ \ln k\) asymptotically.
- Increasing proof inventory helps only until caps/diversity hit; beyond that returns are zero.
- **Therefore the rational frontier is: modest proof stacking to approach caps + logarithmic nonce grinding.**

---

## 5) Withholding & timing games

**Withholding**: An adversary may keep a near-threshold block and continue grinding hoping to cross \(\Theta\) with a strictly better \(S\) or to create a private lead.

- Expected gain from extra trials over short time \(\delta t\): \(\approx \ln(1+r\,\delta t)\) where \(r\) is attempt rate; tiny unless they are vastly faster.
- **Fork-choice** (weight-aware longest) and **retarget clamps** limit benefit of strategic latency.
- **Share proofs** do not enter consensus; they do not alter retarget. They only aid off-chain telemetry.

---

## 6) Cross-work manipulation & falsification

PoIES relies on **verifiers** that turn raw artifacts into metrics ⇒ ψ inputs:
- **AI/Quantum**: TEE or provider attestations + trap receipts + QoS. See `proofs/ai.py`, `proofs/quantum.py`.
- **Storage**: PoSt heartbeat windows and optional retrieval tickets, `proofs/storage.py`.
- **VDF**: Wesolowski verification, `proofs/vdf.py`.
- **HashShare**: header binding and target check, `proofs/hashshare.py`.

**Abuse resistance:**
- **Nullifier TTL**: each proof has a domain-separated nullifier (header binds where applicable). Reuse ⇒ reject.
- **Policy roots**: the accepted verifier/codecs and policy parameters are committed in headers; mismatched roots ⇒ reject.
- **Attestation roots**: trusted vendor roots and expected measurements pinned; unverifiable quotes ⇒ reject.
- **Size/time caps**: prevent DoS via enormous proofs or ultra-slow checks.
- **Diversity/escort**: discourages monoculture grinding of a single cheap work kind.

---

## 7) Economic framing: expected work to win

Let \(A\) be attempt rate (nonce trials/sec) and let feasible expected \( \mathbb{E}[e^{\sum \psi}] \) over admissible policies be \(M \le e^{\Gamma}\) (strict \(<\) due to clipping). Then the **per-second success probability** satisfies
\[
\lambda \;\propto\; A \cdot e^{-\Theta} \cdot M.
\]
Retarget sets \(\Theta\) so that network-wide \( \lambda \approx \lambda_{\text{target}} \). An individual’s relative success share is linear in its \(A\) and in its achievable \(M\) (bounded). Raising \(M\) close to caps is **cheaper at first** but **hits brick walls** (caps), whereas raising \(A\) maintains **log-diminishing returns per template** and linear returns per unit time.

---

## 8) Known attacks & mitigations

| Vector | Risk | Mitigation |
|---|---|---|
| Timestamp skew | Bias intervals → easier blocks | Drift bounds, trimmed means, EMA + deadband, per-step clamps |
| Proof replay | Inflate Σψ via reuse | Nullifier TTLs, header binding, policy-root checks |
| Fake attestations | Cheap ψ from forged TEEs/QPUs | Vendor roots pinned, measurement allowlists, strict parsers, tests |
| Payload bloat | DoS verifier | Size caps, quick schema checks before heavy crypto |
| Proof monoculture | Single cheap work dominates | Per-type caps, diversity/escort, α-tuner for long-run fairness |
| Private withholding | Higher expected S before publish | Limited by \(\ln k\) returns; fork-choice and network propagation reduce edge |
| Mempool shaping | Hoard txs to change ψ mix | ψ derives from *proofs*, not fees; tx selection bounded by gas/bytes, not ψ |

---

## 9) Quantitative guidance (parameters)

- **Total cap \(\Gamma\)**: pick so that even at cap, honest miners still need nonce luck (i.e., \(T_\mathrm{eff} < 1\)). Typical: \(\Gamma \in [2, 6]\) (units of S).
- **Per-type caps**: set to prevent one kind from single-handedly saturating \(\Gamma\).
- **Escort/diversity parameter \(q\)**: shape diminishing returns; enforce minimum blend.
- **Nullifier TTL**: at least several blocks; align with windowing in `consensus/nullifiers.py`.

---

## 10) Testing & simulation

- **Grind curves:** Verify \( \mathbb{E}[\max_{i\le k}(-\ln u_i)] \approx \ln k + \gamma \).
- **Cap saturation:** Simulate proof inventories; check Σψ never exceeds \(\Gamma\); winning probability flattens once cap is hit.
- **Adversarial traces:** Timestamp push/pull, malformed proofs, replay attempts.
- **Stability:** Run retarget under sudden \(A\) and ψ-mix step changes; no oscillation.

See `consensus/tests/test_scorer_accept_reject.py`, `consensus/tests/test_difficulty_retarget.py`, and `proofs/tests/*`.

---

## 11) Takeaways

- Nonce grinding yields **logarithmic** S gains; useful-work stacking yields gains bounded by **policy caps**.
- Combining both has sharply diminishing returns; **multi-work manipulation is expensive** and saturates.
- Correctness hinges on strict **verifiers, attestation roots, nullifiers, and policy commitments**.
- Retarget and fork-choice contain timing games; diversity rules contain monoculture risk.

