# AICF Provider Registry — Onboarding, Staking, Deregistration

This document specifies the **identity**, **attestation**, **staking**, and **lifecycle**
for AI/Quantum providers participating in the AI Compute Fund (AICF).

- Registry code: `aicf/registry/{registry.py, staking.py, verify_attest.py, heartbeat.py, filters.py, penalties.py}`
- Types: `aicf/types/provider.py`
- RPC: `aicf/rpc/methods.py` (`aicf.listProviders`, `aicf.getProvider`, `aicf.getBalance`, `aicf.claimPayout`)
- CLI: `aicf/cli/{provider_register.py, provider_stake.py, provider_heartbeat.py}`

> All objects use **deterministic CBOR** on the wire and **JSON** for UI/RPC views.
> Hashing uses **SHA3-256**; signatures use PQ (Dilithium3/Sphincs+) where applicable.

---

## 1) Roles & Goals

- **Provider**: runs AI or Quantum workloads, returns outputs & on-chain proofs.
- **Registry**: maintains provider identities, attestations, capabilities, stake & status.
- **Matcher/Queue**: selects eligible providers for jobs based on policy & health.
- **Treasury**: holds provider balances; pays out upon settlement.

Goals: Sybil resistance, safety (attested environments), predictable capacity, and
operator accountability through stake & slashing.

---

## 2) Provider Identity & Capability Flags

### 2.1 ProviderId
A stable string of the form:

provider:<sha3_256(pubkey || alg_id)[:12]>

Derived from the provider’s **registry key** (Dilithium3/Sphincs+). The public key
is also used to sign heartbeats and capability updates.

### 2.2 Capability Flags
- `ai` (model families, throughput hints)
- `quantum` (trap families, max depth/width, shot rates)

Capability metadata lives in `aicf/types/provider.py` and is exposed via RPC.

---

## 3) Attestation & Verification

Providers must supply **attestation bundles** that correspond to their runtime:

- **AI**: TEE evidence (Intel SGX/TDX, AMD SEV-SNP, Arm CCA), model runner measurement,
  policy bits (I/O restrictions), and optional acceleration flags.
- **Quantum**: provider identity certificate, trap-circuit policy/support, facility metadata.

