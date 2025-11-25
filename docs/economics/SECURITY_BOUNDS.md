# Security Bounds Under PoIES Work Mixes (v1)

This note provides **order-of-magnitude** bounds and tunable formulas for attack costs when block acceptance uses **PoIES**:
\[
S \;=\; H(u) \;+\; \sum_{i \in \mathcal{T}} \psi_i \;\;\ge\;\; \Theta,
\]
where \(H(u) = -\ln u\) with \(u\sim \mathrm{Uniform}(0,1)\), \(\psi_i\) is the scored contribution from work type \(i\) (e.g., **Hash**, **AI**, **Quantum**, **Storage**, **VDF**), and \(\Theta\) is the current threshold (retargeted to hit a target inter-block rate).

PoIES aggregates heterogeneous proofs while enforcing **per-proof**, **per-type**, and **total** caps, plus optional **diversity escort rules**. These mechanisms bound the advantage obtained by concentrating spend into a single work type.

---

## 1) Notation & Policy Knobs

- Work types: \(\mathcal{T}=\{\text{hash},\text{ai},\text{quantum},\text{storage},\text{vdf}\}\).
- Score addends: \(\psi_i \ge 0\), capped by:
  - **Per-proof** caps \(c_i\): individual proof contribution bound.
  - **Per-type** caps \(C_i\): total for type \(i\) in a candidate block.
  - **Global** cap \(\Gamma\): \(\sum_i \psi_i \le \Gamma\).
- Diversity/escort: require at least \(q\) distinct types to exceed small floor(s) before full \(\psi\) is credited; prevents monoculture boosts.
- Retarget: \(\Theta\) updated via EMA (see `docs/spec/poies/RETARGET.md`) to stabilize inter-block time.

**Acceptance probability per attempt.** Given total \(\Psi=\sum_i \psi_i\),
\[
p_{\mathrm{acc}}(\Psi)\;=\; \Pr\left(H(u)\ge \Theta-\Psi\right)\;=\;
\begin{cases}
e^{-(\Theta-\Psi)}, & \Psi < \Theta,\\[2pt]
1, & \Psi \ge \Theta.
\end{cases}
\]
Thus, **each additional nat** of \(\Psi\) multiplies per-attempt success by \(e^{+1}\approx 2.718\), subject to caps.

---

## 2) Cost Model (Abstract)

For each type \(i\), let \(K_i(\Delta\psi_i)\) be the **marginal cost** to obtain \(\Delta\psi_i\) nats (before caps). In practice:

- **Hash**: cost scaled by electricity \$/J, hardware amortization, and micro-target scan rate.
- **AI**: cost per **ai\_unit** (TEE-attested inference + redundancy + traps + QoS), mapped to \(\psi_{\text{ai}}\) via policy weights.
- **Quantum**: cost per **quantum\_unit** (trap families + provider attestations).
- **Storage**: cost per **redundant byte*time** with PoSt/QoS.
- **VDF**: time/area trade-offs; capped to remain bonus-like.

A linearized bound suffices for **security planning**:
\[
\textbf{Cost}(\Delta\Psi)\;\ge\;\sum_i c_i^{(\$)} \cdot \Delta\psi_i,\quad \sum_i \Delta\psi_i=\Delta\Psi,
\]
with \(c_i^{(\$)}\) = \$ per nat for type \(i\), piecewise-constant until caps hit.

**Equivalence to hashpower.** Because \(p_{\mathrm{acc}} \propto e^{\Psi}\), a \(\Delta\Psi=\ln x\) acts like an **x-fold** success-rate multiplier for a fixed attempt rate. The **minimum \$ to double** effective success rate is
\[
\mathrm{Cost}_{\times 2}\;\ge\;\min_{\sum \Delta\psi_i=\ln 2}\;\sum_i c_i^{(\$)}\,\Delta\psi_i.
\]
Caps enforce \(\Delta\Psi\le \Gamma\) per candidate.

---

## 3) System-Level Advantage & Share

