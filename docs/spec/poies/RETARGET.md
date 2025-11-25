# Θ Retarget: Keeping Block Rate Stable

> Target a stable block interval \( \tau_{\text{target}} \) (e.g., 2s–12s) by adjusting the acceptance
> threshold \( \Theta \) so that the observed block-rate \( \lambda_{\text{obs}} \) tracks
> \( \lambda_{\text{target}} = 1/\tau_{\text{target}} \), despite changes in hashrate and useful-work.

Related docs:
- Acceptance & scoring: `docs/spec/poies/ACCEPTANCE_S.md`
- Scoring & caps: `docs/spec/poies/SCORING.md`
- Fairness notes: `docs/spec/poies/FAIRNESS.md`
- Implementation: `consensus/difficulty.py`, tests in `consensus/tests/test_difficulty_retarget.py`

---

## 1) Background & Objective

PoIES accepts a candidate when
\[
S \;=\; -\ln u \;+\; \sum \psi \;\;\ge\;\; \Theta
\quad\Longleftrightarrow\quad
u \le e^{-(\Theta - \sum\psi)} =: T_{\mathrm{eff}}.
\]

Aggregating over all attempts, the **per-second success rate** (block-rate) is
\[
\lambda \;\propto\; \mathbb{E}\big[ \mathbf{1}\{u \le e^{-(\Theta - \sum\psi)}\} \big]
\;=\; \mathbb{E}\big[ e^{-(\Theta - \sum\psi)} \big]
\;=\; e^{-\Theta}\, \mathbb{E}\big[e^{\sum\psi}\big].
\]
As conditions change (attempt rate, distribution of \( \sum\psi \)), we adjust \( \Theta \) to keep
\(\lambda\) near \( \lambda_{\text{target}} \).

**Goal:** A simple, stable, monotone controller
\[
\Theta_{k+1} \;=\; \Theta_k \;+\; \Delta\Theta_k
\]
that:
- converges without oscillation,
- resists timestamp jitter and short-term variance,
- rate-limits changes (anti-DoS),
- remains policy-agnostic to proof mixes (caps/diversity handled elsewhere).

---

## 2) Observables & Estimators

Let block \(k\) have wall-clock timestamp \(t_k\) (clamped by timestamp rules; see §6).
Define inter-block intervals \( \Delta t_i = t_i - t_{i-1} \).

Over a rolling window of the **last \(W\) blocks** (e.g., \(W\in\{64,128\}\)) or limiting **max span** \(T_{\max}\)
(e.g., 10×\(\tau_{\text{target}}\)), estimate:

- **Trimmed mean interval**
  \[
  \widehat{\tau} = \mathrm{trim\_mean}\big(\{\Delta t_i\}_{i=k-W+1}^k,\, p\%\big)
  \]
  with a small trim \(p\%\) (e.g., 10%) to remove outliers.
- **Observed rate**
  \[
  \lambda_{\text{obs}} = \frac{1}{\widehat{\tau}}.
  \]

Optionally smooth the **log-rate error** via EMA:
\[
e_k \;=\; \ln\!\frac{\lambda_{\text{obs}}}{\lambda_{\text{target}}},\qquad
\tilde e_k \;=\; \beta\,\tilde e_{k-1} + (1-\beta)\, e_k,\quad \beta\in[0.7,0.95].
\]

Using log-error makes multiplicative errors additive, aiding stability.

---

## 3) Controller: Fractional Retarget with Clamps

We use a **fractional, log-domain** update with a small gain \(\kappa\) and multiple clamps:

\[
\boxed{
\Delta\Theta_k \;=\; \mathrm{clip}\Big( \kappa \cdot \mathrm{deadband}(\tilde e_k;\,\varepsilon),\;
-\Delta\Theta_{\max},\; +\Delta\Theta_{\max} \Big)
}
\]
\[
\Theta_{k+1} \;=\; \mathrm{clip}\big(\Theta_k + \Delta\Theta_k,\; \Theta_{\min},\; \Theta_{\max}\big).
\]

- **Gain \(\kappa\)**: small (e.g., 0.25–0.50) for stability.
- **Deadband**: if \(|\tilde e_k| \le \varepsilon\) (e.g., \(\varepsilon=0.02\)), treat as 0 to avoid chattering.
- **Per-step clamp** \(\Delta\Theta_{\max}\): limits instantaneous change (e.g., 0.20).
- **Absolute clamps** \([\Theta_{\min},\Theta_{\max}]\): prevent pathological difficulty (e.g., \([-10, +40]\)).
- **EMA \(\beta\)**: larger \(\beta\) = smoother, slower response.

**Sign convention.** If blocks are too fast, \(\lambda_{\text{obs}}>\lambda_{\text{target}}\Rightarrow \tilde e_k>0\Rightarrow \Delta\Theta_k>0\Rightarrow\) acceptance tightens; if too slow, \(\Delta\Theta_k<0\Rightarrow\) acceptance loosens.

---

## 4) Micro-Thresholds for Share Targets (Telemetry)

Miners may publish **share events** at effective thresholds
\[
T_{\text{share}} = e^{-(\Theta - \sum\psi)} \cdot \rho,
\]
for calibrated \(\rho \in (0,1)\) (e.g., \(\rho\in\{2^{-8},2^{-12}\}\)).
Nodes **do not** retarget from shares, but shares:
- help pool operations and off-chain monitoring,
- provide early drift signals without impacting consensus.
(Consensus retarget uses *blocks only*.)

