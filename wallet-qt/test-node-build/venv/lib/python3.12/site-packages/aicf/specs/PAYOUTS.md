# AICF Payouts — Settlement, Receipts, Audits

This document specifies how **proof-derived earnings** flow into **provider balances** via epoch **settlements**, how **receipts** are constructed, and how nodes and third parties **audit** the process. It is normative for the implementations in:

- `aicf/economics/{pricing.py,split.py,epochs.py,settlement.py}`
- `aicf/integration/{proofs_bridge.py,execution_hooks.py}`
- `aicf/treasury/{state.py,rewards.py,withdraw.py,mint.py}`
- `aicf/rpc/{methods.py,ws.py}`
- `aicf/db/{schema.sql,migrations/*}`

Status keywords **MUST**, **SHOULD**, **MAY** follow RFC 2119.

---

## 1. Scope & Terms

- **Claim**: Mapping from on-chain proof(s) to a job record and its measured units. See `aicf/integration/proofs_bridge.py`.
- **Payout**: Monetary attribution computed from a Claim using pricing and split rules.
- **Settlement**: An epoch-batched aggregation of payouts applied to provider balances.
- **Epoch**: Accounting window with an explicit **block-height range** and **fund cap Γ_fund** (see `epochs.py`).
- **Treasury**: Internal ledger and, optionally, on-chain account used for mint/splits/escrow (see `treasury/*`).

---

## 2. Lifecycle Overview

1. **Accrual**  
   As blocks are finalized, AICF ingests proofs via `proofs_bridge.py` → **Claims**. Each Claim is keyed by:

claim_key = H(“AICF|claim” | task_id | block_height | nullifier)

Implementations **MUST** enforce uniqueness of `claim_key` (no double-claim).

2. **Pricing & Split**  
For each Claim, compute:

gross = pricing(units, kind, policy_at(block_height))
(to_provider, to_treasury, to_miner) = split(gross, kind, network_policy)

See §5 for normalization, rounding, and caps.

3. **Epoch Settlement**  
At `epoch_end`, after `k_finality` confirmations, aggregate all unpaid Claims in the epoch into a **SettlementBatch** (§3). Apply slashing adjustments and epoch caps (§6).

4. **Apply**  
`settlement.py` writes deltas to `treasury/state.py` (credit provider balances, record splits, record miner shares if applicable). A **SettlementReceipt** is produced (§4) and a `epochSettled` WS event emitted.

5. **Claim/Withdraw**  
Providers query `aicf.getBalance` and **optionally** request a proof of inclusion for their lines. Withdrawals follow `treasury/withdraw.py` (cooldowns/caps).

---

## 3. Settlement Batch

A **SettlementBatch** is a deterministic, reproducible aggregation for one epoch:

SettlementBatch {
epoch_id            # monotonically increasing
height_start, height_end_inclusive
claims_root         # Merkle root of normalized ClaimLines (see below)
payouts_root        # Merkle root of PayoutLines
totals: {
gross_total,
provider_total,
treasury_total,
miner_total
}
cap_applied         # bool
cap_deferral_next   # amount deferred due to Γ_fund
slashing_total      # aggregate penalty applied this epoch
rounding_dust       # accumulator of sub-unit rounding dust
salt                # H(“AICF|settle”|epoch_id|policy_hash)
policy_hash         # commit of economics/split config used
prepared_at_height  # block height used for snapshot/finality
batch_id            # H(“AICF|batch”|epoch_id|claims_root|payouts_root|totals|salt)
}

### 3.1 ClaimLine (canonical)

ClaimLine {
claim_key
provider_id
requester
kind                 # AI | QUANTUM
units                # normalized units after SLA scaling
proof_ref            # digest or on-chain pointer
job_id, lease_id     # optional linkage for audits
block_height
}

### 3.2 PayoutLine (canonical)

PayoutLine {
claim_key
provider_id
kind
gross
split: { provider, treasury, miner }
slashing: { penalty, reason_code }  # zero if none
net_provider = provider - penalty
}