Let miner or coalition \(j\) have raw attempt intensity \(R_j\) (e.g., hash scans per micro-target). Their **effective** block-arrival rate scales like:
\[
\Lambda_j \;\propto\; R_j \cdot \mathbb{E}\left[e^{\Psi_j}\right],
\]
with \(\Psi_j\) constrained by per-candidate caps and escort rules.

Define the **effective work share** of an attacker \(\beta\):
\[
\beta\;=\;\frac{\sum_{j\in \text{att}} \Lambda_j}{\sum_{k\in \text{all}} \Lambda_k}.
\]
This \(\beta\) plugs into reorg/double-spend analyses below.

---

## 4) Reorg & Finality Bounds

For a longest-(effective-)work chain with geometric inter-block times, standard Nakamoto-style tail bounds apply after adjusting for \(\beta\).

- Probability attacker with share \(\beta<\tfrac12\) **catches up** by \(z\) blocks (conservative bound):
\[
P_{\text{catch}}(z) \;\lesssim\; \left(\frac{\beta}{1-\beta}\right)^{z}.
\]
- With **retarget windows**, choose \(z\) within an epoch for static \(\Theta\). Across epochs, use worst-case \(\beta\) envelope.
- **Recommendation (ops):** publish chain-specific \(z\) → target residual risk (e.g., \(10^{-6}\)) under a budgeted \(\beta_{\max}\) (see §7).

---

## 5) Attack Classes & Mitigations

1) **Monoculture spending (boost one type).**  
   *Bounded by* \(C_i\) and \(\Gamma\); *blunted by* escort \(q\) to require multi-type presence.

2) **Nullifier reuse / replay.**  
   Each proof has a deterministic **nullifier** with TTL window; reuse ⇒ rejection. See `proofs/nullifiers.py`.

3) **TEE/QPU attestation forgery.**  
   Vendor roots + transparency logs; **trap receipts** force ongoing correctness. Slashing/AICF penalties raise \(c_i^{(\$)}\).

4) **Selective withholding / selfish assembly.**  
   Diversity rules and policy roots in headers constrain rearrangements; **per-type caps** prevent extreme stacking.

5) **DoS via oversized submissions.**  
   Enforced by \(\psi\) per-proof caps and **pre-admission size checks** (mempool/consensus validators).

6) **ASIC/FPGA hash skew.**  
   Expected; bounded by multi-work channels making “pure hash” insufficient to dominate when caps are balanced.

7) **VDF time-machine assumptions.**  
   Parameters set so verification remains cheap; contribution capped to prevent outsized advantage.

---

## 6) Closed-Form Speedup & Cost

If an attacker can fill all caps each candidate, their **per-candidate multiplier**
\[
M_{\max}\;=\;e^{\Gamma}.
\]
Example: \(\Gamma=2.0\) ⇒ \(M_{\max}\approx 7.39\times\).

If honest miners typically achieve \(\bar{\Psi}_{\text{H}}\) (due to organic AI/Storage work) while attacker can reach \(\Psi_{\text{A}}\), the **relative intensity factor**
\[
\frac{\Lambda_{\text{A}}}{\Lambda_{\text{H}}} \;\approx\; \frac{R_{\text{A}}}{R_{\text{H}}} \cdot
\frac{e^{\Psi_{\text{A}}}}{e^{\bar{\Psi}_{\text{H}}}}.
\]
Thus the **incremental spend** required to lift \(\Psi_{\text{A}}-\bar{\Psi}_{\text{H}}=\Delta\Psi\) by \(\ln 2\) per doubling is at least \(\min\sum c_i^{(\$)}\Delta\psi_i\) (subject to escort and caps).

---

## 7) Parameterization Guidance (Back-of-Envelope)

- Choose caps so **plurality** of advantage requires **≥2 types**:
  - Example: \(\Gamma=2.0\) with \(C_{\text{ai}}=0.8\), \(C_{\text{quant}}=0.8\), \(C_{\text{hash}}=0.4\), \(C_{\text{storage}}=0.4\), \(C_{\text{vdf}}=0.2\), escort \(q=2\).
