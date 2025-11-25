# PoIES Worked Examples
**Concrete mixes of hash + AI + Quantum + Storage (and VDF) under caps.**

All examples use the acceptance rule
\[
S \;=\; -\ln u \;+\; \sum \psi_j \;\;\ge\;\; \Theta,\qquad u\sim \mathrm{Uniform}(0,1),
\]
so a single nonce trial accepts iff \(u \le e^{-(\Theta-\sum\psi)}\).
Define the **per-trial success probability**
\[
p \;=\; \Pr[\text{accept in one trial}] \;=\; 
\begin{cases}
e^{-(\Theta-\sum\psi)} & \text{if }\sum\psi<\Theta,\\
1 & \text{otherwise.}
\end{cases}
\]

> **Policy knobs assumed for these examples** (illustrative):
> - Threshold \(\Theta = 6.0\) (nats).
> - Per-type caps: AI ≤ 2.0, Quantum ≤ 2.0, Storage ≤ 1.0, VDF ≤ 0.5.
> - Total cap \(\Gamma = 4.0\) (i.e., \(\sum\psi \le 4.0\) after clipping).
> - Diversity/escort rules may further scale contributions; we call out when used.

---

## 1) Hash-only baseline
- Mix: **Σψ = 0.0** (no useful-work proofs).
- \(p = e^{-(6-0)} = e^{-6} \approx 0.002478752\) (**0.2479%** per trial).

**Trials to 95% success on a template**  
For small \(p\), \(k_{95} \approx \frac{\ln(0.05^{-1})}{p} = \frac{2.995732}{p}\).
- \(k_{95} \approx 2.996 / 0.002478752 \approx 1,209\) trials.

---

## 2) Hash + modest AI proof
- Raw AI score maps to \(\psi_{\text{AI}} = 1.5\) (≤ cap), others 0.  
- **Σψ = 1.5**.
- \(p = e^{-(6-1.5)} = e^{-4.5} \approx 0.011108997\) (**1.1109%**).

Trials to 95%:
- \(k_{95} \approx 2.996 / 0.011108997 \approx 270\) trials.

**Takeaway:** A single decent AI proof cuts trials by ~4.5× vs baseline.

---

## 3) Hash + AI + Storage (encounter a per-type cap)
- AI contributes \(\psi_{\text{AI}} = 1.8\) (≤ 2.0 cap).
- Storage raw would be 0.6 but **clipped at \(\psi_{\text{Storage}}=0.5\)**.
- **Σψ = 1.8 + 0.5 = 2.3**.
- \(p = e^{-(6-2.3)} = e^{-3.7} \approx 0.024723526\) (**2.472%**).

Trials to 95%:
- \(k_{95} \approx 2.996 / 0.024723526 \approx 121\) trials.

**Takeaway:** Small additional proofs help, but caps matter—0.1 of raw storage here is discarded.

---

## 4) Near the total cap (AI + Quantum + Storage + VDF)
- AI at cap: \(\psi_{\text{AI}} = 2.0\).
- Quantum below cap: \(\psi_{\text{Q}} = 1.8\).
- Storage: \(\psi_{\text{S}} = 0.7\) (≤ 1.0).
- VDF at cap: \(\psi_{\text{VDF}} = 0.5\).

Raw sum: \(2.0 + 1.8 + 0.7 + 0.5 = 5.0\) → **clipped to total \(\Gamma = 4.0\)**.  
- **Σψ (after policy) = 4.0**.
- \(p = e^{-(6-4)} = e^{-2} \approx 0.135335283\) (**13.5335%**).

Trials to 95%:
- \(k_{95} \approx 2.996 / 0.135335283 \approx 22.2\) trials.

**Takeaway:** Total cap \(\Gamma\) is a hard ceiling—beyond it, only nonce luck (more trials) helps.

---

## 5) Effect of nonce grinding (k trials) at fixed mix
Let per-trial success be \(p\). With \(k\) independent trials on a template:
\[
P_{\ge1}(k) = 1 - (1-p)^k.
\]

Example with **Σψ = 2.0** → \(p = e^{-(6-2)} = e^{-4} \approx 0.018315639\).
- \(k=1\): \(1.8316\%\)
- \(k=50\): \(1-(0.981684361)^{50} \approx 60.7\%\)
- \(k=100\): \(1-(0.981684361)^{100} \approx 84.3\%\)

**Takeaway:** Returns are quickly saturating in \(k\) on a *single* template.

---

## 6) Strategy comparison: “more ψ” vs “more hash”
Two miners share the network alone for this toy comparison.

- **Miner A (useful-work):** base attempt rate \(R\), **Σψ = 1.5** → \(p_A = e^{-4.5}\approx 0.011109\).  
  Expected wins/sec ∝ \(R \cdot p_A = 0.011109R\).

- **Miner B (hash-heavy):** doubles attempts (**\(2R\)**), **Σψ = 0** → \(p_B = e^{-6}\approx 0.002479\).  
  Expected wins/sec ∝ \(2R \cdot 0.002479 = 0.004958R\).

**Share of wins:** A : B ≈ 0.011109 : 0.004958 ⇒ **~69% vs 31%**.  
**Takeaway:** A single solid ψ move can dominate doubling raw hash in this regime.

---

## 7) Diversity/escort example (illustrative)
Suppose policy penalizes monoculture by a **10% haircut** if any single type >80% of Σψ.

- Before escort: AI \(=2.0\), others tiny so raw Σψ \(= 2.2\) → total clipping off, assume ≤ Γ.  
- AI’s share \(= 2.0 / 2.2 \approx 90.9\% > 80\%\) ⇒ apply 10% haircut to AI: AI becomes **1.8**.  
- New **Σψ = 1.8 + 0.2 = 2.0**, \(p = e^{-(6-2)} = e^{-4} \approx 1.8316\%\).

**Takeaway:** Escort rules can materially change \(p\) when one type dominates.

---

## 8) Quick calculator (pseudo-code)

```python
import math

def accept_prob(theta, psi_sum):
    return 1.0 if psi_sum >= theta else math.exp(-(theta - psi_sum))

def trials_for_confidence(p, conf=0.95):
    # solve 1 - (1-p)^k >= conf
    if p >= 1.0: return 1
    return math.ceil(math.log(1 - conf) / math.log(1 - p))

# examples
theta = 6.0
for psi in [0,1,2,3,4,5,6]:
    p = accept_prob(theta, psi)
    print(psi, p, trials_for_confidence(p))


⸻

9) Reference table ((\Theta=6))

Σψ	(p = e^{-(6-Σψ)})	%
0	0.002478752	0.2479%
1	0.006737947	0.6738%
2	0.018315639	1.8316%
3	0.049787068	4.9787%
4	0.135335283	13.5335%
5	0.367879441	36.7879%
≥6	1.0	100%


⸻

10) Notes & edge cases
	•	Nullifiers & TTLs prevent reuse of the same proof across blocks; replays are rejected.
	•	Header binding in certain proofs (e.g., HashShare) prevents transplanting ψ across templates.
	•	Verifier DoS resisted by strict schema checks and size/time caps (policy-enforced).
	•	Retarget keeps network-wide success rate near target (\lambda_{\text{target}}); do not tune (\Gamma) so high that ψ alone trivializes blocks.

See also:
	•	docs/spec/poies/SCORING.md – mapping raw metrics to ψ and caps.
	•	docs/spec/poies/RETARGET.md – EMA retarget & stability.
	•	docs/spec/poies/SECURITY.md – grinding economics & mitigations.

