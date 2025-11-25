# Fairness Analysis: Lottery vs Pool Dynamics under PoIES

**Scope.** This paper studies miner fairness and concentration dynamics for **Proof-of-Integrated-External-Work (PoIES)**. We compare *solo* (pure lottery) and *pool* mining, quantify variance reduction from shares, analyze incentives created by external proof credits \( \psi \), and give policy levers (caps \( \Gamma \), escort/diversity bonuses \( B(\cdot) \), retarget and rate limits) that preserve decentralization.

It builds on the scoring model \( S = H(u) + A \) with acceptance if \( S \ge \Theta \), where \( H(u)=-\ln u \sim \mathrm{Exp}(1) \) and \( A = \sum_{p\in\mathcal{P}} \psi(p) + B(K) \) (post-verification, post-cap). Throughout, \(\Theta\) is adjusted by difficulty retarget.

---

## 1. Success Rates and Shares

Let miner \(i\) attempt nonces at rate \( \rho_i \) (draws/sec). With a fixed per-candidate external contribution \( A_i \) (bounded by caps), the per-attempt success probability is
\[
p_i = \min\{1, e^{A_i-\Theta}\}.
\]
Thus accepted blocks form a Poisson process with rate
\[
\lambda_i = \rho_i \, e^{A_i-\Theta} \quad (A_i<\Theta).
\]
Total network rate \( \Lambda=\sum_j \lambda_j \). Miner \(i\)'s steady-state block share:
\[
\pi_i \;=\; \frac{\lambda_i}{\Lambda} \;=\; \frac{\rho_i e^{A_i}}{\sum_j \rho_j e^{A_j}}.
\]
**Implication.** External work amplifies effective hashrate *multiplicatively* via \(e^{A_i}\), but caps and diversity design keep amplification ratios bounded (Sec. 4).

---

## 2. Solo vs Pool: Variance and Time-to-Luck

Consider a horizon \(T\). Solo miner \(i\) produces \(N_i \sim \mathrm{Poisson}(\lambda_i T)\) blocks. Revenue \(R_i = R \, N_i\) where \(R\) is block reward (incl. fees).

- **Solo expectations:** \( \mathbb{E}[R_i]=R\lambda_i T\), \(\mathrm{Var}[R_i] = R^2 \lambda_i T\).  
- **CV (coefficient of variation):** \( \mathrm{CV} = 1/\sqrt{\lambda_i T} \). Small actors (\(\lambda_i\) tiny) face high variance.

### Share-based estimation (for pools & dashboards)

Define a **share target** \(\theta_{\mathrm{sh}}<\Theta\). A *share* is a hit when \(H(u)+A \ge \theta_{\mathrm{sh}}\). Per attempt success \(p_{\mathrm{sh}}=e^{A-\theta_{\mathrm{sh}}}\) (for \(A<\theta_{\mathrm{sh}}\) region this expression continues smoothly; it saturates at 1 otherwise). The share rate for miner \(i\) is
\[
\mu_i = \rho_i \, e^{A_i-\theta_{\mathrm{sh}}}.
\]
Shares are high-frequency: variance of share counts over \(T\) is \(\mu_i T\), enabling **low-noise** hashrate/acceptance estimation and **pool payouts** with low variance.

---

## 3. Pools: Payout Schemes and ψ-Aware Splits

Let a pool aggregate workers \(i\). For each share, the worker submits: (i) the u-draw evidence and (ii) any external proof contributions \( \psi \) they attached (subject to nullifier rules and header binding).

**Key accounting principle.** A block’s acceptance probability gain from external work is multiplicative. For small increments \(\Delta A\) near \(A\), the *marginal* acceptance lift is
\[
\Delta \Pr \approx e^{A-\Theta}\,\Delta A \quad (\text{first order}).
\]
Hence **ψ-credit** ought to pay proportionally to the *marginal lift* a worker’s verified proofs contributed to the winning candidate.

We propose a payout decomposition per found block:
\[
R_{\text{block}} = R_{\text{hash}} + R_{\psi},
\]
where
- \(R_{\text{hash}}\) distributes by conventional share weight (PPLNS/FPPS against \(\mu_i\)),
- \(R_{\psi}\) distributes by the *normalized ψ contribution* used in the winning block, e.g.
\[
w^{(\psi)}_i \;=\; \frac{\sum_{p\in \mathcal{P}_i} \psi(p)}{\sum_{k}\sum_{p\in \mathcal{P}_k}\psi(p)}.
\]
If the winning block has little or no ψ (e.g., \(A\approx 0\)), then \(R_{\psi}\) is near zero, reverting to hash-only split.

