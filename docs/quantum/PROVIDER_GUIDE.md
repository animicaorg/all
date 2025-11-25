# Quantum Provider Guide — Build, Attest, Operate

**Status:** Stable (v1)  
**Audience:** operators building **quantum compute providers** that serve on-chain jobs and are rewarded by AICF (AI Compute Fund).  
**Related:** `aicf/*`, `proofs/quantum.py`, `proofs/quantum_attest/*`, `capabilities/host/compute.py`, `capabilities/adapters/aicf.py`, `randomness/*`

This guide explains how to stand up a provider that:
1) registers & stakes, 2) receives jobs, 3) executes circuits, 4) returns **attested results** and **trap outcomes**, 5) passes **SLA** checks, and 6) gets paid.

---

## 0. TL;DR (Happy Path)

1. **Generate identity** & prepare attestation bundle (TEE host or QPU vendor cert).
2. **Register**: `omni aicf provider_register --bundle attest.json --stake 5000`.
3. **Heartbeat** every 30s: `omni aicf provider_heartbeat`.
4. **Run dispatcher** to accept leases, execute jobs, and upload proof payloads.
5. **Return proof** using `proofs/quantum_attest/*` format.  
6. **Pass SLA** (traps ratio, QoS, latency) ⇒ **payout** credited after settlement.

---

## 1. Roles & Surfaces

- **Chain**: Verifies block proofs and accounts for payouts.
- **AICF**: Off-chain scheduler & treasury accounting that matches jobs ↔ providers.
- **Provider** (you): Runs circuits, returns **attested** outputs + trap results.
- **Contracts / Users**: Submit job requests via `capabilities.host.compute`.

**Interfaces to implement/consume**
- **Queue / Lease API** (AICF): receive job specs, accept/renew leases, upload results.
- **Proof Format** (on-chain): submit evidence in `proofs/quantum.py` format for settlement.
- **Health / Heartbeat**: declare liveness & capacity (`aicf/registry/heartbeat.py`).

---

## 2. Threat Model & Guarantees

- Jobs are incentivized to be **performed faithfully**; cheating should be **caught** with high probability by **trap-circuit auditing** and **attestation**.
- Providers can be temporarily offline; leases will be re-assigned if you time out.
- **Slashing** happens for provable misbehavior (bad attestation, forged outputs, chronic SLA failures).
- **Privacy**: Job payloads are not secrets; model/circuit privacy is out of scope in v1.

---

## 3. Provider Identity & Attestation

A provider must present a **verifiable identity** and a claim about the **execution environment**.

