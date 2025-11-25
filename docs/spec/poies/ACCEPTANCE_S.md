# Acceptance Predicate \(S = -\ln u + \sum \psi \ge \Theta\)

This note derives the PoIES acceptance rule used by consensus and mining:

\[
S \;\stackrel{\mathrm{def}}{=}\; H(u) + \sum_{p\in\mathcal{P}} \psi(p) \;\;=\;\; \underbrace{-\ln u}_{\text{hash-draw}} \;+\; \underbrace{\sum \psi}_{\text{useful‐work score}} \;\;\ge\;\; \Theta,
\]

where \(u\sim \mathrm{Uniform}(0,1)\) is the per-attempt *u-draw* derived from the header template and nonce (see `mining/nonce_domain.py`), \(\psi(p)\ge 0\) are capped contributions from attached proofs, and \(\Theta\) is the current difficulty threshold.

---

## 1) Hash Draw \(\Rightarrow\) Exponential Score

Let \(u \leftarrow \mathrm{Uniform}(0,1)\). Define

\[
H(u) := -\ln u.
\]

Then \(H(u)\sim \mathrm{Exp}(\lambda{=}1)\) (unit-rate exponential):
- \(\Pr[H(u)\ge x] = e^{-x}\) for \(x\ge 0\).
- Memoryless: \(\Pr[H(u)\ge x+y\mid H(u)\ge x]=e^{-y}\).

This is the classic continuous formulation of PoW share difficulty: comparing \(-\ln u\) to a threshold is equivalent to comparing a hash to a target.

---

## 2) Adding Useful Work \(\sum \psi\)

For each attached proof \(p\) (AI, Quantum, Storage, VDF), a verifier emits a **nonnegative** score \(\psi(p)\) after policy caps:

\[
\psi(p) = \min\big(\psi_{\text{raw}}(p),\, C_{\text{proof}}[\mathrm{type}(p)]\big).
\]

Aggregate (after diversity/escort rules; see `SCORING.md` and `consensus/caps.py`):

\[
\sum \psi \;=\; \min\!\left(\sum_t \min\!\left(\sum_{p\in t}\tilde{\psi}(p),\, C_{\text{type}}[t]\right),\, \Gamma\right).
\]

Thus \(\sum\psi\) is **bounded** and **deterministic** from the candidate’s proofs.

---

## 3) Acceptance Inequality

A candidate is **accepted** iff

\[
H(u) + \sum \psi \;\ge\; \Theta
\quad\Longleftrightarrow\quad
H(u) \;\ge\; \Theta - \sum\psi.
\]

Because \(H(u)\ge 0\), only the nonnegative part of \(\Theta - \sum\psi\) matters. Writing \(x := \max(0,\,\Theta-\sum\psi)\):

\[
\Pr[\text{accept} \mid \sum\psi] \;=\; \Pr[H(u)\ge x]
\;=\; e^{-x}
\;=\; \min\!\big(1,\, e^{-(\Theta-\sum\psi)}\big).
\]

Equivalently, in “hash target” form:

\[
u \;\le\; e^{-(\Theta-\sum\psi)} \;=\;: T_{\text{eff}}.
\]

So useful work **raises the effective target** \(T_{\text{eff}}\) by reducing the required hash score.

---

## 4) Relation to Classic PoW

With no useful work (\(\sum\psi=0\)):

\[
\Pr[\text{accept}] = e^{-\Theta}.
\]

In PoIES, the **effective difficulty** is

\[
\Theta_{\text{eff}} \;=\; \Theta - \sum\psi,
\]

and all standard PoW timing arguments carry through with \(\Theta\) replaced by \(\Theta_{\text{eff}}\). For a miner attempting \(r\) u-draws/sec, the per-second success rate is

\[
\lambda \;=\; r \cdot \mathbb{E}\!\left[e^{-\max(0,\Theta-\sum\psi)}\right],
\]

and the inter-block time remains exponential with parameter \(\lambda\) under the usual independence assumptions.