**Schemes.**
- **PPS (Pay-Per-Share).** Each share earns a fixed \( \alpha R \), where \( \alpha = e^{-(\Theta-\theta_{\mathrm{sh}})} / \mathbb{E}[\text{shares per block}] \). Lowest variance but requires pool capital & risk. Extend to **FPPS** to also include expected fees. ψ-aware PPS adds a small per-share ψ bonus proportional to validated \(\psi\) attached in that share.
- **PPLNS.** Distribute each block’s \(R\) across the last \(N\) shares (or time window) including winning share; ψ-aware variant reserves \(R_{\psi}\) for contributors of the block’s attached proofs, with anti-gaming nullifier checks.
- **Score-based.** Weight shares by decaying score to deter hopping; ψ-component paid only when the pool actually consumes a proof in a block (prevents recycling ψ across many shares).

**Anti-abuse.** Nullifiers (TTL + header binding) prevent a single ψ from being claimed in many shares. Pool software should record *ψ-claims ledger* keyed by nullifier and block template id.

---

## 4. Concentration: HHI/Gini Under Caps and Diversity

Define effective amplification \( \alpha_i = e^{A_i} \) (bounded by global cap \(\Gamma\) and per-type caps). Market share
\[
\pi_i = \frac{\rho_i \alpha_i}{\sum_j \rho_j \alpha_j}.
\]
**HHI (Herfindahl–Hirschman Index):**
\[
\mathrm{HHI} = \sum_i \pi_i^2.
\]
Differentiating w.r.t. \(A_k\),
\[
\frac{\partial \pi_k}{\partial A_k} = \pi_k(1-\pi_k),
\qquad
\frac{\partial \mathrm{HHI}}{\partial A_k} = 2\pi_k \frac{\partial \pi_k}{\partial A_k} - 2\sum_{i\ne k}\pi_i \frac{\partial \pi_i}{\partial A_k}
= 2\pi_k(1-2\pi_k)\pi_k.
\]
Thus increasing \(A_k\) for a dominant miner (\(\pi_k>1/2\)) **raises** HHI sharply, while for small miners (\(\pi_k\ll 1/2\)) the effect on HHI is mild.

**Policy levers:**
- **Global cap \(\Gamma\)** with \(\Gamma<\Theta\) keeps amplification bounded \( \alpha_i \le e^{\Gamma} \).
- **Per-type caps \(\Gamma_T\)** prevent specialization runaway.
- **Submodular diversity bonus \(B(K)\)** (diminishing returns) favors breadth over depth, helping smaller or more generalized participants close the gap (marginal gain is largest at small \(A\); see ΔPr in Sec. 3).

---

## 5. Pool Hopping and Window Design

In score-based pools, define per-share score \(s_t=\beta^{T_{\text{now}}-t}\) with \(\beta\in(0,1)\), or use PPLNS with a fixed **window measured in expected blocks** (e.g., last \(N= \kappa / p_{\mathrm{sh}}\) shares). Then the expected value of a share is **time-stationary**, eliminating incentives to hop.

**Guideline.**
- Use a **share target** \(\theta_{\mathrm{sh}}\) that yields \(p_{\mathrm{sh}}\in[10^{-3},10^{-2}]\) per attempt for typical workers so variance is low and stale risk is manageable.
- Measure the window in *expected blocks* (e.g., last 2–4 blocks worth of shares), not wall-time.

---

## 6. Stales, Propagation, and Fairness

Let network propagation lag be \(d\). Effective stale probability roughly scales with \(d \Lambda\). Pools with better connectivity reduce stales and thus skew revenue. To counteract centralization pressure:
- **P2P improvements** (header-first relay, compact blocks).
- **Penalty-free orphan credit** for shares submitted from a losing branch right before reorg (pool policy), smoothing revenue for small nodes.

---

## 7. Sybil and Share-Splitting

Pools often cap *per-peer* ingress (tx/s, shares/s). A miner could Sybil to bypass limits. Mitigations:
- Aggregate limits by **identity heuristics** (connection patterns, costed proof of identity optional).
- **Per-origin token buckets** plus **global pool buckets**.
- Enforce **minimum share difficulty per peer** that scales with observed rate (prevents micro-spamming).

---

## 8. ψ Attribution and Cross-Miner Composition

