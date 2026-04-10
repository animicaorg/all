# AICF Security — Abuse Resistance, Slashability, Replay Protection

This document specifies the security model for the **AI/Quantum Compute Fund (AICF)**: who can attack, how we bound damage, what constitutes **slashable** behavior, and how we prevent **replay** and **double-claim** abuse. It is normative for:

- `aicf/registry/*` (identity, staking, allowlists, heartbeat, penalties)
- `aicf/queue/*` (assignment, leases, retries, quotas, TTL)
- `aicf/integration/*` (proofs bridge, randomness bridge)
- `aicf/sla/*` (metrics, evaluation, slashing engine)
- `aicf/economics/*` (escrow, settlement, slashing rules)
- `capabilities/host/*` and `capabilities/jobs/*` (job ids, receipts, result linkage)

Keywords **MUST**, **SHOULD**, **MAY** follow RFC 2119.

---

## 1. Threat Model

### 1.1 Assets
- **Funds**: AICF epoch budget (Γ_fund), provider stakes, provider balances, requestor escrows (optional).
- **Integrity**: Correct mapping from **on-chain proofs** → **claims** → **payouts**.
- **Availability**: Queue assignment, timely completion, and honest sampling/traps compliance.
- **Identity**: Provider identity/attestation keys; registry state; settlement signing key.

### 1.2 Actors
- **Requestor**: Enqueues jobs via capabilities → AICF queue.
- **Provider**: Runs jobs (AI/Quantum), posts attestations/proofs, holds stake.
- **Miner/Node**: Includes proofs in blocks; executes settlement hooks.
- **AICF Service**: Registry, queue, SLA & settlement logic (deterministic, auditable).
- **Adversary**: Any of the above behaving maliciously or collusively.

Assume authenticated transport (chain P2P/RPC), PQ crypto, and that block headers follow Animica’s acceptance predicate.

---

## 2. Provider Identity, Attestation, and Revocation

- Providers **MUST** register with an **attestation bundle** verified by `registry/verify_attest.py`:
  - **AI (TEE)**: SGX/TDX/SEV-SNP/CCA evidence → workload digest binding, platform measurement, vendor root.
  - **Quantum**: QPU provider identity (X.509/EdDSA/PQ-hybrid), **trap-circuit receipts** with failure bounds.
- Registry stores:
  - `provider_id = H("AICF|provider"|pubkey)`; **bech32m** or hex.
  - Capabilities, regions, min stake, endpoints, key fingerprints.
- **Revocation**:
  - Compromised cert chains or out-of-policy measurements **MUST** flip provider to *JAILED* and cancel leases.
  - Attestation policy hash is included in `policy_hash` (settlement/epoch), enabling auditors to replay validation.

---

## 3. Abuse Vectors & Mitigations

### 3.1 Sybil Providers / Stake Grinding
- **Mitigation**: `registry/staking.py` enforces **min stake per capability**; allowlist/region filters; heartbeat health decay.
- **Randomized assignment** (`integration/randomness_bridge.py`): uses beacon seeds to shuffle eligible sets → reduces assignment gaming.
- **Quota** (`queue/quotas.py`): caps concurrent leases and units/provider to bound blast radius.

### 3.2 Job Spam / Griefing by Requestors
- **Mitigation**:
  - **Escrow** (`economics/escrow.py`) or **pre-commit fee** gate for high-cost jobs.
  - **Spec validation** (`capabilities/runtime/determinism.py`): size caps, deterministic shapes, timeouts.
  - **Queue priority** (`queue/priority.py`): fee × age × size × requester tier; low-fee spam is deprioritized.
  - **TTL & GC** (`queue/ttl.py`): expiration prevents backlog ossification.

### 3.3 Free-Riding / Result Theft
- **Mitigation**:
  - Deterministic **task_id** (`capabilities/jobs/id.py`, `aicf/queue/ids.py`):
    ```
    task_id = H("AICF|task" | chainId | height | txHash | caller | payload_digest)
    ```
    Binds outputs to a specific chain context.
  - **Lease binding** (`queue/assignment.py`): job → provider via a signed lease; results from non-leased entities are ignored.

### 3.4 Result Forgery / Attestation Forgery
- **Mitigation**:
  - TEE evidence and **trap receipts** verified in `integration/proofs_bridge.py`.
  - **Redundancy/traps**: policy may inject canary prompts/circuits; failures count to SLA.
  - **Zero-knowledge verify** (optional): `capabilities/host/zk.py` allows embedding succinct verifications for select workloads.

### 3.5 Equivocation / Duplicate Claims
- **Mitigation**:
  - **Nullifiers** per proof type (domain-separated) prohibit reuse across windows.
  - **Claim key** (unique):
    ```
    claim_key = H("AICF|claim" | task_id | block_height | nullifier)
    ```
  - DB uniqueness on `claim_key` + `paid_claims` table. Reorg-safe via finality `k_finality` (claims only settle after k).

### 3.6 Lease Stealing / Front-running
- **Mitigation**:
  - Provider submits proof including **lease_id** and `provider_id`; mismatches are rejected.
  - Assignment randomness and **short lease windows** limit predictability.

### 3.7 Denial-of-Service on Queue / Registry
- **Mitigation**:
  - Rate-limits at RPC; per-IP and per-key buckets.
  - Bounded data structures; `queue/retry.py` with exponential backoff.
  - Proof-of-work style **requestor puzzles** MAY be enabled for public endpoints.

### 3.8 Settlement Fraud
- **Mitigation**:
  - Deterministic batch roots and **PQ signature** over `SettlementReceipt`.
  - **Full replayability**: Anyone can recompute `claims_root` and `payouts_root` from proofs + policy.
  - **Two-person rule** (ops recommendation): signing key usage requires threshold approval (optional).

