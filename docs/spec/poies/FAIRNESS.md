# Fairness & Concentration in PoIES
*Measuring miner/provider concentration and tuning policy for healthy “lottery” dynamics.*

This note defines concrete fairness metrics (Gini & HHI), explains the stochastic “lottery” dynamics of block production under PoIES, and outlines targets and levers for policy tuning.

---

## 1) Scope & Units of Analysis

We evaluate concentration over rolling **epochs** of \(B\) consecutive blocks (e.g., \(B\in\{256, 1024\}\)).

Let \(\mathcal{M}\) be the set of **miners** (or pools). For each miner \(i\in\mathcal{M}\) in an epoch, define:

- \(b_i\): blocks produced by \(i\) in the epoch (count).
- \(w_i\): **work-share** credited to \(i\) in the epoch (can be reward-weighted: base + \(\sum\psi\) bonuses).
- \(s_i\): **market share** for metric computation. Choose either
  - **Block-share** \(s_i = b_i / \sum_j b_j\), or
  - **Reward-share** \(s_i = w_i / \sum_j w_j\).  
  Reward-share is preferred when useful-work changes payouts.

We aggregate by **pool identity** (on-chain coinbase OR recognized pool tag). Address-level fragmentation must be merged, or concentration will be understated.

---

## 2) Concentration Metrics

### 2.1 Gini Coefficient (inequality of shares)
For nonnegative shares \(s_1,\dots,s_n\) with \(\sum_i s_i = 1\):

\[
G = \frac{1}{2 n^2 \mu}\sum_{i=1}^n \sum_{j=1}^n |s_i - s_j|, \quad \mu = \frac{1}{n}\sum_i s_i = \frac{1}{n}.
\]

Since shares sum to 1, \(\mu = 1/n\) and:

\[
G = \frac{1}{2n}\sum_{i=1}^n \sum_{j=1}^n |s_i - s_j|.
\]

- Range: \(G\in[0, 1 - \tfrac{1}{n}]\) (approaches 1 as a single entity dominates).
- Interpretation: lower is fairer (more equal share distribution).

### 2.2 HHI (Herfindahl–Hirschman Index; concentration)
\[
\mathrm{HHI} = \sum_{i=1}^n (100 s_i)^2 = 10{,}000 \sum_i s_i^2.
\]

- DOJ heuristic (not binding here):  
  - **Unconcentrated**: HHI < 1500  
  - **Moderately**: 1500–2500  
  - **Highly**: > 2500

We also use a **normalized HHI** in \([0,1]\):

\[
\mathrm{HHI}_{\mathrm{norm}} = \frac{\sum_i s_i^2 - 1/n}{1 - 1/n}.
\]

- 0 = perfectly equal, 1 = monopoly.

> **Recommended:** Track both `Gini` and `HHI_norm` per epoch, plus CR\(_k\) (top-k cumulative share, e.g., CR\(_3\), CR\(_5\)).

---

## 3) Lottery Dynamics Under PoIES

Each candidate attempts a **u-draw** and attaches proofs yielding capped score \(\sum\psi\). The acceptance predicate is:

\[
S = -\ln u + \sum\psi \ge \Theta \quad \Longleftrightarrow \quad
u \le e^{-(\Theta - \sum\psi)} =: T_{\mathrm{eff}}.
\]

For miner \(i\) with attempt rate \(r_i\) and distribution of \(\sum\psi_i\), the **per-second success rate** is approximately:

\[
\lambda_i \approx r_i \cdot \mathbb{E}\big[e^{-\max(0,\,\Theta-\sum\psi_i)}\big].
\]

Blocks are a **Poisson race** with probabilities proportional to \(\lambda_i\). Over a window of \(B\) blocks, \((b_i)\) is Multinomial\((B,\,p_i)\) with \(p_i=\lambda_i/\sum_j\lambda_j\). Hence:

- **Expected share** \(\mathbb{E}[b_i]/B = p_i\).
- **Variance** \(\mathrm{Var}[b_i]/B^2 = p_i(1-p_i)/B\).

**Implications.**
- Short windows inflate apparent concentration due to variance; use multiple window sizes.
- Useful-work advantage raises \(p_i\) through \(\sum\psi\), but caps/diversity rules prevent saturation.

---

## 4) Policy Levers Affecting Fairness

