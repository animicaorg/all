# AICF Provider Registry — Specification

This document defines the **provider lifecycle**, **attestation requirements**, and **staking rules** for the AI/Quantum Compute Framework (AICF). It is normative for implementations in `aicf/registry/*` and the types in `aicf/types/*`.

Status keywords **MUST**, **SHOULD**, **MAY** follow RFC 2119.

---

## 1. Goals & Roles

- **Providers** offer off-chain compute (AI or Quantum) and submit verifiable proofs.
- **Requesters** (users/contracts) enqueue jobs via `capabilities/host/compute.py`.
- **AICF** matches jobs to eligible providers, verifies proofs, and settles payouts.

The registry ensures only **attested**, **staked**, and **healthy** providers can receive work.

---

## 2. Canonical Types

The following types are defined in code (references are informative):

- `ProviderId`: 32-byte identifier `H("AICF|prov"|attest_pubkey)` (hex encoded).  
  _See_ `aicf/types/provider.py`.
- `CapabilityFlags`: bitset for `AI`, `QUANTUM` (+ optional sub-alg flags).
- `ProviderStatus`: `REGISTERED | ACTIVE | JAILED | RETIRED | SUSPENDED`.
- `Stake`: integer units (chain native units) with lock metadata.
- `Endpoints`: control/data URLs + region tags and supported models/arches.
- `AttestationBundle`: vendor-rooted evidence (TEE/QPU), see §4.
- `Heartbeat`: signed health ping with caps and QoS hints.
- Events: `Enqueued, Assigned, Completed, Settled, Slashed` (_see_ `aicf/types/events.py`).

> **Note**  
> Implementations **MUST** persist providers, stakes, leases and payouts as in `aicf/db/schema.sql`.

---

## 3. Provider Lifecycle

### 3.1 States & Transitions

UNREGISTERED
│ register(attestation)
▼
REGISTERED ── stake≥min ──► ELIGIBLE ── first heartbeat ──► ACTIVE
▲                               │                       │
│ re-attest / update caps       │health decay           │slash/jail/expiry
│                               ▼                       ▼
└──────────── SUSPENDED ◄───── JAILED ◄─────────────── fault/expiry
│
unjail after cooldown + health OK
▼
ACTIVE

ACTIVE ─ retire() ─► RETIRED (immutable)

- **REGISTERED**: identity verified, no job assignments yet.
- **ELIGIBLE**: stake meets min for declared capabilities.
- **ACTIVE**: passing heartbeats, assignable for jobs.
- **JAILED**: temporarily ineligible due to SLA faults or slashing.
- **SUSPENDED**: attestation expired/revoked; re-attestation required.
- **RETIRED**: provider voluntarily exits; no new jobs; withdrawals permitted after delays.

### 3.2 Transition Rules (normative)

- `register()` **MUST** verify attestation (§4) before creating the record.
- `declare_capabilities()` **MUST** be backed by attestation claims.
- `stake_increase()` may occur in any non-retired state.
- `unstake_request()` **MUST** respect lock and cooldown (§5).
- `heartbeat()` **MUST** be signed by the registry key bound in attestation.
- **Expiry**: When attestation `not_after` is in the past, move to **SUSPENDED**.
- **Jail**: On `SlashEvent`, set state to **JAILED** for `penalty.cooldown_blocks`.

---

## 4. Attestation Requirements

### 4.1 Supported Classes

- **AI/TEE**: Intel SGX/TDX, AMD SEV-SNP, Arm CCA.
- **Quantum**: QPU provider identity (X.509 or PQ-hybrid), trap-circuit receipts.

### 4.2 Bundle Shape (high-level)

AttestationBundle {
provider_pubkey,                 # registry key for heartbeats & control plane
hw_class,                        # SGX|TDX|SNP|CCA|QPU
vendor_roots,                    # certs or reference hashes
platform_evidence,               # quote/report/measurement
workload_digest,                 # code/config hash for the job runner
capabilities,                    # {AI:{models:[]}, QUANTUM:{gates:[], depth_max, width_max}}
regions,                         # ISO-3166 region tags (optional)
not_before, not_after,           # validity window
nonce_signature                  # binds a registry challenge (anti-replay)
}

### 4.3 Verification (normative)

Implementations (**`aicf/registry/verify_attest.py`**) **MUST**:

1. Validate vendor chain/roots and CRLs (or embedded trusted hashes).
2. Verify the platform measurement binds `workload_digest` and `provider_pubkey`.
3. Enforce time window with allowable clock skew `Δt` (configurable).
4. Confirm **capabilities** claimed are justified by measurement/config.
5. For **Quantum**: verify trap-circuit receipts and identity chain; record hardware limits.
6. Derive `ProviderId = sha3_256(provider_pubkey)`; collision resistance required.
7. Record `not_after`; scheduler **MUST** stop assigning at/after expiry.

### 4.4 Rotation & Re-attestation

- Key rotation **MUST** be executed via a valid active key signing the handover.
- Capability upgrades/downgrades **MUST** be accompanied by new evidence.
- Revocation feeds (vendor revocation lists) **MUST** trigger **SUSPENDED**.

---

## 5. Staking

### 5.1 Parameters

Configurable in `aicf/config.py` and network policy:

