# AICF/Quantum Provider Slashing Logic (v1)

This document specifies the **slashing**, **jailing**, and **cooldown** rules for AI Compute Fund (AICF) providers, including Quantum-capable providers. It aligns with:
- `aicf/registry/*` (identity, stake, status),
- `aicf/sla/*` (metrics, evaluation, slash engine),
- `aicf/economics/*` (pricing, payouts, epochs, settlement),
- `proofs/*` (AI/Quantum proof normalization and validation),
- `capabilities/*` (enqueue/attest/result flow).

> **Goal:** Incentivize availability, correctness, and timely delivery of compute while bounding griefing risk. Slashing is **deterministic**, **evidence-based**, and **epoch-accounted**.

---

## 1) Concepts & State

- **Provider** P: Registered entity with capability flags *(AI, Quantum)*, on-chain **stake** *S*, and status *(Active/Jailed/CoolingDown)*.
- **Job** J: Deterministically identified compute task (from `capabilities/jobs/id.py`) with a **lease** and **deadline**.
- **SLA Metrics** *(per job or window)* from `aicf/sla/metrics.py`:
  - `traps_ratio` (Quantum): fraction of trap-circuit outcomes matching ground truth.
  - `qos` (AI/Quantum): latency and successful completion rate, normalized [0,1].
  - `availability` (AI/Quantum): timely lease heartbeats / deadline adherence.
- **Epoch** E: Settlement window (`aicf/economics/epochs.py`) where evidence is aggregated and payouts/slashes are applied.
- **Slash Event**: `(provider_id, epoch_id, reason, magnitude, evidence_hash)` recorded in `aicf/sla/slash_engine.py`.

---

## 2) Slashable Behaviors & Evidence

| Reason Code | Description | Evidence Source | Severity |
| --- | --- | --- | --- |
| `Q_TRAPS_MISMATCH` | Quantum trap outcomes fall below threshold | `proofs/quantum_attest/traps.py`, attestation bundle | High |
| `Q_ATTEST_INVALID` | Invalid provider cert or attestation chain | `proofs/quantum_attest/provider_cert.py` | Critical |
| `AI_ATTEST_INVALID` | TEE evidence invalid (SGX/SEV/CCA) | `proofs/attestations/tee/*` | Critical |
| `DEADLINE_MISS` | Lease expired without valid completion | Queue timestamps, lease logs | Medium |
| `RESULT_TAMPER` | Digest mismatch vs commitment | Capabilities receipts & proof digests | High |
| `AVAIL_DROP` | Availability below window threshold | Heartbeats, `registry/heartbeat.py` | Medium |
| `LEASE_ABUSE` | Over-claiming capacity, early abandon | Queue assignments & renewals | Low–Medium |
| `FRAUD_REPEAT` | Repeated slash triggers within N epochs | Slash history | Escalating |

**Evidence normalization**:
- All cryptographic proofs are normalized via `proofs/*` and hashed with `sha3_256` to produce an **evidence hash** stored with the event and logged to the settlement journal.

---

## 3) Thresholds & Magnitudes

Let:
- `T_traps`: minimum acceptable trap agreement (e.g., 0.97).
- `T_qos`: minimum QoS (e.g., 0.80).
- `T_avail`: minimum availability (e.g., 0.90).
- `S`: provider stake (units of ANM).
- `β`: slashing aggressiveness scalar per reason.
- `R_epoch`: total rewards credited in the epoch (pre-slash).
- `R_frac`: fraction of epoch rewards clawed back for certain failures.

### 3.1 Quantum Trap Shortfall (continuous)
For observed `r = traps_ratio`:

\[
\Delta = \max(0, T_{\text{traps}} - r)
\]
\[
\text{slash} = \min\big(S,\; \beta_{\text{traps}}\cdot \Delta^2 \cdot S + R_{\text{frac}}\cdot R_{\text{epoch}}\big)
\]

- Quadratic penalty near threshold discourages hovering just below acceptable correctness.
- Typical values: `β_traps ∈ [0.05, 0.25]`, `R_frac ∈ [0.10, 0.50]`.

### 3.2 Invalid Attestation (AI/Quantum)
- If attestation is cryptographically invalid or revoked: **hard slash** = `min(S, β_attest * S)` with `β_attest ∈ [0.25, 1.0]`.
- Provider is immediately **Jailed** and must **Re-attest** and **Restake** after a cooldown.

### 3.3 Deadline Miss / Availability
For deadline misses over window W:
\[
m = \text{miss\_rate} \in [0,1], \quad a = \text{availability}
\]
\[
\text{slash} = \beta_{\text{deadline}}\cdot m \cdot R_{\text{epoch}} + \beta_{\text{avail}}\cdot \max(0, T_{\text{avail}}-a)\cdot S
\]
- Mix of **reward clawback** (operational) and **stake burn** (systemic).

### 3.4 Result Tampering
- If submitted output digest mismatches commitment and intent proves malice:
  - Slash = `min(S, β_tamper * S)` with `β_tamper ∈ [0.5, 1.0]`.
  - Immediate **Jail**, plus **blacklist** until governance review.

---

## 4) Escalation & Cooldown