- **Total cap \(\Gamma\)** and **per-type caps** \(C_{\text{type}}\): prevent extreme \(\sum\psi\) dominance.  
- **Diversity/Escort** parameter \(q\): requires mixing proof types within a candidate; avoids single-type monocultures.
- **Nullifier windows**: limit re-use of identical proof material across candidates.
- **\(\alpha\)-tuner** (fairness drift corrector): slow feedback that adjusts type-weights to nudge toward equalized **marginal** impact across proof types without changing absolute caps.
- **Network fees/reward split** (AICF/treasury): can smooth payout volatility.

**Targets (illustrative):**
- \(\mathrm{HHI}_{\mathrm{norm}} \le 0.25\) over \(B=1024\) blocks,
- \(G \le 0.35\),
- CR\(_3\) \(\le 0.6\), CR\(_5\) \(\le 0.75\).  
Tune per network phase; verify by simulation before mainnet changes.

---

## 5) Measurement Protocol

1. **Identity resolution.** Map coinbase → pool. Merge addresses known to belong to the same pool.
2. **Choose share basis.** Prefer reward-share \(w_i\) in PoIES to reflect useful-work bonuses.
3. **Windowing.** Compute metrics over overlapping windows (e.g., step 64 over \(B=256, 1024, 4096\)).
4. **Confidence.** Report Wilson intervals for CR\(_k\) and bootstrap CIs for Gini/HHI.
5. **Dashboards.** Publish time series of Gini, HHI\(_\mathrm{norm}\), CR\(_k\), plus **Lorenz curves** snapshot per day.

---

## 6) Lorenz Curve & Visuals

Sort shares \(s_{(1)}\le\dots\le s_{(n)}\). The Lorenz curve plots cumulative fraction of entities vs cumulative share. The **area** between Lorenz and diagonal gives **Gini**:

\[
G = 1 - 2\int_0^1 L(p)\,dp.
\]

---

## 7) Simulation & Backtesting

- **Synthetic providers.** Sample \(\sum\psi\) distributions per provider type; calibrate to policy caps.
- **Attempt rates.** Set \(r_i\) by hardware/energy budgets; include variance.
- **Outputs.** Compare observed \(p_i\) vs expected; sweep \(\Gamma, C_{\text{type}}, q\).
- **Goal.** Minimize concentration subject to target TPS and DA limits.

---

## 8) Practical Thresholds & Alerts

- **Amber alert** if \(\mathrm{HHI}_{\mathrm{norm}}>0.35\) or CR\(_3\)>0.7 for \(\ge 4\) consecutive epochs at \(B=1024\).
- **Red alert** if any single pool exceeds 33% for \(\ge 2\) epochs.  
  Trigger governance review: tighten caps, increase escort \(q\), or adjust \(\alpha\)-tuner bounds.

---

## 9) Example (Pseudo-Code)

```python
# inputs: list of (miner_id, reward_share) for one epoch, shares sum to 1
shares = [s for _, s in epoch_shares]
n = len(shares)

# Gini
shares_sorted = sorted(shares)
cum = 0.0
lorenz_area = 0.0
for i, s in enumerate(shares_sorted, start=1):
    cum += s
    lorenz_area += cum  # Riemann sum
lorenz_area /= n
G = 1 - 2 * (lorenz_area - 0.5 / n)

# HHI (normalized)
hhi = sum(s*s for s in shares)
hhi_norm = (hhi - 1/n) / (1 - 1/n)

# CR3/CR5
cr3 = sum(sorted(shares, reverse=True)[:3])
cr5 = sum(sorted(shares, reverse=True)[:5])


⸻

10) Notes on Pools & External Suppliers
	•	External proof suppliers (AI/Quantum) must not turn block acceptance into a deterministic queue.
Caps and escort ensure hash lottery remains decisive at sub-epoch horizons.
	•	Pool openness. Favor policies that allow small miners to contribute useful-work via open markets without exclusive deals.

⸻

11) References & Pointers
	•	Acceptance predicate and scoring: docs/spec/poies/ACCEPTANCE_S.md, SCORING.md
	•	Caps/diversity: consensus/caps.py, spec/poies_policy.yaml
	•	α-tuner: consensus/alpha_tuner.py
	•	Bench & sims: consensus/tests/test_fork_choice.py, .../test_scorer_accept_reject.py

