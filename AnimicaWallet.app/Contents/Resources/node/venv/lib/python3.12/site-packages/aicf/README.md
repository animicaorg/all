# AICF — AI Compute Fund

**Purpose.** The AI Compute Fund (AICF) is Animica’s off-chain compute coordination layer. It:
- matches **AI** and **Quantum** jobs to registered providers,
- ties completed work to **on-chain proofs** (via `proofs/`),
- prices and settles rewards under transparent **policy & SLA** rules,
- and exposes read/write **RPC/WS** endpoints + CLIs for ops and testing.

AICF is *policy-driven* and *deterministic*: identifiers, accounting epochs, and payouts are derived from chain parameters so independent nodes converge on the same state.

---

## Lifecycle (enqueue → assign → prove → settle)

1) **Enqueue.**  
   A contract (via `capabilities/host/compute.py`) or a tool submits a job.  
   A deterministic `task_id = H(chainId | height | txHash | caller | payload)` is derived (see `capabilities/jobs/id.py`).  
   The job is persisted in the **queue** with priority (fee, age, size, requester tier).

2) **Assign.**  
   The dispatcher selects eligible providers using the **registry** (stake, attestation, health, quotas) and grants a **lease** with TTL. Quotas & concurrency caps prevent overload.

3) **Prove.**  
   The provider executes the job and publishes an **attested result**. On-chain, this appears as a `QuantumProof` or `AIProof` included in a block. Off-chain adapters (`aicf/integration/proofs_bridge.py`) map proof → claim (`task_id`, nullifier, metrics).

4) **Settle.**  
   At epoch boundaries, the economics engine prices units (policy), applies **split rules** (provider/treasury/miner), and writes **payouts**. SLAs (traps ratio, QoS, latency) are evaluated; failing providers are **slashed**.

**Data flow (high-level):**

contracts / SDK  ──enqueue──▶  capabilities  ──►  aicf.queue  ──assign──▶ providers
▲                                          ▲                               │
│                                          │                               ▼
proofs ◀──── aicf.integration.proofs_bridge ◀┘                     proofs on-chain
│                                                                            │
└────────── aicf.economics.settlement ◀──────────────────────────────────────┘

---

## Components

- **Registry & Staking** (`aicf/registry/*`): provider identities, attestation checks (TEE/QPU), stake/lockups, allow/deny lists, health heartbeats.
- **Queue & Matcher** (`aicf/queue/*`): persistent job queue (SQLite/Rocks), priorities, quotas, leases, retries/expiry.
- **Economics & Settlement** (`aicf/economics/*`): pricing schedules, split rules, epoch rollover, payout batches.
- **SLA & Slashing** (`aicf/sla/*`): metrics aggregation, threshold checks, penalties and jailing.
- **Treasury** (`aicf/treasury/*`): internal balances; withdraw with delay/cooldown.
- **Integration bridges** (`aicf/integration/*`): link proofs/results to claims; randomness for assignment shuffles; RPC/WS surfacing.
- **RPC & CLI** (`aicf/rpc/*`, `aicf/cli/*`): operational control & inspection.

---

## Threat model (summary) & mitigations

| Threat | Mitigation |
|---|---|
| **Fake results** (no real compute) | Vendor **attestation verification** (SGX/TDX, SEV-SNP, CCA) and **Quantum trap-circuit** checks; results only pay when a **proof** is finalized on-chain. |
| **Result replay / job theft** | Deterministic `task_id` binds to chainId, height, txHash, caller, payload. **Nullifiers** prevent re-use across windows. |
| **Sybils & griefing** | **Stake & registry** gates; **quotas** and **rate limits**; cooldowns and jailing. |
| **QoS degradation** | **SLA evaluator** with rolling windows; slashing and score decay. |
| **Assignment bias** | Stochastic shuffles mixed with chain randomness; region/feature filters; diversity quotas. |
| **Double-claim / double-spend** | Proofs → **claim mapping** is 1:1 via hashes/nullifiers; settlement is deterministic per epoch. |
| **Privacy / sensitive data** | AICF treats payloads as opaque; operators should use **redacted digests** or DA references. |