### 3.1 Identity Certificate (Provider)
- **ProviderId** = SHA3-256(public_key) or an X.509 subjectKeyIdentifier, depending on your stack.
- Sign a self-description:
  ```json
  {
    "provider_id": "hex32",
    "capabilities": ["quantum"],
    "contact": {"email":"ops@provider.io"},
    "timestamp": 1735689600,
    "signature": "eddsa-or-pq-sig-over-above"
  }

3.2 Environment Attestation

One (or more) of:
	•	TEE host (SGX/TDX/SEV/CCA) running the job runner:
	•	Evidence parsed by proofs/attestations/tee/* and normalized via proofs/quantum_attest/provider_cert.py.
	•	QPU Vendor Certificate:
	•	Vendor-signed JSON asserting device family, serial, firmware, and a public device_key.
	•	Optional: device-local signing of a job transcript nonce.

See proofs/quantum_attest/provider_cert.py and proofs/quantum_attest/benchmarks.py for accepted schema fields.

⸻

4. Registration & Staking
	1.	Create provider bundle:

cat > provider_bundle.json <<'JSON'
{
  "provider": { "...": "identity fields" },
  "attestations": {
    "tee": { "...": "SGX/SEV/CCA evidence (optional)" },
    "qpu": { "...": "vendor cert & device info (optional)" }
  },
  "algorithms": ["trap_h","trap_xz","qft_small","grover_demo"],
  "regions": ["us-east-1","eu-west-1"]
}
JSON


	2.	Register & stake:

omni aicf provider_register --bundle provider_bundle.json
omni aicf provider_stake --amount 5000


	3.	AICF validates identity & attestations (aicf/registry/verify_attest.py), records stake, and lists your capabilities.

⸻

5. Job Lifecycle

5.1 Job Spec (simplified)

Jobs queued by users/contracts land as:

{
  "job_id": "hex32",
  "kind": "quantum",
  "spec": {
    "circuit_id": "animica.quantum.trapset.v1",
    "width": 20,
    "depth": 120,
    "shots": 4096,
    "trap_ratio": 0.1,
    "seed": "hex32"
  },
  "max_latency_ms": 2000,
  "price": {"units": 1200}
}

AICF will assign a lease to you if you are eligible (stake, health, region, quotas). See aicf/queue/assignment.py.

5.2 Accepting a Lease
	•	A lease includes lease_id, ttl_ms, and job_spec.
	•	You ack to lock the lease and start execution; renew if taking longer than a TTL.

5.3 Executing the Job
	•	Compile/build the circuit with injected trap subcircuits (if job requires).
	•	Run shots including traps; collect outcome stats and trap verdicts.

5.4 Return Result + Attestation
	•	Prepare provider evidence:
	•	Execution transcript hash (input, circuit hash, seed, lease_id).
	•	Device & firmware identities.
	•	If TEE host: quote/report binding the runner measurement and transcript.
	•	Produce QuantumProof (see §7) and send via AICF receiver.
	•	AICF maps results → on-chain proof claim, priced & settled.

⸻

6. SLA & Scoring

AICF evaluates:
	•	Trap success ratio: fraction of trap shots passing expected outcomes.
	•	QoS: uptime, error rates.
	•	Latency: time-to-completion vs max_latency_ms.
	•	Availability: heartbeats and lease completions.

Policy in aicf/types/sla.py and aicf/sla/*.
Repeated failures ⇒ cooldowns or slashing (aicf/registry/penalties.py, aicf/sla/slash_engine.py).

Targets (defaults, network policy may differ)
	•	trap_ratio_pass ≥ 0.98
	•	p95_latency ≤ max_latency_ms
	•	Heartbeat every ≤ 30s when active

⸻

7. Proof Format (On-Chain)

Your result must serialize to the QuantumProof envelope consumed by consensus:

{
  "type_id": "quantum_v1",
  "body": {
    "provider_id": "hex32",
    "circuit_hash": "sha3-256",
    "shots": 4096,
    "trap_ratio": 0.1,
    "trap_pass": 4032,
    "trap_fail": 64,
    "hist_digest": "sha3-256(outcome histogram)",
    "qos": {"latency_ms": 1720, "success": true},
    "attest": {
      "tee": { "... normalized TEE evidence ..." },
      "qpu": { "... vendor cert & device sig ..." }
    }
  },
  "nullifier": "sha3-256(domain|job_id|provider_id)"
}

	•	Schema enforced by proofs/schemas/quantum_attestation.schema.json.
	•	Verifier path: proofs/quantum.py → proofs/quantum_attest/* → proofs/policy_adapter.py.

Important: Include the job transcript hash binding:
H(chain_id | round | lease_id | circuit_hash | shots | seed | params)
This is what attestations must reference/sign to prevent replay.

⸻

8. Heartbeats & Health

Run a small sidecar or cron:

omni aicf provider_heartbeat --provider-id <id> --capacity 8 --qos "normal"

	•	Missing heartbeats degrades your health score.
	•	Capacity tells the matcher how many concurrent leases you can handle.

⸻

9. Pricing & Payouts
	•	Base reward computed from units derived by aicf/economics/pricing.py (width × depth × shots with reference scaling in proofs/quantum_attest/benchmarks.py).
	•	Split per policy (aicf/economics/split.py): provider / treasury / miner.
	•	Settlement per epoch (aicf/economics/settlement.py); query with:

omni aicf list_jobs --status completed
omni aicf get_balance --provider <id>
omni aicf claim_payout --epoch <n>



⸻

10. Slashing & Dispute

You may be slashed for:
	•	Invalid attestation or forged device identity.
	•	Fabricated results (trap mismatch).
	•	Chronic SLA failures across windows.

Appeal window is policy-defined; submit counter-evidence signed by your device/TEE.

⸻

11. Local Dev & Staging

11.1 Dry-Run with Fixtures
	•	Use proofs/fixtures/qpu_provider_cert.json, proofs/fixtures/trap_seed.json.
	•	Simulate a job end-to-end:

python -m proofs.cli.proof_build_quantum \
  --provider provider_bundle.json \
  --circuit-hash deadbeef... \
  --shots 512 --trap-ratio 0.1 --seed cafe... \
  --out proof.quantum.json
python -m proofs.cli.proof_verify proof.quantum.json



11.2 Devnet Integration
	•	Launch local AICF queue, registry, and node (see aicf/README.md, sdk/test-harness/devnet_env.py).
	•	Use aicf/cli/queue_list.py to watch assignments.

⸻

12. Operational Checklist

At launch
	•	Identity key backup with HSM or vault (rotation documented).
	•	Attestation pipeline tested against proofs/tests/test_quantum_attest.py.
	•	Trap implementations reviewed; evidence binds transcript hash.
	•	Heartbeat agent supervised (systemd/k8s/pm2).
	•	Logging & metrics exported: jobs/sec, p50/p95 latency, trap_fail count.

Continuous
	•	Renew vendor certs & TEE cert chains before expiry.
	•	Calibrate benchmarks monthly; width×depth vs real runtime.
	•	Audit SLA dashboard; investigate spikes in trap_fail/latency.

⸻

13. API Sketch (HTTP JSON)

Exact router paths are in aicf/rpc/methods.py. This is a friendly sketch.

	•	POST /aicf/register
	•	Body: provider_bundle.json
	•	POST /aicf/heartbeat
	•	Body: { "provider_id":"...", "capacity":8, "ts": 1735... }
	•	GET /aicf/leases/next?provider_id=...
	•	POST /aicf/leases/ack
	•	Body: { "lease_id":"...", "ttl_ms": 2000 }
	•	POST /aicf/leases/result
	•	Body: { "lease_id":"...", "proof": QuantumProof }

⸻

14. JSON Examples

14.1 Provider Bundle (condensed)

{
  "provider": {
    "provider_id": "c1a2...ff",
    "pubkey": "ed25519:...",
    "contact": {"email":"ops@quantico.dev"},
    "signature": "..."
  },
  "attestations": {
    "qpu": {
      "vendor": "AcmeQPU",
      "device_id": "AQ-7x",
      "firmware": "1.9.3",
      "device_pubkey": "ed25519:...",
      "vendor_signature": "..."
    }
  },
  "algorithms": ["trap_h","qft_small"],
  "regions": ["us-east-1"]
}

14.2 QuantumProof (condensed)

{
  "type_id": "quantum_v1",
  "body": {
    "provider_id": "c1a2...ff",
    "circuit_hash": "f0b1...e7",
    "shots": 4096,
    "trap_ratio": 0.1,
    "trap_pass": 4032,
    "trap_fail": 64,
    "hist_digest": "9ab3...cd",
    "qos": {"latency_ms": 1720, "success": true},
    "attest": {"qpu": { "device_id":"AQ-7x", "firmware":"1.9.3", "sig":"..." }}
  },
  "nullifier": "04c8...de"
}


⸻

15. Best Practices
	•	Deterministic seeding: Always derive execution seed from the lease’s seed field; never from wall-clock randomness.
	•	Transcript-first: Hash the entire input before execution and bind it in your attestations.
	•	Fail-fast: If traps start failing beyond tolerance, abort, mark as failure; do not attempt to fudge results.
	•	Separate control-plane: Don’t mix provider identity keys with device keys; keep rotation independent.
	•	Observe policy: Read aicf/policy/example.yaml and keep within quotas (concurrency, units per epoch).

⸻

16. Troubleshooting
	•	“AttestationError: chain invalid”
Ensure the vendor/TEE root is in proofs/attestations/vendor_roots/*.
	•	“TrapMismatch”
Validate your trap library version matches network policy; verify seeds.
	•	Leases expiring
Increase capacity or use lease renewals; ensure p95 ≤ max_latency_ms.
	•	Underpaid
Check computed units via proofs/quantum_attest/benchmarks.py and the epoch policy.

⸻

17. Compliance & Audits
	•	Keep device & host firmware SBOM and change logs.
	•	Maintain logs linking lease_id → transcript hash → attestation → proof.
	•	Rotate keys quarterly; publish attestation re-issuance windows.

⸻

18. References
	•	proofs/quantum.py — On-chain verification rules
	•	proofs/quantum_attest/* — Provider cert, traps verification, benchmarks
	•	aicf/registry/* — Registration, staking, allowlist, penalties
	•	aicf/sla/* — SLA metrics, evaluator, slashing
	•	capabilities/host/compute.py — Contract syscall bridge
	•	randomness/* — Beacon & round seeds influencing assignments

⸻

Changelog
	•	v1: Initial provider guide with SLA, attestation, leases, and proof schema pointers.