Because external proofs carry **nullifiers** bound to context, a single ψ cannot be re-claimed across many candidates. Pools should:
- Attribute ψ to the **submitter** of that nullifier when it is actually used in the winning block.
- Decline ψ that fails policy checks (caps, type caps, policy roots).
- Optionally allow **bounty markets** inside the pool (workers can offer ψ to the template builder for a posted split), but ensure deterministic selection to avoid biased assembly.

---

## 9. Quantifying Variance Reduction

Let a small miner have \( \lambda \ll 1/\text{day} \). Solo CV after one week is \( \approx 1/\sqrt{\lambda \cdot 7\text{d}} \), often \(>100\%\). In a PPLNS pool with share rate \(\mu\) and window covering \(W\) expected blocks, the payout per block is spread across \(W\cdot \mathbb{E}[\text{shares per block}]\) shares, driving **per-share variance** to \(O(1/(\mu T))\) over the same horizon, typically reducing CV by an order of magnitude or more. ψ-aware payouts add little additional variance because ψ is sparse and paid only when consumed; pool can smooth \(R_{\psi}\) with a “ψ-dividend” reserve if desired.

---

## 10. Practical Fairness Recommendations

1. **Set \(\Gamma<\Theta\)** on mainnet, pick \(\Gamma_T\) to avoid single-type dominance; use **submodular** \(B(K)\).  
2. **Share target**: choose \(\theta_{\mathrm{sh}}\) s.t. \(p_{\mathrm{sh}}\approx 10^{-3}\)–\(10^{-2}\).  
3. **Pool payouts**: adopt **PPLNS (or FPPS) + ψ-aware split**; record ψ claims by nullifier; pay ψ only on consumption.  
4. **Anti-hopping**: score-based windows or PPLNS measured in expected blocks.  
5. **Stales**: improve relay; optionally credit near-tip losing-branch shares.  
6. **Rate limits**: per-peer + global buckets; adaptive minimum share difficulty; Sybil heuristics.  
7. **Transparency**: publish effective \( \alpha_i \) distribution (anonymized) and pool HHI/Gini metrics over time.  
8. **Retarget**: use **log-EMA** with share-based estimator for stability (low variance input).

---

## 11. Fairness Metrics to Monitor

- **HHI** and **Gini** over \(\pi_i\) (rolling windows).  
- **Top-k share** (e.g., top-1, top-5).  
- **Stale rate** per region/provider.  
- **ψ mix**: fraction of blocks with non-zero \(A\), average \(A\) used, per-type utilization vs caps.  
- **Pool dependence**: % of network behind top pools; aim to keep <50% for top-1.  
- **Window leakage**: correlation between entry/exit timing and realized payout (should be ~0 in anti-hopping design).

---

## Appendix A — Expected Share Value (FPPS-style)

Let expected blocks per share (network-wide) be
\[
\mathbb{E}[\text{blocks per share}] = \frac{\Lambda}{\sum_j \mu_j}
= e^{-(\Theta-\theta_{\mathrm{sh}})} \cdot \frac{\sum_j \rho_j e^{A_j-\Theta}}{\sum_j \rho_j e^{A_j-\theta_{\mathrm{sh}}}}
= e^{-(\Theta-\theta_{\mathrm{sh}})}.
\]
Hence an **unbiased** FPPS per-share base reward is
\[
r_{\text{share}} = R \cdot e^{-(\Theta-\theta_{\mathrm{sh}})},
\]
independent of miner composition (assuming fees are folded into \(R\) or a separate FPPS-fees term). ψ-aware FPPS can add a small premium \(r_{\psi,\text{share}}\) based on validated \(\psi\) attached to that share, scaled by a pool-managed reserve to keep variance low.

---

## Appendix B — ψ Diversity Advantage at the Margin

Given two miners with equal \(\rho\), one with \(A\) and one with \(A+\Delta\) (via diversity), the block-share ratio is \(e^{\Delta}\). Under submodular escort, early adoption (\(A\) small) yields larger **marginal acceptance gain** (Sec. 3), helping smaller miners catch up when they add a *new* proof type rather than stacking the same type deeper.

---

**Summary.** PoIES preserves the lottery nature of block selection while rewarding verifiable useful work. With bounded amplification (caps), submodular diversity bonuses, and ψ-aware pool accounting, the system supports low-variance payouts for small actors **without** entrenching dominance. The share-based estimator and log-EMA retarget provide stable, fair operating points observable and auditable on-chain and via pool transparency reports.