```yaml
staking:
  min_stake:
    AI:      10_000
    QUANTUM: 50_000
  lock_blocks: 21_600         # ~3 days @ 12s
  cooldown_blocks: 7_200      # ~1 day before withdrawal
  slash:
    minor:   {percent: 0.5,  jail_blocks: 3_600}
    major:   {percent: 5.0,  jail_blocks: 21_600}
    severe:  {percent: 25.0, jail_blocks: 86_400}

5.2 Rules
	•	Eligibility: For capability set C, effective_stake ≥ Σ min_stake[c ∈ C].
	•	Locking: Each stake_increase creates/extends a lock expiring at now+lock_blocks.
	•	Unstake: unstake_request(amount) queues amount for release after:
	•	all locks covering that amount expire, and
	•	a global cooldown_blocks elapse.
	•	Withdraw: Possible only from REGISTERED/ACTIVE/JAILED/SUSPENDED (not RETIRED until all jobs settled).
	•	Auto-suspend: If effective stake drops below minimum for current capabilities, SUSPENDED until topped up or capabilities reduced.

5.3 Slashing

Triggered by aicf/sla/slash_engine.py with reason codes (non-exhaustive):
	•	UNVERIFIABLE_PROOF, TRAPS_FAIL, QOS_VIOLATION, LIVENESS_LOSS, CHEATING_ATTEMPT.

Effects:
	1.	Deduct stake * percent/100.
	2.	Emit SlashEvent with reason, amount, block_height.
	3.	Set state to JAILED for jail_blocks.
	4.	Repeat offenses within a rolling window MAY escalate to severe.

Clawback schedule for late detection MUST reserve balance (escrow) until window closes.

⸻

6. Heartbeats & Health
	•	Providers MUST send heartbeat every hb_interval (e.g., 60s) including:
	•	current load, free lease slots, model/gate support, software versions.
	•	Missing N consecutive heartbeats applies a health decay (see aicf/registry/heartbeat.py):
	•	health = max(0, health - decay_per_miss)
	•	health < threshold ⇒ status SUSPENDED until recovered.
	•	Heartbeats MUST be signed by provider_pubkey from the latest attestation.

⸻

7. Eligibility Filters

The matching engine (aicf/registry/filters.py) MUST enforce:
	•	Stake ≥ per-capability minimum (§5.2).
	•	Attestation valid and not expired (§4.3).
	•	Region/allowlist/denylist constraints (see aicf/registry/allowlist.py).
	•	Health score ≥ min_health.
	•	Algorithm/model support matches the job spec.
	•	Quota limits (leases, ai_units/quantum_units) not exceeded.

⸻

8. RPC Surface (read-only)

Mounted via aicf/rpc/mount.py. Method hints:
	•	aicf.listProviders(status?, capability?, region?) -> [ProviderInfo]
	•	aicf.getProvider(providerId) -> ProviderInfo
	•	aicf.getBalance(providerId) -> {staked, escrow, available}
	•	aicf.listJobs(providerId?, status?) -> [JobRecordRef]
	•	aicf.getJob(jobId) -> JobRecord
	•	aicf.claimPayout(payoutId) -> Receipt

Provider registration/staking endpoints are node-admin or CLI paths (see aicf/cli/*) and MUST NOT be exposed publicly without auth.

⸻

9. Security Considerations
	•	Domain separation: All hashes/signatures include "AICF|…" prefixes.
	•	Replay protection: Attestation includes a registry challenge nonce.
	•	Time: Enforce skew bounds and monotonicity for attestation/heartbeats.
	•	Key hygiene: Use PQ-safe keys as policies evolve; rotation is mandatory at deprecation milestones.
	•	Data minimization: Registry stores only what is necessary for matching and audit.

⸻

10. Events & Audit Trail

Every transition MUST emit a structured event into the AICF log stream and, where applicable, persist to block-linked tables (aicf/adapters/block_db.py):
	•	ProviderRegistered, ProviderReattested, StakeChanged, Jailed, Unjailed, Suspended, Retired, SlashEvent.

Audit includes: actor, prior/new values, reason codes, block height, tx hash (if on-chain interaction occurred).

⸻

11. Test Vectors & Conformance

Implementations SHOULD pass:
	•	Registry & attestation: aicf/tests/test_registry.py
	•	Staking & locks: aicf/tests/test_staking.py
	•	Matching eligibility: aicf/tests/test_queue_matcher.py
	•	Slashing: aicf/tests/test_slashing.py, aicf/test_vectors/slashing.json

⸻

12. Example Policy Snippet

policy:
  regions:
    allow: ["US", "EU", "SG"]
    deny:  []
  min_health: 0.6
  attestation:
    skew_sec: 180
    require_traps_for_qpu: true
  quotas:
    leases_max_per_provider: 16
    ai_units_per_epoch: 100_000
    quantum_units_per_epoch: 10_000


⸻

13. Rationale
	•	Attestation-first: prevents Sybil/opaque providers.
	•	Stake & jail: align incentives and provide rapid response to faults.
	•	Health-based gating: avoids cascading failures during provider outages.
	•	Explicit capability proofs: ensures fair, verifiable matching across heterogeneous hardware.

⸻

This spec is versioned with the module. See aicf/version.py and the network policy files for parameterization.