Verification flow (`aicf/registry/verify_attest.py`):
1. Parse vendor evidence (X.509/COSE/quote).
2. Validate **root chains** against pinned vendor roots (`proofs/attestations/vendor_roots/*`).
3. Check **measurement**/**policy** against network policy.
4. Record a **verified attestation hash** on the registry record.

Attestation updates are versioned; the latest valid record gates eligibility.

---

## 4) Staking

### 4.1 Minimums & Locks
- **Per capability** minimums: `min_stake_ai`, `min_stake_quantum` (see `aicf/config.py`).
- **Lock period** `stake_lock_blocks`: withdraw only after lock elapses.
- **Cooldown** after slashing or deregistration before funds release.

### 4.2 Operations
- **Stake / Top-up**: increases bonded stake; resets lock timer.
- **Unstake (request)**: enters `UNSTAKING` state; linearly unlocks after lock period.
- **Withdraw**: once unlocked.

All stake movements are recorded in `aicf/treasury/state.py`. RPC exposes balances.

---

## 5) Lifecycle & Status

| Status        | Meaning                                       | Entered By                               |
|---------------|-----------------------------------------------|------------------------------------------|
| `REGISTERED`  | Identity exists, no eligibility yet           | `provider_register`                      |
| `ACTIVE`      | Eligible for assignment                       | `attest_ok && stake >= min && healthy`   |
| `JAILED`      | Temporarily ineligible (faults or policy)     | `slash_engine` / admin                   |
| `UNSTAKING`   | Unlocking stake; not eligible                 | `provider_stake --unstake`               |
| `DEREGISTERED`| Offboarded; records retained for audit        | `provider_deregister`                    |

**State Machine (simplified)**

REGISTERED –(attest+stake+heartbeat)–> ACTIVE
ACTIVE –(slash/health_fail)–> JAILED –(cooldown+recover)–> ACTIVE
ACTIVE –(unstake)–> UNSTAKING –(unlock)–> DEREGISTERED or REGISTERED
REGISTERED –(deregister)–> DEREGISTERED

---

## 6) Health & Heartbeats

- Providers must **heartbeat** periodically (`aicf/registry/heartbeat.py`).
- Heartbeat includes: `provider_id`, `attest_hash`, `capability snapshot`, `lease load`,
  `qos metrics`, signed with provider key.
- The registry keeps an **exponential-decay** health score; matching filters by
  `health >= threshold` and recent RTT/QoS windows.

Missed heartbeats reduce health; prolonged misses can trigger **jailing**.

---

## 7) Matching Eligibility (Filters)

`aicf/registry/filters.py` applies:

- Capability match (AI/Quantum, model family or trap set).
- Region/allowlist/denylist policy.
- Stake ≥ minimum; health ≥ threshold; not jailed; attestation current.
- Quotas (`queue/quotas.py`): concurrent lease caps per provider.

---

## 8) Slashing & Penalties (Interaction)

See `aicf/sla/*` and `aicf/registry/penalties.py`.

Slashable events:
- Invalid proof (attestation or traps fail) after assignment.
- Lease abandonment (timeouts) over threshold.
- QoS/SLA persistent underperformance across windows.

Outcomes: **stake reduction**, **jailing**, **cooldown timers**, and score decay.

---

## 9) Deregistration / Offboarding

Reasons:
- Voluntary exit.
- Policy or compliance change.
- Persistent health/SLA failures.

Process:
1. **Unstake request** → status `UNSTAKING` (not eligible).
2. Continue heartbeats until **all active leases settle** (or expire).
3. After lock & cooldown elapse, **withdraw** remaining stake.
4. **Deregister**: status `DEREGISTERED`. Records remain for audit.

Guards:
- No open leases.
- Outstanding slash liabilities settled.

---

## 10) Data Models (JSON views)

### 10.1 Provider View
```jsonc
{
  "provider_id": "provider:abc123",
  "status": "ACTIVE",
  "capabilities": {
    "ai": { "models": ["llama3-8b","mixtral-8x7b"], "throughput_tps": 2.5 },
    "quantum": { "trap_families": ["clifford","t-depth"], "max_depth": 64, "shots_per_sec": 80 }
  },
  "attest": {
    "hash": "0x…",
    "updated_height": 123450
  },
  "stake": {
    "bonded": "1000000000",
    "min_required": "250000000",
    "status": "BONDED",
    "unlock_height": null
  },
  "health": { "score": 0.98, "rtt_ms_p50": 40, "last_heartbeat": 1713202001 },
  "quotas": { "max_concurrent": 8, "in_use": 2 },
  "region": "us-west-2",
  "metadata": { "endpoint": "https://api.provider.example", "contact": "ops@provider.example" }
}

10.2 Heartbeat (signed payload)

{
  "provider_id": "provider:abc123",
  "attest_hash": "0x…",
  "metrics": { "rtt_ms_p50": 40, "availability": 0.9992 },
  "load": { "leases_active": 2, "leases_capacity": 8 },
  "timestamp": 1713202001,
  "signature": "0x<sig-dilithium3>"
}


⸻

11) RPC & CLI

11.1 RPC
	•	aicf.listProviders({status?, capability?, region?, minHealth?, limit?, cursor?})
	•	aicf.getProvider({provider_id})
	•	aicf.getBalance({provider_id})
	•	aicf.claimPayout({provider_id, epoch}) (ops-controlled; guarded)

11.2 CLI (examples)

# Register + attest
python -m aicf.cli.provider_register --pubkey pub.pem --region us-west-2 --endpoint https://api.provider…

# Stake / top-up
python -m aicf.cli.provider_stake --provider provider:abc123 --amount 1_000_000_000

# Heartbeat (cron)
python -m aicf.cli.provider_heartbeat --provider provider:abc123 --metrics-file metrics.json


⸻

12) Invariants & Security Notes
	•	VK & vendor roots pinned: Attest acceptance depends on pinned roots (zk/registry/*, proofs/attestations/vendor_roots/*).
	•	Stake before eligibility: A provider with insufficient stake cannot be ACTIVE.
	•	No secret material in registry: Endpoints & public metadata only; secrets are provider-held.
	•	Auditability: All status/stake transitions are append-only and signed by either the provider or the registry operator.
	•	Rotation: Attestation and endpoint rotations are supported; eligibility re-evaluated atomically.

⸻

13) References
	•	docs/aicf/OVERVIEW.md — lifecycle & economics
	•	docs/aicf/JOB_API.md — job schemas & settlement
	•	docs/economics/SLASHING.md — slashing policy
	•	proofs/* — attestation & proof envelopes
	•	zk/registry/* — verifier keys (if ZK post-verification is required by policy)

