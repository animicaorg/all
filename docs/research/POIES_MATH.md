# PoIES Math: Rigorous Derivations, Inequalities, and Proof Sketches

**Scope.** This note formalizes the probabilistic model and acceptance criterion used by **Proof-of-Integrated-External-Work (PoIES)**, derives key distributional facts, proves monotonicity and stability properties, and gives bounds that underpin policy choices (caps Γ, escort/diversity bonuses, and difficulty retarget). It complements `spec/poies_math.md` with rigorous statements.

---

## 1. Model and Notation

- Each block candidate carries a **uniform random draw** \(u \sim \mathrm{Uniform}(0,1]\) bound to the header nonce/mix-seed domain.  
- Define the exponential transform
\[
H(u) \;=\; -\ln u \;\sim\; \mathrm{Exp}(1),
\]
with density \(f_{H}(x)=\mathbf{1}_{x\ge 0}\, e^{-x}\), CDF \(F_H(x)=1-e^{-x}\), MGF \(M_H(t)=\mathbb{E}[e^{tH}]=\tfrac{1}{1-t}\) for \(t<1\).

- A candidate also carries a **finite multiset of external proofs** \( \mathcal{P}=\{p_1,\ldots,p_m\}\) from types \(T\in\{\text{HashShare},\text{AI},\text{Quantum},\text{Storage},\text{VDF}\}\).  
  After verification and policy clipping we obtain **nonnegative contributions**
\[
\psi(p_i) \;\ge\; 0,
\qquad
A \;\equiv\; \sum_{p\in\mathcal{P}} \psi(p).
\]
- Acceptance threshold \( \Theta>0\) is controlled by the difficulty retarget (Sec. 5).

- **Acceptance predicate (PoIES):**
\[
S \;=\; H(u) + A \;\;\ge\;\; \Theta.
\]

- **Caps.** A per-type cap \(\Gamma_T\ge 0\), optional escort/diversity bonus \(B(\cdot)\ge 0\), and a **global cap** \(\Gamma\ge 0\) are enforced so that
\[
0\;\le\; A \;\le\; \Gamma,
\qquad
\Gamma \;\le\; \sum_T \Gamma_T \;\;(\text{always satisfied if }\Gamma=\min\{\sum_T\Gamma_T,\;\Gamma_{\max}\}).
\]

---

## 2. Acceptance Probability and Basic Derivatives

Let \(A\) denote the (fixed) total external contribution on a candidate. Conditioned on \(A\), the acceptance probability is
\[
\Pr[S\ge \Theta \mid A]
= \Pr[H(u)\ge \Theta-A]
= 
\begin{cases}
1, & A\ge \Theta,\\[2mm]
e^{-(\Theta-A)} \;=\; e^{A-\Theta}, & A<\Theta.
\end{cases}
\tag{2.1}
\]

### Lemma 2.1 (Monotonicity and smoothness).
For \(A<\Theta\), the map \(A\mapsto \Pr[S\ge\Theta\mid A]\) is smooth, strictly increasing, with
\[
\frac{\partial}{\partial A}\Pr = e^{A-\Theta} = \Pr,\qquad
\frac{\partial^2}{\partial A^2}\Pr = e^{A-\Theta} = \Pr.
\]
For \(A\ge\Theta\) it saturates at \(1\).

*Proof.* Direct differentiation of \(e^{A-\Theta}\) for the sub-threshold region, and saturation by definition. \(\square\)

**Bound.** With a global cap \(A\le \Gamma\),
\[
\Pr[S\ge\Theta \mid A] \;\le\; \min\{1,\, e^{\Gamma-\Theta}\}.
\tag{2.2}
\]
This ensures external proofs alone cannot guarantee block acceptance if \(\Gamma<\Theta\).

---

## 3. Time-to-Block, Effective Rate, and Shares