---

## Configuration

See `aicf/config.py` and `aicf/policy/example.yaml` for:
- **Pricing schedules** (AI/Quantum units → base reward),
- **Splits** (provider / treasury / miner),
- **Stake minima and lockups**,
- **SLA thresholds** & windows,
- **Epoch length** and Γ_fund caps.

**Storage:** default is a local SQLite database; RocksDB is optional. Paths can be set via environment variables or config (exact names in `aicf/config.py`).

---

## Quickstart (devnet)

> Prereqs: Python ≥3.11, a working Animica devnet (or run tests offline), and `pip install -e .` from the repo root.

1. **Register a provider** (using sample attestation):
```bash
python -m aicf.cli.provider_register \
  --provider-id provider1 \
  --caps AI \
  --attestation aicf/fixtures/providers.json

	2.	Stake some units:

python -m aicf.cli.provider_stake --provider-id provider1 --amount 1000

	3.	Submit a job (AI example; or use aicf/fixtures/jobs_ai.json):

python -m aicf.cli.queue_submit --ai --prompt "hello world" --max-units 100
# or:
python -m aicf.cli.queue_list

	4.	(Dev only) Simulate completion without real compute by injecting a result:

python -m aicf.cli.inject_result \
  --task-id <printed_task_id> \
  --result aicf/fixtures/result_example.json

	5.	Settle the current epoch and inspect payouts:

python -m aicf.cli.settle_epoch
python -m aicf.cli.payouts_inspect --provider-id provider1

	6.	Heartbeat (ops):

python -m aicf.cli.provider_heartbeat --provider-id provider1

In production, step 4 is replaced by on-chain proofs: miners include AIProof/QuantumProof in blocks; aicf/integration/proofs_bridge.py maps them to claims; settlements then credit provider balances.

⸻

RPC & WS (read-only in this module)

AICF exposes optional endpoints (mounted in the main FastAPI app):
	•	aicf.listProviders, aicf.getProvider
	•	aicf.listJobs, aicf.getJob, aicf.getBalance, aicf.claimPayout
	•	WS events: jobAssigned, jobCompleted, providerSlashed, epochSettled

See aicf/rpc/methods.py and aicf/rpc/ws.py for exact schemas.

⸻

Metrics

Prometheus counters & histograms in aicf/metrics.py:
	•	enqueue/assign/complete counts, queue depth, lease renewals
	•	SLA pass/fail, slashes
	•	payout amounts per epoch and per capability

⸻

Testing

Run the unit/integration suite:

pytest -q aicf/tests

Useful focused tests:
	•	test_queue_matcher.py — eligibility, quotas, tie-breaks
	•	test_integration_proof_to_payout.py — proof → claim → payout
	•	test_rpc_mount.py — RPC routes work

⸻

Repository layout (selected)

aicf/
  registry/      # provider identities, staking, filters, penalties
  queue/         # storage, priority, leases, dispatcher, retries
  economics/     # pricing, split, escrow, settlement
  sla/           # metrics & thresholds, slashing engine
  treasury/      # internal ledgers & withdrawals
  integration/   # bridges to proofs/capabilities/randomness
  rpc/, cli/     # APIs & ops tools
  fixtures/, tests/  # sample data & pytest suites
  policy/        # example policy with sane defaults


⸻

Notes
	•	All cryptographic identities and signatures are post-quantum by default (Dilithium3/SPHINCS+; Kyber-768 for handshakes).
	•	Economic and SLA policies are network-specific; embed their roots in headers so nodes agree on the active policy set.
	•	Determinism matters: ids, epochs, and settlement batches are derived from chain state so independently run nodes converge.