---

## 4. Slashability: What, When, How Much

Slashing is executed by `sla/slash_engine.py` using rules in `economics/slashing_rules.py`.

### 4.1 Slashable Offenses
| Code | Offense | Evidence | Minimum Penalty |
|------|---------|----------|------------------|
| S1 | **Attestation invalid** (bad cert, revoked, mismatched measurement) | Attestation verification log + vendor CRL/TCB info | `min(slashing_pct_attest * provider_rewards_epoch, stake)` and **JAIL** |
| S2 | **Trap failure / dishonesty** | Signed trap receipts showing non-compliance | `trap_penalty * occurrences` |
| S3 | **QoS breach** beyond grace window | SLA metrics (latency, availability) | proportional to degraded share of rewards |
| S4 | **Equivocation** (conflicting results for same lease) | Dual signed outputs with same `lease_id` | 50–100% of claim rewards + **cooldown** |
| S5 | **Replay attempt** (reused nullifier/claim_key) | On-chain duplicate detection | Forfeit claim + **warning/jail** on repeat |

> Exact magnitudes are policy-driven and MUST be recorded in `policy_hash`.

### 4.2 Process
1. **Detection**: At proof ingestion or post-facto SLA evaluation.
2. **Decision**: Deterministic rule application; build `SlashEvent { provider_id, reason, penalty }`.
3. **Application**:
   - Deduct from **provider portion** of affected PayoutLines in settlement; excess becomes **clawback** debt (`treasury/state.py`).
   - Transition status: `ACTIVE → JAILED` if policy threshold crossed; cooldown timer in `registry/penalties.py`.
4. **Transparency**: Emit `providerSlashed` WS; line items included in `PayoutLine.slashing`.

### 4.3 Safety & False-Positive Guards
- **Confidence windows** (`sla/evaluator.py`): require enough samples before S3 penalties.
- **Grace thresholds**: first minor QoS breach may warn instead of slash.
- **Appeals** (out-of-band): recommended ops practice; chain state remains source of truth.

---

## 5. Griefing Bounds

We aim to bound the cost inflicted by attackers per unit of their cost.

- **Requester → Provider**: Escrow deposits and size/time caps ensure worst-case provider loss ≤ forfeited requester fee.
- **Provider → AICF**: Quotas and min-stake ensure maximum unpaid work exposure ≤ `quota_units * base_rate`.
- **Miner collusion**: Proof acceptance depends on verifiers; bogus inclusions become unpaid Claims; settlement layer refuses without evidence.

---

## 6. Replay & Double-Claim Resistance

### 6.1 Domain Separation & Nullifiers
- Each proof derives a **domain-separated nullifier**:

nullifier = H(“AICF|null” | proof_type | provider_id | task_id | window_id)

where `window_id` ties to block height/epoch policy window.

- `claim_key` (see §3.5) binds to `{task_id, block_height, nullifier}` ensuring:
- Same task across different blocks → different `claim_key`.
- Same proof reused in same window → same `claim_key` → rejected by DB uniqueness.

### 6.2 Reorg Handling
- Claims are **tentative** until `k_finality`; on reorg:
- Remove non-final claims; re-derive from new canonical chain.
- Leases unaffected; settlement only uses finalized claims.

### 6.3 Settlement Replay
- `SettlementReceipt.sig` covers `batch_id` which itself covers roots + totals + `policy_hash`.
- Any mutation alters `batch_id`; replaying old signed receipts on a new policy window is detectable by mismatched `epoch_id/policy_hash`.

---

## 7. Availability & SLA Integrity

- **Heartbeat** (`registry/heartbeat.py`): decays provider health; missing beats reduce eligibility.
- **Metrics source** (`sla/metrics.py`): ingest trap ratios, QoS, latency with signed timestamps; cross-check with job receipts and queue timestamps.
- **Anti-gaming**: random trap injections; providers do not know which jobs carry traps.

---

## 8. Operational Security Guidance

- **Key hygiene**: PQ keys for providers and settlement MUST be HSM-held; rotation at epoch boundaries recommended.
- **Audit trails**: Keep append-only logs of attestation verifications, assignment decisions, and settlement builds.
- **Configuration pinning**: The economics/SLA configs are committed via `policy_hash`; operators SHOULD tag releases with the computed hash.

---

## 9. Invariants (must hold)

1. **No-pay-on-failure**: A payout is applied **iff** claim is valid under current `policy_hash` and not slashed to zero.
2. **Uniqueness**: `claim_key` is unique chain-wide; duplicates never settle twice.
3. **Determinism**: Given the same canonical chain, proofs, and policy, any honest replica computes identical `batch_id`, roots, and line items.
4. **Bounded exposure**: Quotas, TTLs, and escrow ensure adversaries cannot force unbounded resource burn.

---

## 10. Tests & Vectors

Implementations SHOULD pass:

- `aicf/tests/test_registry.py` — attestation & allowlist
- `aicf/tests/test_queue_matcher.py` — quotas/fairness
- `aicf/tests/test_sla_eval.py` — metrics thresholds & confidence windows
- `aicf/tests/test_slashing.py` — penalties, jailing, cooldown
- `aicf/tests/test_integration_proof_to_payout.py` — end-to-end slashing impacts
- `aicf/test_vectors/slashing.json` — expected penalties and flags
- `aicf/test_vectors/assignments.json` — randomness-driven stable assignments

---

## 11. Rationale

- **Stake + quotas** limit Sybil and bound loss.
- **Trap-augmented attestations** raise the cost of dishonesty beyond potential gains.
- **Nullifier + claim_key** provide replay-resistance without global coordination.
- **Policy-hash pinning** makes settlement auditable across versions.

*Versioned with module semver; see `aicf/version.py`.*