Consider attempts arriving as a Poisson process with base intensity \(\rho\) (nonce trials per time unit that produce i.i.d. \(u\)). If the miner pre-assembles a fixed \(A\) on its candidate, each attempt succeeds with probability \(p(A)=\min\{1,e^{A-\Theta}\}\). Then successful blocks form a Poisson process with rate
\[
\lambda(A) \;=\; \rho \, e^{A-\Theta} \quad \text{for } A<\Theta,
\qquad
\lambda(A)=\rho \quad \text{if }A\ge\Theta\text{ but capped by actual building rate}.
\tag{3.1}
\]
Hence **external work exponentially rescales the effective success rate** by \(e^A\) under the cap regime.

### 3.1 Micro-target shares

Let a **share target** \(\theta_{\mathrm{sh}}<\Theta\) define accepted *shares* via \(H(u)+A \ge \theta_{\mathrm{sh}}\). Count shares \(N\) over window \(W\). Then \(\hat{\lambda}_{\mathrm{sh}}=N/W\) is an unbiased estimator of \(\lambda(A;\theta_{\mathrm{sh}})=\rho\,e^{A-\theta_{\mathrm{sh}}}\). A consistent estimator for the **block-rate** \(\lambda(A;\Theta)\) is
\[
\widehat{\lambda}_{\mathrm{blk}} \;=\; \hat{\lambda}_{\mathrm{sh}} \, e^{-(\Theta-\theta_{\mathrm{sh}})}.
\tag{3.2}
\]
Variance is \(\mathrm{Var}[\hat{\lambda}_{\mathrm{blk}}] = \widehat{\lambda}_{\mathrm{blk}}/(W)\) (Poisson), guiding retarget smoothing (Sec. 5).

---

## 4. Diversity/Escort Bonuses and Sub/Super-modularity

Let \(K\subseteq \mathcal{T}\) be the subset of proof types present. Define
\[
A \;=\; \sum_{p\in\mathcal{P}} \psi(p) \;+\; B(K),
\qquad 0\le B(K)\le \Gamma_B,
\]
with a **diversity bonus** \(B(\cdot)\) that is **isotone** (if \(K\subseteq L\Rightarrow B(K)\le B(L)\)) and **submodular** (diminishing returns):
\[
B(K\cup \{t\})-B(K) \;\ge\; B(L\cup\{t\})-B(L)
\quad\text{whenever }K\subseteq L,~ t\notin L.
\tag{4.1}
\]
Submodularity ensures no runaway returns from type-stacking while encouraging multi-proof mixes.

### Lemma 4.1 (Escort-bounded acceptance).
With \(A\le \Gamma_{\text{base}}+\Gamma_B\) and \(\Gamma=\Gamma_{\text{base}}+\Gamma_B\), acceptance remains bounded by (2.2). Optimal \(K\) selection under a budget prefers **breadth first** (by (4.1)) until the marginal gain equals the tightest per-type cap shadow price.

*Proof.* Combine monotonicity of \(e^{A-\Theta}\) with submodular marginal increments and global cap. \(\square\)

**Marginal acceptance gain** from adding a small \(\Delta A\) when \(A<\Theta\) is
\[
\Delta \Pr \;=\; e^{A-\Theta}(e^{\Delta A}-1) \;\approx\; e^{A-\Theta}\,\Delta A
\quad(\text{first order}).
\tag{4.2}
\]
Hence diversification that yields the same \(\Delta A\) earlier (at smaller \(A\)) has *higher* marginal effectiveness.

---

## 5. Difficulty Retarget: Log-EMA Stability

Let the target block-rate be \(\lambda^\star=1/\tau^\star\). Using share-based estimation (3.2), define
\[
\widehat{r}_t \;=\; \log \widehat{\lambda}_{\mathrm{blk},t} - \log \lambda^\star.
\]
Update rule (log-domain EMA):
\[
\Theta_{t+1} \;=\; \Theta_t + \kappa\, \widehat{r}_t,
\qquad 0<\kappa<2.
\tag{5.1}
\]
(Using log-rate ensures symmetry and robustness to multiplicative noise.)

