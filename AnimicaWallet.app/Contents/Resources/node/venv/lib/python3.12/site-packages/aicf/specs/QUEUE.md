# AICF Queue — Matching, Leases, Retries

This document specifies the **job matching rules**, **lease lifecycle**, and **retry/TTL** behavior for the AICF scheduler. It is normative for implementations in `aicf/queue/*` and relies on registry and SLA rules in `aicf/specs/REGISTRY.md` and economics limits in `aicf/specs/ECONOMICS.md`.

Status keywords **MUST**, **SHOULD**, **MAY** follow RFC 2119.

---

## 1. Scope & Terms

- **Job**: Work item created from a capabilities enqueue (`AI` or `QUANTUM`). See `aicf/types/job.py`.
- **Provider**: Registered compute service eligible to receive jobs. See `aicf/types/provider.py`.
- **Lease**: Time-bounded assignment contract authorizing a provider to execute a specific job attempt.
- **Attempt**: A single leased execution of a job (attempt `n=0,1,...`).
- **Seed**: Deterministic randomness for tie-breakers:  
  `seed = H("AICF|match" | chain_id | epoch_id | block_height | beacon)`.

---

## 2. Priority Model

Jobs in state **QUEUED** are ordered by a **stable priority score** computed in `aicf/queue/priority.py`:

score(job, now) =
w_fee     * normalize(job.max_fee)           +
w_age     * normalize(now - job.enqueue_ts) +
w_size    * normalize_inverse(job.size_bytes) +
w_tier    * tier_weight(job.requester_tier)

**Normative rules**

1. Implementations **MUST** produce a *total order* with a deterministic tie-break:  
   `(score DESC, enqueue_ts ASC, job_id ASC)`.
2. Weights `w_*` **MUST** be configurable (network policy); defaults:
   - `w_fee=0.55, w_age=0.25, w_size=0.10, w_tier=0.10`.
3. `normalize_inverse(x)` maps larger payloads to smaller contributions.
4. Requester tiers map to fixed addends (e.g., `FREE=0`, `PRO=0.3`, `PREMIUM=0.6`).

---

## 3. Eligibility Filters

Before a job is considered for a provider, the dispatcher **MUST** ensure:

- Provider status `ACTIVE` and attestation valid (see REGISTRY §4).
- Stake ≥ per-capability minimum and not jailed/suspended.
- Quotas not exceeded (`aicf/queue/quotas.py`):  
  - concurrent lease cap,  
  - per-epoch `ai_units/quantum_units`,  
  - optional per-requester rate limits.
- Region/allow/deny policy satisfied.
- Model/gate support matches job spec (models, precision, max depth/width).
- Provider *health* ≥ `min_health` and not past heartbeat timeout.

Implementations **SHOULD** precompute an eligible set per capability for performance.

---

## 4. Matching Algorithm

Dispatcher loop (`aicf/queue/dispatcher.py`) operates in epochs (e.g., every 200–500 ms) or on demand when supply/demand changes.

