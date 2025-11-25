# AI Compute Fund (AICF) — Overview

The **AI Compute Fund (AICF)** coordinates off-chain AI/Quantum compute with on-chain
accounting and incentives. It pays verified providers for completed jobs, enforces
SLA/attestation rules, and exposes a deterministic, contract-friendly interface via
the **capabilities** subsystem and **proofs**.

This document covers:

- The **treasury slice** that funds AICF,
- The **job lifecycle** (enqueue → assign → prove → settle),
- **Economics** (units → rewards, splits, epochs, slashing),
- Security, monitoring, and operations guidance.

> Background reading
>
> - Capabilities: `docs/spec/CAPABILITIES.md`, `capabilities/specs/*`
> - Proofs: `docs/spec/proofs/OVERVIEW.md`, `docs/spec/proofs/AI_V1.md`, `docs/spec/proofs/QUANTUM_V1.md`
> - Economics: `docs/economics/OVERVIEW.md`, `docs/economics/REWARDS.md`, `docs/economics/SLASHING.md`
> - Randomness & traps: `docs/randomness/OVERVIEW.md`, `docs/spec/proofs/QUANTUM_V1.md`

---

## 1) Roles

- **Requestor (contract/user)** — submits a job through the VM **capabilities** (e.g., `ai_enqueue`).
- **AICF Queue/Matcher** — prioritizes jobs, assigns to eligible providers under quotas.
- **Provider** — off-chain runner (AI/Quantum). Stakes, attests, runs workloads, submits proofs.
- **Treasury** — holds AICF funds, mints per-block/epoch, pays out after verified completion.
- **Miners/Validators** — include proof references in blocks; consensus verifies envelopes.

---

## 2) Treasury Slice

A configurable share of issuance and/or fees funds AICF.