### Theorem 5.1 (Local exponential stability).
Assume the miner population faces a slowly varying \(A_t\) with \(|A_{t+1}-A_t|\le \delta\) and unbiased \(\widehat{r}_t\) with bounded variance. Linearizing around equilibrium where \(\mathbb{E}[\widehat{r}_t]=0\), the error \(e_t=\Theta_t-\Theta^\star\) follows
\[
\mathbb{E}[e_{t+1} \mid e_t] \approx (1-\kappa)\, e_t \;+\; \kappa \, \epsilon_t,
\]
with \(\epsilon_t\) zero-mean estimation noise. For \(0<\kappa<2\) the deterministic part is stable; the stationary variance scales as \(O\!\left(\frac{\kappa^2}{1-(1-\kappa)^2}\mathrm{Var}[\widehat{r}]\right)\).

*Proof.* Standard stochastic approximation; linearization of the log-rate feedback yields an AR(1) with gain \(1-\kappa\). \(\square\)

**Remark.** Measuring \(\widehat{\lambda}\) via shares at \(\theta_{\mathrm{sh}}\ll\Theta\) reduces variance (more frequent observations), improving stability without bias by (3.2).

---

## 6. Nullifiers, Reuse, and Grinding Bounds

Each external proof \(p\) carries a **nullifier** \(n(p)\) domain-separated from its body and binding context. A proof is **admissible once** within a sliding TTL window \(W_n\). Let \(\mathcal{U}\) be the set of admissible (not-yet-consumed) proofs for a miner.

### Lemma 6.1 (No-arbitrage from reuse).
If a miner could reuse a proof arbitrarily, expected accepted blocks per unit time would scale as \(\rho \min\{1,e^{(\psi+\cdots)-\Theta}\}\) with unbounded attempts, violating policy caps. Enforcing TTL uniqueness ensures the *per-block* budget constraint \(A\le\Gamma\) is meaningful and prevents “amortizing” the same \(ψ\) across many draws.

*Proof.* Without nullifiers, \(A\) could include repeated identical \(\psi\), contradicting the clipping semantics. With TTL uniqueness, the feasible set is finite per window. \(\square\)

### Grinding on header templates
Given fixed \(A\), miners already “grind” over nonces \(u\). That is **accounted for** in \(\rho\). Adding optional **selective attachment** (attach external proofs only if \(H(u)\) is close but below \(\Theta\)) is constrained by (i) build latency, (ii) nullifier binding to the header, and (iii) TTL reuse. Model this as a stopping rule: attach when \(H(u)\in[\Theta-A,\Theta)\). The **expected attachment rate** equals \(\rho (1-e^{-A})\), which is bounded by \(A\) for small \(A\) (since \(1-e^{-A}\le A\)). Thus the **maximal “opportunistic” use** does not exceed the global \(\Gamma\) budget per block on average.

---

## 7. Upper/Lower Bounds and Security Margins

- **Acceptance upper bound** with caps: \(\Pr\le \min\{1, e^{\Gamma-\Theta}\}\) (2.2).  
- **Hash-baseline dominance:** If \(\Gamma \le \Theta - \log c\), then even at full external caps, the acceptance probability is \(\le c\) unless accompanied by hashwork; choosing \(c\ll 1\) preserves a meaningful **hash floor**.

- **Mixing gain inequality.** For two mixes \(A_1<A_2<\Theta\),
\[
\frac{\Pr_2}{\Pr_1} \;=\; e^{A_2-A_1},
\]
so a \(\Delta A\) improvement has a **multiplicative** effect independent of \(\Theta\), motivating escort bonuses that bring more types online early (Sec. 4).

---

## 8. Fairness Notes (Sketch)