- Publish **target costs** to double effective rate:
  - If \(c_{\text{ai}}^{(\$)} \approx \$X/\text{nat}\) and \(c_{\text{quant}}^{(\$)} \approx \$Y/\text{nat}\), then \(\mathrm{Cost}_{\times 2}\ge \min(\$X,\$Y)\cdot \ln 2\) when single-type suffices; otherwise with escort \(q=2\), the lower bound is \((\$X+\$Y)\cdot (\ln 2)/2\).
- Maintain **Θ retarget** smoothing such that short spikes in \(\Psi\) do not overreact; see `consensus/difficulty.py`.
- Set **nullifier TTL** ≥ expected block assembly latency + network jitter; ensure reuse detection crosses mempool/reorg paths.

---

## 8) Worked Example (Illustrative)

Suppose:
- \(\Theta=20\) nats; \(\Gamma=2.0\) (global cap).
- Honest median \(\bar{\Psi}_{\text{H}}=1.2\) (light AI+storage presence).
- Attacker can reliably hit \(\Psi_{\text{A}}=2.0\) (full cap), so \(\Delta\Psi=0.8\).
- Relative acceptance factor per attempt: \(e^{0.8}\approx 2.23\times\).
- If attacker raw attempt rate \(R_{\text{A}}=0.25 R_{\text{H}}\), then
  \[
  \frac{\Lambda_{\text{A}}}{\Lambda_{\text{H}}}\approx 0.25\times 2.23 \approx 0.56 \Rightarrow \beta \approx \frac{0.56}{1+0.56}\approx 0.36.
  \]
- With \(\beta\approx 0.36\), the **catch-up probability** after \(z=12\) confs is \(\lesssim (0.36/0.64)^{12}\approx 0.0028\) (conservative). Increasing \(z\) to 20 drives this below \(10^{-4}\).

**Cost to sustain cap:** If escort requires both AI and Storage present and \(c_{\text{ai}}^{(\$)}\approx \$120/\text{nat}\), \(c_{\text{storage}}^{(\$)}\approx \$30/\text{nat}\), then achieving \(\Delta\Psi=0.8\) split evenly costs \(\approx 0.4\cdot(120+30)=\$60\) **per candidate** (not per block found). Effective \$ per found block scales by \(1/p_{\mathrm{acc}}\) at that \(\Psi\).

---

## 9) Monitoring & On-Chain Telemetry

Expose (and alert on):
- Distribution of realized \(\Psi\) by type, per block.
- Fraction of blocks meeting escort via **≥q** types.
- Reorg depth histogram; stale-rate vs. retarget windows.
- AICF/TEE attestation failure rates; **trap miss** ratios.

**Automated policy guardrails** (optional):
- If single-type dominance persists above \(X\%\) of time, decrease its \(C_i\) by \(\delta\) next epoch.
- If average \(\bar{\Psi}\) drifts high (demand surge), raise \(\Theta\) smoothing or widen caps for underrepresented types.

---

## 10) Limitations & Caveats

- Models are **upper/lower bounds**; real costs \(c_i^{(\$)}\) evolve with markets (hardware, energy, cloud capacity).
- Attestations: while vendor roots reduce forgery, assume **non-zero** bypass probability → price into \(c_i^{(\$)}\) via slashing and audits.
- Coordinated cartels could share costs across types; escort still forces *breadth* of spend.

---

## 11) Test Plan & Simulations

- Synthetic streams (see `consensus/tests/test_scorer_accept_reject.py`) sweeping \(\psi\) vectors at fixed \(\Theta\).
- Epoch sims with alternating **high-Ψ** and **low-Ψ** bouts; verify EMA retarget remains stable (`consensus/tests/test_difficulty_retarget.py`).
- Chaos tests: remove one type for N blocks; confirm escort and caps maintain liveness & fairness bounds.

---

## 12) Summary

- Each nat of \(\Psi\) multiplies per-attempt success by \(e\); caps and escort rules bound attainable \(\Delta\Psi\).
- Translate \(\Delta\Psi\) into **equivalent hashpower multipliers** to reason about reorg probabilities via \(\beta\).
- Publish parameters and target **\$-per-doubling** to make economic security legible and adjustable over time.

*Version: v1.0 — aligns with `spec/poies_policy.yaml` semantics and the acceptance equation in `docs/spec/poies/ACCEPTANCE_S.md`.*