**Pseudocode (normative skeleton):**
```python
def match(now, seed):
    jobs = PriorityQueue.pop_batch(MAX_BATCH)
    providers = eligible_providers_snapshot(now)

    # Shuffle provider order deterministically for fairness
    providers = deterministic_shuffle(providers, seed)

    for job in jobs:
        for p in providers:
            if not fits(p, job):              # quotas, model, region, stake
                continue
            lease = issue_lease(job, p, now)  # §5
            emit(JobAssigned(job.id, p.id, lease.id))
            break
        else:
            # no provider matched -> requeue with same priority
            PriorityQueue.reinsert(job)

Determinism & Fairness
	•	deterministic_shuffle MUST be Fisher-Yates with PRNG seeded by seed.
	•	A provider-first round-robin emerges across runs at the same height/epoch.
	•	When multiple jobs chase the same scarce capability, the pair (job_id, provider_id, seed) MUST resolve ties by H(pair) ascending.

⸻

5. Leases

Leases are created in aicf/queue/assignment.py and stored in aicf/queue/storage.py.

5.1 Lease Fields

Lease {
  lease_id        = H("AICF|lease"|job_id|provider_id|attempt|seed)
  job_id
  provider_id
  attempt         # 0-based
  t_start         # monotonic dispatcher clock or block-time
  t_deadline      # t_start + T_lease(job.kind)
  renew_by        # t_start + α * T_lease (e.g., α=0.6)
  status          # ACTIVE|RENEWED|CANCELLED|EXPIRED|COMPLETED
}

5.2 Durations (defaults; policy-controlled)
	•	T_lease(AI) = 10 minutes
	•	T_lease(QUANTUM) = 3 minutes
	•	Renewal window α = 0.6 (i.e., provider SHOULD renew after 60% elapsed).

5.3 Rules
	1.	On assignment, job state MUST become ASSIGNED.
	2.	Provider MUST ACK the lease before ack_timeout (e.g., 5 s) or the lease is cancelled and job is requeued with backoff (§6).
	3.	Provider MUST send renew(lease_id) before renew_by if execution continues. Each renewal extends t_deadline by β * T_lease (β ≤ 1, default β=0.5) with a cap renewals_max.
	4.	At t_deadline without complete(), lease EXPIRES → job retry.
	5.	If provider declines, dispatcher MAY mark a cooldown for that provider/job class to avoid thrashing.

5.4 Cancellation
	•	Dispatcher MUST cancel leases when:
	•	Provider transitions to JAILED/SUSPENDED/RETIRED.
	•	Quotas are forcibly reduced (policy update).
	•	A newer attempt already COMPLETED (tombstoning old attempts).

Cancellation emits LeaseCancelled and immediately schedules a retry.

⸻

6. Retries & Backoff

Retry logic in aicf/queue/retry.py governs attempt creation.

6.1 Attempt Counters & Limits
	•	attempt_max per job MUST be enforced (default 6).
	•	Attempts increment on:
	•	ACK_TIMEOUT, LEASE_EXPIRED, PROOF_INVALID, PROVIDER_FAIL.

6.2 Backoff Schedule (deterministic)

backoff(attempt) (in seconds) MUST be:

b0 = base_backoff(kind)   # AI: 5s, QUANTUM: 2s
factor = 2.0
cap = 15 * 60             # 15 minutes
jitter = hash_jitter(job_id, attempt) in [ -0.1, +0.1 ] * value  # deterministic

delay = min(cap, b0 * factor**attempt)
delay = round(delay * (1.0 + jitter))

hash_jitter MUST derive from H("AICF|retry"|job_id|attempt) and be sign-symmetric to avoid global phase lock.

6.3 Retry Reasons → Provider Scoring
	•	ACK_TIMEOUT: apply small health decay to provider.
	•	LEASE_EXPIRED: medium decay; possible denylist for Δ seconds.
	•	PROOF_INVALID: escalate to SLA engine; may trigger slashing.

⸻

7. TTL & Expiration

TTL logic in aicf/queue/ttl.py.
	•	Absolute TTL: job.ttl_abs from enqueue options; if elapsed → EXPIRED.
	•	Relative TTL: ttl_rel_max across attempts; if exceeded → EXPIRED.
	•	GC: Expired jobs MUST be tombstoned with reason and retained for gc_horizon (for audit) before deletion.

Defaults (policy):
	•	ttl_abs_default = 24h (AI), 2h (QUANTUM) if not specified.
	•	ttl_rel_max = 6h.
	•	gc_horizon = 7d.

⸻

8. Completion & Receiver

Completion is handled by aicf/queue/receiver.py.

Rules
	1.	Provider submits complete(lease_id, output_digest, meta) before t_deadline.
	2.	Dispatcher verifies shape and enqueues proof reference (or direct digest) for on-chain claim mapping.
	3.	On acceptance, job transitions to COMPLETED, lease to COMPLETED and a tombstone prevents further attempts.
	4.	A late arrival after tombstone MUST be ignored (idempotence).

⸻

9. Quotas & Fairness
	•	Per-provider concurrent leases: leases_max_per_provider (default 16).
	•	Per-epoch units caps (aicf/economics/epochs.py): ai_units_per_epoch, quantum_units_per_epoch.
	•	Starvation protection: dispatcher SHOULD interleave low-fee jobs every k matches (e.g., 1 in 20) if headroom exists.
	•	Requester fairness: optional per-requester rolling window to prevent monopolization.

⸻

10. Data Model (storage)

Backed by aicf/queue/storage.py + aicf/db/schema.sql.
	•	Tables/Indexes:
	•	jobs(id, kind, spec, state, priority_cache, enqueue_ts, attempts, ttl_abs, ttl_rel_deadline, requester, size_bytes, max_fee, tier, last_error, tombstoned)
	•	leases(id, job_id, provider_id, attempt, t_start, t_deadline, renew_by, status)
	•	job_index(kind, state, priority_cache)
	•	provider_quotas(provider_id, leases_active, units_epoch_used, windows)
	•	All transitions MUST be atomic (SQL transaction) and emit bus events.

⸻

11. Events (bus & WS)

Emitted via aicf/rpc/ws.py:
	•	jobAssigned {jobId, providerId, leaseId, deadline}
	•	jobCompleted {jobId, providerId, leaseId, digest}
	•	jobExpired {jobId, reason}
	•	leaseRenewed {leaseId, deadline}
	•	leaseCancelled {leaseId, reason}
	•	providerSlashed {providerId, reason, penalty}

Clients SHOULD treat events as hints and reconcile via RPC reads.

⸻

12. Security & Abuse
	•	Determinism: All randomness seeded (§1); no wall-clock nondeterminism in ordering.
	•	Replay: Lease IDs domain-separated; expired/cancelled lease completions rejected.
	•	Denial: Per-provider assignment cooldowns after consecutive fails; queue depth guards.
	•	Fair matching: Deterministic shuffle prevents fixed-order bias across runs.

⸻

13. Parameters (defaults)

From aicf/config.py (illustrative):

queue:
  batch_size: 256
  dispatch_interval_ms: 250
  ack_timeout_ms: 5000
  lease:
    ai_secs: 600
    quantum_secs: 180
    renew_alpha: 0.6
    renew_beta: 0.5
    renewals_max: 4
  retries:
    attempt_max: 6
    base_backoff_ai_secs: 5
    base_backoff_quantum_secs: 2
    backoff_factor: 2.0
    backoff_cap_secs: 900
  quotas:
    leases_max_per_provider: 16
    ai_units_per_epoch: 100000
    quantum_units_per_epoch: 10000
  ttl:
    abs_default_ai_secs: 86400
    abs_default_quantum_secs: 7200
    rel_max_secs: 21600
    gc_horizon_secs: 604800


⸻

14. Conformance

Implementations SHOULD pass:
	•	aicf/tests/test_queue_matcher.py — eligibility, quotas, priority tie-breaks
	•	aicf/tests/test_assignment_and_lease.py — lease timers, renewals, cancellations
	•	aicf/tests/test_retry_ttl.py — backoff, expirations, requeueing
	•	aicf/test_vectors/assignments.json — deterministic matching (seeded)

⸻

15. Rationale
	•	Priority blends price signal and liveness to keep queues moving.
	•	Leases with renewals balance unpredictability of workloads vs. scheduler control.
	•	Deterministic shuffle reduces systemic bias without requiring global coordination.
	•	Backoff with deterministic jitter avoids lockstep thundering herd on scarce hardware.

Versioned with module semver; see aicf/version.py and network policy for overrides.