Let miners \(i=1..N\) have effective attempt rates \(\rho_i\) and external contributions \(A_i\) (respecting per-type caps and global \(\Gamma\)). Their success rates are \(\lambda_i=\rho_i e^{A_i-\Theta}\). The **share of blocks** is
\[
\pi_i \;=\; \frac{\lambda_i}{\sum_j \lambda_j} \;=\; \frac{\rho_i e^{A_i}}{\sum_j \rho_j e^{A_j}}.
\]
**Escort policy** seeks to reduce concentration by shaping \(A_i\) through saturating bonuses and per-type ceilings so that the **effective amplification** \(e^{A_i}\) does not scale superlinearly with specialized hardware advantages alone. Submodular \(B(\cdot)\) (4.1) and tight \(\Gamma_T\) produce **bounded amplification ratios**:
\[
\sup_{i,j}\frac{e^{A_i}}{e^{A_j}} \;\le\; e^{\Gamma_{\max}-\Gamma_{\min}}.
\]

---

## 9. Implementation-friendly Identities

- **Log-domain scoring:** \(S\ge\Theta \iff \log u \le A-\Theta\).  
- **Quantiles:** For target acceptance probability \(p\in(0,1)\) under a fixed \(A<\Theta\),
\[
\Theta \;=\; A - \ln p.
\]
- **Small-\(A\) linearization:** For \(A\ll 1\), \(e^{A-\Theta}\approx e^{-\Theta}(1+A)\).  
- **Share calibration:** choose \(\theta_{\mathrm{sh}}=\Theta-\Delta\) to obtain a share probability \(p_{\mathrm{sh}}=e^{-\Delta}\) independent of \(A\), simplifying rate estimation when attachment policy keeps \(A\) near-constant across attempts.

---

## 10. Design Guidance Backed by the Math

1. **Pick \(\Gamma<\Theta\)** on mainnet to guarantee the **hash floor**; use diversity \(B(\cdot)\) to encourage mixes without allowing external-only blocks to deterministically pass.  
2. **Share-based retarget (log-EMA)** with \(0.2\le\kappa\le 0.6\) stabilizes under realistic noise.  
3. **Submodular escort** prevents runaway specialization; concave-in-\(|K|\) bonuses (e.g., piecewise linear until \(q\) types) satisfy (4.1).  
4. **TTL nullifiers** at least a few blocks long preclude reusing the same external proof to push many draws over \(\Theta\).  
5. **Separate per-type caps \(\Gamma_T\)** plus global \(\Gamma\) to enforce both **local** and **systemic** fairness constraints.

---

## Appendix A — Proof of (2.1)

For \(A<\Theta\),
\[
\Pr[S\ge\Theta\mid A] = \Pr[H(u)\ge \Theta-A] 
= \int_{\Theta-A}^{\infty} e^{-x}\,dx = e^{-(\Theta-A)}.
\]
For \(A\ge \Theta\), the integrand lower limit is \(\le 0\) and the integral equals 1. \(\square\)

---

## Appendix B — Stability of Log-EMA (5.1)

Write \(\theta_t=\Theta_t-\Theta^\star\) and \(r_t=\widehat{r}_t + \eta_t\) with \(\mathbb{E}[\eta_t\mid \mathcal{F}_{t-1}]=0\). The update
\[
\theta_{t+1}=\theta_t + \kappa r_t
\]
has characteristic multiplier \(1-\kappa\) near equilibrium (since \(\partial r/\partial \Theta=-1\) in log-domain). Thus \(|1-\kappa|<1 \Leftrightarrow 0<\kappa<2\) implies exponential mean stability with stationary variance \( \propto \frac{\kappa^2}{1-(1-\kappa)^2}\mathrm{Var}[r] \). \(\square\)

---

## Appendix C — Opportunistic Attachment Bound

Let attachment be triggered when \(H(u)\in[\Theta-A,\Theta)\) so that adding \(A\) crosses the threshold. The measure of this interval under Exp(1) is
\[
\Pr[\Theta-A \le H(u) < \Theta] = e^{-(\Theta-A)} - e^{-\Theta} = e^{-\Theta}(e^{A}-1).
\]
At attempt rate \(\rho\), **attachment events** occur at rate \(\rho e^{-\Theta}(e^{A}-1)\le \rho e^{-\Theta} A\) for small \(A\). This upper-bounds the average **nullifier consumption** rate and shows why caps and TTL suffice to prevent pathological reuse. \(\square\)

---

**End of note.**