---

## 5) Reference Parameters (Illustrative)

Network policy SHOULD specify:

| Symbol | Meaning | Typical |
|---|---|---|
| \(\tau_{\text{target}}\) | Target block interval | 4 s |
| \(W\) | Blocks per window | 64 |
| \(T_{\max}\) | Max time span of window | 40 s |
| \(p\) | Trim % for mean | 10% |
| \(\beta\) | EMA factor | 0.85 |
| \(\kappa\) | Controller gain | 0.35 |
| \(\varepsilon\) | Deadband (log-rate) | 0.02 |
| \(\Delta\Theta_{\max}\) | Per-block clamp | 0.20 |
| \([\Theta_{\min},\Theta_{\max}]\) | Absolute guardrails | \([-10, +40]\) |

These are network-tunable; test via sims before mainnet.

---

## 6) Timestamp Rules & Robustness

Retarget uses block **timestamps** subject to the following **consensus constraints** (summarized):

1. **Monotonic**: \(t_k > t_{k-1}\), with a minimal delta (e.g., \( \ge 0.5\,\mathrm{s}\)).
2. **Bounded drift vs wall-clock**: proposer timestamp must be within \(\pm D\) of local time upon receipt (e.g., \(D=5\,\mathrm{s}\)); violating blocks are rejected or treated conservatively.
3. **Median-of-N** sanity (optional): networks may require alignment with peers’ median arrival times to reduce single-actor skew.

Trimmed means + EMA make the controller **robust** to outliers and occasional manipulation, while clamps prevent overreaction.

---

## 7) Pseudocode (Consensus-Side)

```python
# Inputs each block k:
#   t_k: block timestamp (validated)
#   params: {tau_target, W, T_max, trim_p, beta, kappa, eps, dtheta_max, theta_min, theta_max}

window.append(t_k - t_{k-1})
while len(window) > W or sum(window) > T_max:
    window.pop_left()

# Robust interval estimator
intervals = sorted(window)
trim = int(len(intervals) * trim_p)
intervals = intervals[trim: len(intervals)-trim] if len(intervals) > 2*trim else intervals
tau_hat = max(mean(intervals), 1e-6)
lambda_obs = 1.0 / tau_hat
lambda_target = 1.0 / params.tau_target

# Log-rate error with EMA
e = math.log(lambda_obs / lambda_target)
e_ema = beta * e_ema_prev + (1 - beta) * e

# Deadband
if abs(e_ema) <= eps:
    e_ctl = 0.0
else:
    e_ctl = e_ema

# Fractional update with clamps
dtheta = clamp(kappa * e_ctl, -dtheta_max, +dtheta_max)
theta_next = clamp(theta_prev + dtheta, theta_min, theta_max)

persist(theta_next)


⸻

8) Stability & Tuning Notes
	•	Why log-domain? Multiplicative rate errors become linear, enabling proportional control that behaves similarly across scales.
	•	Why clamps? Prevent oscillation and adversarial “yo-yo” by bounding response per block and overall.
	•	Window vs EMA. Window smooths observation; EMA smooths error; both are needed for stability under bursty arrivals.
	•	Deadband. Suppresses noise around the target; pick (\varepsilon) ≈ (1%)–(3%) in log-rate.
	•	Tuning method. Start with small (\kappa), simulate step changes in aggregate work and (\mathbb{E}[e^{\sum\psi}]), increase until settling time (e.g., < 2–3 windows) without overshoot > 20%.

⸻

9) Interaction with Caps & Diversity

Retarget controls global rate only; it does not modify:
	•	per-type caps or total (\Gamma),
	•	escort/diversity parameter (q),
	•	(\alpha)-tuner for fairness.

Those levers shape (\mathbb{E}[e^{\sum\psi}]) and distributional fairness; retarget adapts (\Theta) around whatever policy yields.

⸻

10) Security Considerations
	•	Timestamp Push/Pull. Trimming & drift bounds reduce individual influence; multi-block windows dilute sustained bias requirements for an attacker.
	•	Withholding Attacks. Deliberately holding blocks makes (\lambda_{\text{obs}}) appear low → controller loosens (\Theta), briefly rewarding honest miners. Per-step clamps limit how much benefit attackers can create.
	•	Oscillation. Over-large (\kappa) or too-small windows cause ringing. Keep (\kappa) moderate and (W) ≥ 32, enable deadband.
	•	Network Partitions. Each partition will retarget to its own rate. On healing, fork-choice resolves; (\Theta) re-converges within a few windows.

⸻

11) Test Plan (Must Pass)
	•	Step response: Double/halve attempt rate; settle within ≤ 3 windows; overshoot < 20%.
	•	Noise robustness: Add ±20% timestamp jitter; no instability.
	•	Adversarial drift: Inject 1% timestamp bias per block for a single proposer; minimal effect on (\Theta) after trimming.
	•	Bounds: Saturation at (\Theta_{\min/\max}) never causes NaNs; recovery when conditions normalize.

See: consensus/tests/test_difficulty_retarget.py.

⸻

12) Wire & Persistence
	•	( \Theta ) is stored in consensus state (or header field if externally visible).
	•	Header includes current ( \Theta ) (or a commitment), allowing light clients to reconstruct acceptance dynamics.
	•	Policy parameters (e.g., (W,\beta,\kappa,\varepsilon,\Delta\Theta_{\max})) live in spec/params.yaml and are versioned/upgradable via standard governance gates.

⸻