- **Counters**: Each slash type increments a per-provider counter over a rolling M-epoch window.
- **Escalation schedule** (example):
  - 1st event: magnitude × 1.0, **CoolingDown** for 1 epoch.
  - 2nd event: × 1.5, **Jailed** for 2 epochs, require re-attestation.
  - 3rd+ events: × 2.0, governance flag for permanent removal.
- **Cooldown**: Rewards accrue but are **locked**; withdrawals disabled until window elapses.

---

## 5) Determinism & Auditability

- Slash calculations MUST be reproducible from:
  - **Proof records** (CBOR/JSON) and **attestations**.
  - **Queue/lease** logs and **heartbeats**.
  - **SLA evaluation snapshots**: JSON blobs with input metrics and thresholds.
- `aicf/sla/evaluator.py` outputs a signed **SLA Report**:
  ```json
  {
    "provider_id": "...",
    "epoch_id": 123,
    "metrics": {"traps_ratio": 0.945, "qos": 0.81, "availability": 0.88},
    "thresholds": {"T_traps": 0.97, "T_qos": 0.80, "T_avail": 0.90},
    "reasons": ["Q_TRAPS_MISMATCH","AVAIL_DROP"],
    "suggested_slash": {"amount": "123.45", "currency": "ANM"},
    "evidence_hashes": ["0x…", "0x…"],
    "report_sig": "…"
  }

	•	aicf/sla/slash_engine.py converts the suggested slash to a Slash Event and persists it; aicf/economics/settlement.py applies it at epoch close.

⸻

6) Application Order at Settlement
	1.	Aggregate payouts per provider for E.
	2.	Apply reward clawbacks (operational failures).
	3.	Apply stake burns (security failures): reduce S; if S < S_min, mark Jailed.
	4.	Update cooldown/jail timers.
	5.	Emit Settlement Receipt (per provider): pre/post balances, slashes, evidence references.

No negative balances. If clawbacks exceed payouts, defer excess to stake burn (bounded by S).

⸻

7) Appeals & Overrides
	•	Providers may submit an Appeal Package within K blocks post-settlement containing:
	•	New attestation confirmations, vendor revocation lists, or misclassification proofs.
	•	The Appeals Committee (governance) can:
	•	Downgrade a Critical to High severity with rationale.
	•	Refund up to REFUND_MAX_FRACTION of the burn into locked pending (unlocks after N epochs without incidents).

All overrides are publicly logged with hashes.

⸻

8) Anti-Griefing & Safety
	•	No unbounded third-party accusations: Only chain-verifiable evidence (proofs, attest chains, queue logs) is admissible.
	•	Watchdog windows: Late evidence after E + grace is considered for future reputational scoring, not retroactive slashes, unless cryptographic revocation retroactively invalidates attestations.
	•	Partition tolerance: Availability penalties use provider-local timestamps cross-checked with beacon rounds and network health metrics; broad outages may soften penalties.

⸻

9) Configuration (Policy)

Key	Default	Module
T_traps	0.97	aicf/sla/evaluator.py
T_qos	0.80	aicf/sla/evaluator.py
T_avail	0.90	aicf/registry/heartbeat.py
β_traps	0.15	aicf/sla/slash_engine.py
β_attest	1.00	aicf/sla/slash_engine.py
β_deadline	0.50	aicf/sla/slash_engine.py
β_avail	0.10	aicf/sla/slash_engine.py
β_tamper	0.75	aicf/sla/slash_engine.py
R_frac	0.25	aicf/economics/settlement.py
S_min	chain-specific	aicf/registry/staking.py
cooldown_epochs	1–2	aicf/registry/penalties.py
jail_epochs	2+	aicf/registry/penalties.py


⸻

10) Pseudocode

on_epoch_close(E):
  for P in providers:
    met = sla.collect_metrics(P, E)
    reasons, amt = sla.evaluate_and_price(P, met, policy)
    if amt > 0:
      record_slash(P, E, reasons, amt, evidence_hashes(met))
    payout = rewards[P]
    clawback = min(payout, portion_of(amt, operational))
    stake_burn = amt - clawback
    balances.apply(P, payout - clawback, stake_burn)
    penalties.update_status(P, reasons)
  settlement.emit_receipts(E)


⸻

11) Monitoring & Transparency
	•	Metrics (aicf/metrics.py):
	•	aicf_slashes_total{reason}, aicf_stake_burn_total, aicf_clawbacks_total
	•	SLOs: job_deadline_miss_rate, quantum_traps_ratio_p50/p95, availability_rate
	•	Dashboards should show per-epoch provider standings and a slash ledger with links to evidence hashes.

⸻

12) Versioning & Migration
	•	Slashing policy versions are embedded in the policy hash published each epoch. Clients must pin policy versions to reproduce historical settlements.
	•	Parameter changes follow governance; grace period may apply.

⸻

13) Summary

The AICF slashing framework binds security (attest validity, trap correctness) and operations (availability, deadlines) into a unified, reproducible regime. Penalties scale with severity, escalate with repeat offenses, and are transparent, minimizing rent-seeking and maximizing the reliability of AI/Quantum compute for Animica.

Version: v1.0. Compatible with AICF Gate (registry + staking + SLA + settlement).