**Normalization & Encoding**  
`ClaimLine` and `PayoutLine` **MUST** be CBOR-encoded with deterministic ordering (canonical CBOR). Hashes above use SHA3-256 with domain tags as shown. All money amounts are integers in the chain's base unit.

**Merkle Construction**  
`claims_root` is a Merkle root over `H(cbor(ClaimLine_i))` (sorted by `claim_key ASC`). `payouts_root` similarly covers `PayoutLine` hashed leaves (sorted by `claim_key ASC`). The sorting **MUST** be stable and deterministic.

---

## 4. Receipts

Two receipt forms are produced:

### 4.1 SettlementReceipt (epoch-wide)

SettlementReceipt {
batch_id
epoch_id
height_start, height_end_inclusive
claims_root, payouts_root
totals
policy_hash
sig                      # PQ signature by the settlement authority key
}

- Signature **MUST** be per-network PQ scheme (Dilithium3 by default).
- The public key/fingerprint is published in the node’s policy registry and headers (see chain policy roots).

### 4.2 ProviderReceipt (per provider, optional)

For each provider with `N` payout lines, a compact receipt can be emitted:

ProviderReceipt {
batch_id, epoch_id
provider_id
lines_hash = H(concat_sorted(H(cbor(PayoutLine_i)) for i where provider_id))
amount_provider = Σ net_provider over lines
merkle_proofs[]  # minimal proofs from payouts_root for each included PayoutLine
sig
}

A `ProviderReceipt` allows a third party to verify inclusion without downloading the full batch.

---

## 5. Pricing, Splits, Rounding

### 5.1 Pricing

`pricing.py` maps `units` to `gross`:

gross = base_rate(kind, policy) * units
gross = gross * qos_multiplier(sla_metrics)   # ∈ [0, 1]
gross = clamp(gross, min_per_claim, max_per_claim)

- `base_rate` and clamps are epoch-parameterized.
- SLA multipliers are derived from `aicf/sla/evaluator.py` and **MUST** be documented in `policy_hash`.

### 5.2 Splits

`split.py` returns `(provider, treasury, miner)` percentages per kind. Invariants:

- `provider + treasury + miner == 1.0` (within rounding tolerance).
- **Rounding Rule**: amounts **MUST** be rounded using **banker’s rounding** to the nearest base unit. Any residual dust accumulates in `rounding_dust` at batch scope and is credited to **treasury** at batch end (prevents drift).

### 5.3 Caps and Deferral

- **Epoch Cap Γ_fund**: If `Σ gross > Γ_fund`, implementations **MUST** scale **pro-rata** by a factor `f = Γ_fund / Σ gross` unless policy dictates **deferral**.
- **Deferral**: If deferring, each `PayoutLine.gross` is left intact but `cap_deferral_next` is set and unpaid remainder is inserted at the **front** of next epoch’s queue with a `deferred_from = epoch_id` tag. Scaling vs. deferral is policy-controlled.

---

## 6. Slashing Adjustments

If a Claim triggers slashing (e.g., failed traps or QoS breach post-facto):

- `slashing_rules.py` computes `penalty` and `reason_code`.
- Penalty **MUST** be deducted from `provider` portion only (never from treasury/miner splits).
- If `penalty > provider`, set `net_provider = 0` and carry **clawback** to provider balance (may drive balance negative or create a debt ledger per `treasury/state.py`).
- All penalties roll into `slashing_total`.

---

## 7. Database & Idempotence

### 7.1 Tables (see `aicf/db/schema.sql`)
- `claims (claim_key PK, provider_id, kind, units, proof_ref, block_height, paid BOOLEAN)`
- `payout_lines (claim_key PK, provider_id, gross, provider, treasury, miner, penalty, reason)`
- `settlements (batch_id PK, epoch_id, roots, totals, policy_hash, sig, prepared_at_height)`
- `provider_balances (provider_id PK, balance, last_updated)`
- `paid_claims (claim_key PK, batch_id)`

### 7.2 Idempotence Rules

- Applying the same `SettlementBatch` **MUST** be idempotent: if `batch_id` exists, no-op.
- `claim_key` **MUST** be unique across `claims` and `paid_claims`.
- A `PayoutLine` cannot be re-issued with different amounts for the same `claim_key` under the same `policy_hash`.