```yaml
# aicf/config.yml (conceptual)
treasury:
  source:
    block_mint_bps: 150   # 1.50% of per-block issuance
    fee_share_bps:  200   # 2.00% of base+tip fees
  split:
    provider_bps: 8000    # 80% to providers
    miner_bps:    1500    # 15% to block producer/miner
    fund_bps:      500    # 5% retained (buffer/ops)
  epoch:
    seconds: 86400        # daily settlement
    gamma_cap: 1_000_000  # cap on units paid per epoch (anti-drain)

Properties
	•	Predictable outlay — epoch caps (Γ_fund) prevent runaway spend.
	•	Counter-cyclical buffer — fund_bps accumulates to smooth volatile demand.
	•	Transparent — exposures, settlement batches, and balances are on-chain (or in the AICF DB with verifiable roots).

⸻

3) Job Lifecycle

enqueue      assign            run           prove           settle
  │           │                │              │               │
  ▼           ▼                ▼              ▼               ▼
┌────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   ┌─────────────┐
│request │→│queue/match│→│ provider  │→│ proof/ref │→  │ settlement  │
│(VM ABI)│  │ quotas    │  │ executes │  │ on-chain │    │ payouts     │
└────────┘  └──────────┘  └──────────┘  └──────────┘   └─────────────┘

3.1 Enqueue (Deterministic)
	•	Contracts call ai_enqueue(model, prompt, …) / quantum_enqueue(circuit, …).
	•	The capabilities/host assigns a deterministic task_id:

task_id = H(chainId | height | txHash | caller | payload)


	•	A JobReceipt is returned and emitted (see capabilities/schemas/job_receipt.cddl).
	•	The request is persisted to the AICF Queue with priority (fee, age, size, requester tier).

3.2 Assignment
	•	The matcher selects an eligible Provider based on:
	•	Capability flags (AI/Quantum, supported models/devices),
	•	Stake and health, regional filters, allowlists,
	•	Quotas: active leases, ai_units/quantum_units headroom.
	•	A Lease is created; provider heartbeats keep it alive.

3.3 Execution & Proof
	•	Provider runs the job off-chain.
	•	For AI_V1, the provider returns digest + optional TEE attestation + QoS.
	•	For Quantum_V1, provider returns trap-circuit results + certs + QoS.
	•	On-chain block includes ProofEnvelope references; consensus verifies or rejects.
	•	The capabilities/jobs/resolver links proofs → task_id → ResultRecord.

3.4 Settlement
	•	At epoch end, the engine aggregates verified jobs:
	•	Price units via schedule (see §4),
	•	Split rewards (provider / miner / fund),
	•	Emit Payout entries and optionally transfer funds (L1 ledger or internal AICF ledger mirrored on-chain).

⸻

4) Economics

4.1 Units & Pricing
	•	AI units reflect compute-normalized work (model class × tokens/sec × latency).
	•	Quantum units reflect circuit depth×width×shots vs reference benchmarks.

Example schedule:

pricing:
  ai:
    # units = sec * model_weight * qos_multiplier
    model_weights:
      llama3-8b:  1.0
      llama3-70b: 6.5
      clip-vit-b: 0.3
    base_rate_per_unit:  15000     # in micro-ANM (1e-6 ANM)
    qos_multipliers:
      p95_latency_ms:
        "<=300": 1.15
        "<=800": 1.00
        "else":  0.80
      availability:
        ">=0.999": 1.10
        ">=0.990": 1.00
        "else":    0.90
  quantum:
    # units = shots * depth_weight * quality_weight
    depth_weight: { "<=16":1.0, "<=32":1.5, "else":2.0 }
    base_rate_per_unit:  50000

4.2 Splits

See treasury slice above. Default split:
	•	Provider — 80% (execution reward),
	•	Miner/Block producer — 15% (inclusion/execution bandwidth),
	•	Fund — 5% (buffer/ops, slashing coverage).

4.3 Epochs & Caps
	•	Epoch accounting ensures:
	•	Budget cap Γ_fund per epoch (units or ANM),
	•	FIFO within priority classes; jobs over cap carry to next epoch,
	•	Price clamps when demand spikes.

4.4 Staking & Slashing

Providers must stake to accept leases. Slashing events:
	•	Missed lease / timeout beyond retry budget,
	•	Bad attestation (quote invalid, trap failure),
	•	QoS chronic failure across windows (availability < threshold),
	•	Fraud proofs (if applicable to future schemes).

Penalties: proportional stake reduction, cooldown/jail, loss of eligibility.

⸻

5) Attestation & Proofs
	•	AI_V1: TEE (SGX/SEV/CCA) quote + model digest + output digest, optional redundancy/traps/QoS.
	•	Quantum_V1: provider cert + trap outcomes + QoS.
	•	Proofs are verified by proofs/* modules; metrics mapped to ψ inputs via proofs/policy_adapter.py.
	•	Capability resolver maps verified proofs → ResultRecord (binds task_id deterministically).

⸻

6) Interfaces

6.1 Contract-facing (VM Stdlib → Capabilities)
	•	ai_enqueue(model: bytes, prompt: bytes, max_tokens: u32, …) -> JobReceipt
	•	quantum_enqueue(circuit: bytes, shots: u32, …) -> JobReceipt
	•	read_result(task_id: bytes) -> ResultRecord?
	•	zk_verify(...) -> bool (optional—used for future ZK-posted results)
	•	Deterministic ids, length caps, and costed units enforced in capabilities/runtime/*.

6.2 RPC (Operator / Explorer)
	•	aicf.listProviders, aicf.getProvider
	•	aicf.listJobs, aicf.getJob
	•	aicf.claimPayout (if L2 ledger), aicf.getBalance
	•	WS: jobAssigned, jobCompleted, providerSlashed, epochSettled

See aicf/rpc/methods.py and aicf/specs/*.

⸻

7) Security Model
	•	Identity & stake bind providers to behavior.
	•	Eligibility filters (region, attestation class, model allowlist).
	•	Lease renewals with heartbeats; tombstones prevent duplicate claims.
	•	Randomness mix (beacon) can seed deterministic assignment shuffles (reduce gaming).
	•	Auditability: every payout references:
	•	provider id,
	•	job id (task_id),
	•	proof ids/hashes,
	•	pricing parameters and multipliers in effect.

⸻

8) Monitoring & SLOs
	•	Metrics:
	•	Queue: size, age, assigns/sec, retries,
	•	Provider: leases, success rate, p50/p95 latency, availability,
	•	Economics: units/epoch, payouts, split totals.
	•	Alarms:
	•	Proof verification failures,
	•	SLA drift (latency/availability),
	•	Budget pressure (cap utilization > X%).

Dashboards derive from aicf/metrics.py; see docs/dev/METRICS.md.

⸻

9) Failure & Recovery
	•	Provider loss → retry with backoff; requeue if lease expires.
	•	Attestation root updates → rolling updates of vendor roots (see proofs/attestations/vendor_roots).
	•	Budget exhaustion → throttle new leases; carry jobs.
	•	Chain reorg → idempotent task resolution; payouts tied to finalized heights.

⸻

10) Worked Example
	1.	Contract calls ai_enqueue("llama3-8b", prompt=...) in block H.
	2.	task_id = H(chainId|H|txHash|caller|payload); receipt stored.
	3.	Matcher assigns to provider:P1 with lease L.
	4.	P1 returns AI_V1 proof in block H+1; consensus verifies.
	5.	Resolver writes ResultRecord(task_id, digest, units=3.2, qos=OK).
	6.	Epoch closes; settlement:
	•	price = units * base_rate * multipliers,
	•	splits per treasury config,
	•	Payout recorded; balances updated.

⸻

11) Configuration Checklist
	•	Treasury: slice sources, splits, epoch caps.
	•	Pricing: model tables, QoS multipliers, quantum depth/quality weights.
	•	Registry: attestation roots, provider allowlist/filters, min stake.
	•	Quotas: max leases/provider, units rate limits (burst + sustained).
	•	Slashing: reasons, magnitudes, cooldown timers.
	•	Observability: metrics endpoints, WS events, audit log retention.

⸻

12) References
	•	aicf/ package (queue, matcher, staking, settlement, RPC)
	•	capabilities/ (host/runtime bindings and resolver)
	•	proofs/ (AI/Quantum verifiers and policy adapter)
	•	docs/economics/*, docs/randomness/*