**Design note.** Policies pick caps \((\Gamma, C_{\text{type}}, C_{\text{proof}})\) and retarget \(\Theta\) so that \(\Theta\) comfortably exceeds any feasible \(\sum\psi\), avoiding the trivial regime where acceptance saturates at 1.

---

## 5) Retargeting \(\Theta\)

Difficulty retargeting chooses \(\Theta\) so the observed block interval tracks a target \(\Delta^*\). One convenient form (see `docs/spec/DIFFICULTY_RETARGET.md`) is an EMA on the *effective* success probability:

\[
\Theta_{k+1} \;=\; \mathrm{clamp}\!\Big(\Theta_k \;+\; \eta\cdot \ln\frac{\Delta_k}{\Delta^*},\; \Theta_{\min},\Theta_{\max}\Big),
\]

where \(\Delta_k\) is the observed inter-block time. Because useful work changes \(\Theta_{\text{eff}}\), the EMA implicitly accounts for \(\sum\psi\) through realized acceptance.

---

## 6) Binding & Unbiasability

- **u-draw binding.** \(u = \mathrm{U}(0,1)\) is derived by hashing the **header template + nonce + mixSeed** in a **domain-separated** manner (see `spec/header_format.cddl`, `mining/nonce_domain.py`). This prevents adaptive bias via payload edits.
- **No double counting.** Proof *nullifiers* and policy caps ensure proofs cannot be replayed or over-credited across candidates.
- **Determinism.** \(H(u)\) and \(\sum\psi\) are computed with fixed-point arithmetic (μ-nats) in consensus code (`consensus/math.py`), avoiding FP nondeterminism.

---

## 7) Worked Example

Let \(\Theta = 32\ \mu\text{-nats}\) and the candidate’s capped/diversity-adjusted sum be \(\sum\psi = 27.4\ \mu\text{-nats}\) (cf. `SCORING.md`):

\[
H(u) \ge \Theta-\sum\psi = 4.6 \;\;\Rightarrow\;\; \Pr[\text{accept}] = e^{-4.6} \approx 1.0\%.
\]

A different candidate with stronger proofs (but within \(\Gamma\)) might reach \(\sum\psi=30\), raising acceptance to \(e^{-2}\approx 13.5\%\).

---

## 8) Lemmas (Sketch)

1. **Monotonicity.** If \(\sum\psi_1 \le \sum\psi_2\) then
   \(\Pr[\text{accept}\mid \sum\psi_1] \le \Pr[\text{accept}\mid \sum\psi_2]\).  
   *Proof:* direct from \(e^{-(\Theta-\sum\psi)}\).

2. **Upper bound by \(\Gamma\).** With total cap \(\Gamma\),
   \(\Pr[\text{accept}] \le e^{-\max(0,\Theta-\Gamma)}\).  
   *Proof:* substitute \(\sum\psi \le \Gamma\).

3. **Equivalence to target test.** The predicate \(H(u)+\sum\psi\ge \Theta\) is equivalent to
   \(u \le e^{-(\Theta-\sum\psi)}\).  
   *Proof:* rearrange, apply monotonicity of \(\exp\).

---

## 9) Implementation Pointers

- Scoring & acceptance: `consensus/scorer.py`, `consensus/validator.py`
- Caps & escort: `consensus/caps.py`, `spec/poies_policy.yaml`
- u-draw domain: `mining/nonce_domain.py`
- Fixed-point math: `consensus/math.py`
- Tests: `consensus/tests/test_scorer_accept_reject.py`, `test_difficulty_retarget.py`, `mining/tests/test_nonce_domain.py`

---

## 10) Operational Guidance

- Choose \(\Theta_{\min} > \Gamma + \text{margin}\) to avoid saturation.
- Keep \(\Gamma\) small enough that hash randomness remains decisive over short windows, preventing centralized proof suppliers from deterministically sealing blocks.
- Validate with simulation benches before policy changes.