---

## 8. Execution Hook

`integration/execution_hooks.py` ensures that when a block containing proof attestations is applied:

- Corresponding Claims are recorded (or marked final) so they are eligible for the current epoch.
- If network policy mints a per-block *AICF slice* (`treasury/mint.py`), that mint is credited independently of settlements.

---

## 9. RPC & Events

**RPC** (`aicf/rpc/methods.py`):
- `aicf.listJobs`, `aicf.getJob` — introspection (for audits).
- `aicf.getBalance(providerId)`
- `aicf.claimPayout(epochId|batchId, providerId)` — returns `ProviderReceipt`.
- `aicf.listSettlements(startEpoch, limit)` — paginate `SettlementReceipt`s.

**WS Events** (`aicf/rpc/ws.py`):
- `epochSettled { epochId, batchId, totals }`
- `providerSlashed { providerId, reason, penalty }`

Clients **SHOULD** treat events as hints and reconcile via RPC.

---

## 10. Auditing Procedure

An auditor or provider can verify payments independently:

1. Fetch `SettlementReceipt` for `epoch_id`; verify `sig` with the network’s PQ key.
2. Retrieve `payouts_root` and the provider’s `ProviderReceipt`.
3. For each included `PayoutLine`:
   - Recompute pricing and splits using `policy_hash` and archived SLA metrics.
   - Verify Merkle proofs against `payouts_root`.
4. Check:
   - `Σ net_provider == amount_provider` in `ProviderReceipt`.
   - Batch totals equal Σ of all lines (mod `rounding_dust`).
   - No `claim_key` appears outside the epoch height window.
5. If caps were active, confirm `cap_applied` and scaling or deferral rules.
6. Cross-check that `paid_claims` contains all lines from the receipt and none outside it.

**Test Vectors**: `aicf/test_vectors/settlement.json` covers typical and edge cases (caps, slashing, rounding).

---

## 11. Security Considerations

- **Replay Safety**: `batch_id` binds roots, totals, and `policy_hash`. Replaying a receipt with mutated content changes the ID and invalidates the PQ signature.
- **Determinism**: Sorting orders and CBOR canonicalization eliminate encoding variance.
- **Rounding Bias**: Banker’s rounding plus `rounding_dust` sink prevents systematic drift.
- **Nullifier Linking**: `claim_key` includes the proof nullifier to prevent multi-epoch reclaims.
- **Key Rotation**: Settlement signing keys may rotate at epoch boundaries; the active public key fingerprint is included in the chain’s policy root.

---

## 12. Parameters (illustrative defaults from `aicf/config.py`)

```yaml
economics:
  epoch_secs: 3600
  finality_k: 12
  gamma_fund_per_epoch: 200_000_000   # base units
  base_rates:
    ai: 1200         # per unit
    quantum: 5000
  split:
    ai: { provider: 0.80, treasury: 0.15, miner: 0.05 }
    quantum: { provider: 0.82, treasury: 0.13, miner: 0.05 }
  rounding: bankers
  cap_policy: scale   # or 'defer'
slashing:
  max_penalty_pct: 1.0
  debt_allowed: true


⸻

13. Conformance

Implementations SHOULD pass:
	•	aicf/tests/test_pricing_split.py — units→reward; split math
	•	aicf/tests/test_payouts_settlement.py — batch settlement; balances/receipts
	•	aicf/tests/test_integration_proof_to_payout.py — proof → claim → credit
	•	aicf/tests/test_epoch_rollover.py — epoch caps/rollover
	•	aicf/test_vectors/settlement.json — deterministic totals and roots

⸻

14. Rationale
	•	Batching by epoch amortizes verification and enables transparent caps.
	•	Two-root receipt (claims + payouts) separates what happened from how it was priced.
	•	PQ signatures align with Animica’s post-quantum identity and policy roots.
	•	Deterministic data model ensures multiple implementations converge on identical batch IDs and receipts.

Versioned with module semver; see aicf/version.py.
