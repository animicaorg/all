# AICF — Test Vectors

This directory holds **portable, deterministic** test vectors for the *AI/Quantum Compute Fund (AICF)* module.  
Vectors are small JSON/CBOR blobs that exercise core flows end-to-end without depending on a running node.

> All amounts are in the smallest ANIM unit: **atoms**.

---

## Goals

- Ensure **deterministic IDs** and linkage between on-chain proofs and off-chain jobs.
- Validate **pricing & splits** are stable over time given the same inputs.
- Check **assignment & quotas** logic with edge cases.
- Cover **SLA evaluation** and **slashing** scenarios.
- Provide reproducible inputs for integration tests in `aicf/` and consumers in `capabilities/`.

---

## File conventions

- Files are lowercase with hyphens, ending in `.json` (or `.cbor` when noted).
- Each file contains:
  - `"meta"`: version, description, and optional references.
  - `"vectors"`: an array of cases, each with `input` and `expect` sections.

Example header:

```json
{
  "meta": {
    "version": 1,
    "module": "aicf",
    "description": "Deterministic job-id derivation and payout splits"
  },
  "vectors": [ /* ... */ ]
}


⸻

Common fields
	•	chainId (u32), height (u64)
	•	txHash (0x… hex), caller (anim1… or 0x20-byte hex)
	•	task_id (0x… hex, 32 bytes) — deterministic as defined in capabilities/jobs/id.py
	•	job_kind — "AI" or "QUANTUM"
	•	units — integer units for pricing (AI or Quantum unit)
	•	fee_paid — upfront job fee (atoms)
	•	utilization — float ∈ [0,1], used for surge multipliers
	•	sla — object of SLA measures (e.g., p99_latency_ms, traps_ratio, qos_score)
	•	split — { "provider": f, "treasury": f, "miner": f } fractions that must sum to 1.0

⸻

Vector sets (planned & examples)

1) enqueue-and-id.json

Verifies that job task IDs are derived deterministically from (chainId, height, txHash, caller, payload).

Case shape

{
  "input": {
    "chainId": 1337,
    "height": 100,
    "txHash": "0x3f…ab",
    "caller": "anim1qq…",
    "job_kind": "AI",
    "payload_digest": "0x91…ee"  // digest of canonical CBOR envelope
  },
  "expect": {
    "task_id": "0x7db1…c0",
    "stable": true
  }
}

2) assignment-quotas.json

Exercises provider eligibility, lease issuance, and per-provider quotas.

Case shape

{
  "input": {
    "providers": [
      { "id": "provA", "stake": 1_000_000, "caps": ["AI"], "health": 0.98, "region": "us" },
      { "id": "provB", "stake": 200_000,  "caps": ["AI","QUANTUM"], "health": 0.80, "region": "eu" }
    ],
    "quotas": { "provA": { "concurrent": 3 }, "provB": { "concurrent": 1 } },
    "jobs": [
      { "task_id": "0x…01", "job_kind": "AI", "priority": 0.72 },
      { "task_id": "0x…02", "job_kind": "AI", "priority": 0.60 }
    ],
    "random_seed": "0x1234"
  },
  "expect": {
    "assignments": [
      { "task_id": "0x…01", "provider_id": "provA" },
      { "task_id": "0x…02", "provider_id": "provA" }
    ]
  }
}

3) pricing-and-surge.json

Validates tiered pricing, min job fee, and surge multipliers (utilization).

Case shape

{
  "input": {
    "schedule_ref": "../fixtures/payout_rates.json",
    "job_kind": "AI",
    "units": 3200,
    "fee_paid": 400000,
    "utilization": 0.91
  },
  "expect": {
    "base_reward": 3200,
    "tier_rate": 100,
    "surge_multiplier": 1.35,
    "reward_gross": 432000,
    "reward_capped": 432000
  }
}

4) payout-splits.json

Checks provider/treasury/miner split math, rounding, and per-epoch caps.

Case shape

{
  "input": {
    "job_kind": "QUANTUM",
    "reward_gross": 2_200_000,
    "split": { "provider": 0.82, "treasury": 0.13, "miner": 0.05 }
  },
  "expect": {
    "provider": 1_804_000,
    "treasury": 286_000,
    "miner": 110_000,
    "sum_ok": true
  }
}

5) sla-and-slashing.json

Applies SLA rules and verifies slash events and stake reductions.

Case shape

{
  "input": {
    "provider": { "id": "provX", "stake_before": 5_000_000 },
    "sla": { "traps_ratio": 0.01, "p99_latency_ms": 4200, "qos_score": 0.76, "availability": 0.93 },
    "policy": { "latency_max_ms": 3000, "qos_min": 0.85, "availability_min": 0.97 },
    "repeat_offense_window": 10
  },
  "expect": {
    "slashed": true,
    "reason": "LATENCY_QOS_AVAIL",
    "stake_after": 4_500_000,
    "jailed_until_blocks": 200
  }
}

6) proofs-bridge.json

Ensures on-chain AIProof/QuantumProof → JobClaim mapping is correct (nullifiers, task linkage).

Case shape

{
  "input": {
    "proof_envelope": {
      "kind": "AI",
      "task_id": "0x…aa",
      "nullifier": "0x…bb",
      "height": 12345,
      "metrics": { "units": 1500, "qos_score": 0.92 }
    }
  },
  "expect": {
    "claim": {
      "task_id": "0x…aa",
      "height": 12345,
      "units": 1500
    },
    "nullifier_unique": true
  }
}


⸻

Determinism notes
	•	Hashing/IDs use explicit domain tags and canonical encodings.
Any hex value in vectors is lowercase, 0x prefixed, and sized to its domain.
	•	Rounding: splits round down (floor) per leg, then any remainder (≤ 2 atoms) is assigned to the treasury to preserve conservation.
	•	Time fields: when present, ISO-8601 UTC (YYYY-MM-DDTHH:MM:SSZ).

⸻

Validation guidance

Load vectors via your test runner and validate with the corresponding modules:
	•	ID derivation: capabilities/jobs/id.py
	•	Pricing/splits/surge: aicf/economics/pricing.py, aicf/economics/split.py
	•	SLA & slashing: aicf/sla/evaluator.py, aicf/registry/penalties.py
	•	Proofs bridge: aicf/integration/proofs_bridge.py

Suggested pattern (pseudo-pytest):

for vec in data["vectors"]:
    out = pricing.quote(vec["input"])
    assert out.reward_capped == vec["expect"]["reward_capped"]


⸻

Adding new vectors
	1.	Start from an existing set that matches your area.
	2.	Keep inputs minimal and singly-responsible (each case should aim to assert one thing).
	3.	Include a short "note" per case when something is subtle (e.g., boundary tiers).

⸻

Compatibility
	•	Vector schema versioning is tracked via "meta.version".
Bump this when you make breaking changes to field names or semantics.

⸻

Happy testing!
