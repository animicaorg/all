# Treasury & Settlement — Fee Split / Escrow for Off-Chain Compute

This note defines **how value moves** when contracts use deterministic syscalls to request off-chain AI/Quantum work. It covers: where funds are reserved, who gets paid, when settlements occur, and how reorgs are handled. All rules preserve determinism at the consensus boundary.

Related code paths:
- `capabilities/host/treasury.py` (debit/credit hooks)
- `capabilities/jobs/*` (task_id, receipts, resolver)
- `aicf/economics/*` (pricing, split, settlement, epochs)
- `aicf/treasury/*` (internal ledgers, mint, withdraw)
- `aicf/integration/execution_hooks.py` (apply settlement in-state)
- `execution/runtime/fees.py`, `execution/runtime/system.py` (coinbase/treasury accounts)
- `proofs/*` (proof validation that unlocks settlement)
- `consensus/*` (no direct money flow; just validity & ψ aggregation)

---

## 1) Accounts & ledgers

At minimum the chain defines three special sinks (see `execution/runtime/system.py`):

- **Coinbase** — receives miner splits at block application time.
- **Treasury** — network treasury; holds:
  - (a) **AICF Fund**: block/epoch mints earmarked for compute rewards (`aicf/treasury/mint.py`).
  - (b) **Escrows**: caller reserves captured at enqueue time.
- **Reserved/System** — for future protocol uses (no role here).

AICF also keeps **internal ledgers** (off-chain but applied on-chain during settlement) in `aicf/treasury/state.py`:
- Provider balances (pending/available)
- Escrow positions by `task_id`
- Epoch accounting snapshots

**Invariant:** total supply changes **only** via explicit mint parameters configured for AICF (or global monetary policy). Settlement is otherwise supply-neutral.

---

## 2) Prices, splits, and epochs

### 2.1 Pricing
The economics layer converts normalized work units → amount:

amount = pricing(kind, units, qos, policy)

- AI: units from model/prompt/params/QoS (`aicf/economics/pricing.py`)
- Quantum: units from depth × width × shots + traps/QoS

### 2.2 Split
A deterministic split partitions `amount`:

provider_share = amount * split.provider
treasury_share = amount * split.treasury
miner_share    = amount * split.miner
∑shares = 1.0

Configured in `aicf/economics/split.py`. Splits are **consensus parameters** (embedded via policy roots).

### 2.3 Epochs & caps
Settlement runs per **epoch** (`aicf/economics/epochs.py`) with:
- Epoch budget cap `Γ_fund` (limits outlay from AICF fund).
- SLA filters (only passing jobs/providers settle fully).
- Rollover: unpaid residue carries to the next epoch if configured.

---

## 3) Enqueue-time escrow (deterministic)

When a contract calls `ai_enqueue` / `quantum_enqueue`:

1. **Normalize inputs** → `task_id` (see `capabilities/jobs/id.py`).
2. **Estimate reserve**:

reserve_units = min(request.max_units, policy.reserve_cap)
reserve_amount = pricing_estimate(kind, reserve_units)

3. **Funding source** (policy-driven):
- **Caller-funded** (default on test/dev): debit caller → Treasury Escrow.
- **Treasury-funded** (subsidy mode): earmark from AICF Fund (no caller debit).
- **Hybrid**: caller floor + treasury top-up.
4. **Record escrow** under `task_id` in Treasury ledgers.
5. Return a **JobReceipt** (CBOR) containing `task_id`, `reserved_units`, `funding_mode`.

**Determinism:** failure to meet reserve (insufficient balance / cap exceeded) REVERTS the syscall.

---

## 4) Proof → settlement

Once a block includes a verified AI/Quantum proof and the **resolver** links it to a `task_id`:

1. **Actual cost**  
`actual_amount = pricing(kind, actual_units, qos, policy_at_height)`
2. **Consume escrow**  
- If **caller-funded**: charge from caller’s escrow (held in Treasury). Any **excess reserve** is refunded to the caller.
- If **treasury-funded**: charge the AICF Fund allocation.
3. **Apply split** (provider/treasury/miner).
4. **Accrue provider**: credit provider’s balance in AICF ledgers.
5. **Credit miner**: book miner’s coinbase portion to be paid in the same block (system transfer).
6. **Treasury share**: remains in Treasury (policy burn or accumulation).

**Non-delivery:** if proof is missing by the expiry window (job TTL), release escrow back to the caller; optionally a small penalty fee can be retained per policy.

---

## 5) On-chain application (state changes)

At block application:
- **Miner share** is transferred **immediately** to coinbase using a **system action** (no signature).
- **Provider share** is **not** paid directly each job; instead, it accumulates in the AICF internal balance and is paid in **epoch settlements** to reduce on-chain churn.
- **Escrow refunds** (if any) are returned to callers at proof finalization time (batched safe list allowed).

The plumbing lives in `aicf/integration/execution_hooks.py` and uses:
- `execution/adapters/block_db.py` for height anchoring
- `execution/runtime/fees.py` & `execution/runtime/system.py` to post system debits/credits
- Idempotency keys `(epoch_id, batch_id)` to prevent double-apply on replays

---

## 6) Epoch settlement

At the end (or beginning) of each epoch:
1. Aggregate provider balances → **Payout** entries (`aicf/economics/payouts.py`).
2. Apply SLA verdicts (`aicf/sla/evaluator.py`) to adjust/withhold amounts if policies require.
3. Enforce `Γ_fund` caps; partial-pay or rollover the remainder if needed.
4. Emit a **single** settlement system transaction that:
- Debits Treasury (AICF Fund bucket).
- Credits providers’ addresses.
- Optionally emits structured **Settlement events** for indexing.
5. Persist the settlement batch (idempotent key: `(epoch_id, merkle_root_of_payouts)`).

**Withdrawals:** Providers may optionally hold balances in the AICF ledger and withdraw via a governance-gated flow (`aicf/treasury/withdraw.py`). For baseline networks, epoch settlement pays directly on-chain to the provider address (no manual withdraw).

---

## 7) Reorg safety

- Every booked item references `(height, task_id)` and is applied through idempotent records:
- Job resolution writes are reversed automatically on reorg by replaying from canonical blocks.
- Epoch settlements include previous-epoch range locks; if reorg crosses a boundary, the batch id changes and old batch is voided.
- The **source of truth** is canonical block history; AICF ledgers are derived state.

---

## 8) Configuration (illustrative)

```yaml
# aicf/config.yaml (excerpt)
economics:
ai_unit_price:        12_000       # microunits per AI unit
quantum_unit_price:   40_000
split:
 provider: 0.84
 treasury: 0.08
 miner:    0.08
epochs:
length_blocks:  720
fund_cap:       50_000_000        # max outlay per epoch (microunits)
rollover:       true
reserve:
mode:           caller            # caller | treasury | hybrid
reserve_cap:    5_000_000         # max microunits reserved per task
refund_policy:  full_on_timeout
sla:
traps_min_ratio: 0.90
qos_min:         0.70
mint:
per_block:      0                 # 0 for neutral economics; >0 funds AICF

All consensus-relevant knobs must be committed into policy roots (see spec/poies_policy.yaml / AICF policy) and surfaced via RPC params.

⸻

9) Accounting identities

Let R be total rewards for settled jobs in an epoch:

R = Σ amount(task_i)
R_provider = R * split.provider
R_treasury = R * split.treasury
R_miner    = R * split.miner

Supply delta in epoch:

Δsupply = mint_epoch  (if configured)    # otherwise 0

Escrow neutrality (caller-funded mode):

Σ caller_reserves  -  Σ caller_refunds  = Σ amounts_charged_from_escrow

Total conservation:

Treasury_start + mint_epoch + caller_reserves
  - caller_refunds - payouts_provider - payouts_miner
  = Treasury_end + R_treasury


⸻

10) Errors & edge cases
	•	InsufficientReserve: caller cannot fund reserve_amount → syscall reverts.
	•	ResultTooLarge: results must be summarized or pinned to DA; settlement unaffected.
	•	ExpiredJob: TTL exceeded → escrow refund according to policy.
	•	ProviderJailed: settlement excludes jailed providers this epoch.
	•	FundCapExceeded: partial settlement; remainder rolled over if enabled.

⸻

11) Testing matrix
	•	Unit: aicf/tests/test_pricing_split.py, test_payouts_settlement.py
	•	End-to-end: aicf/tests/test_integration_proof_to_payout.py
	•	Reorg: aicf/tests/test_epoch_rollover.py
	•	Treasury ops: capabilities/tests/test_provider_limits.py (caps), withdraw/mint tests

⸻

12) Security & abuse notes
	•	Deterministic task_id + proof nullifiers prevent double charging.
	•	Reserve limits throttle spam; rate-limits on enqueue are enforced at RPC.
	•	No private keys ever live in AICF components; all payouts are system transfers governed by protocol logic.
	•	Policy versions are bound into headers to avoid split-brain economics.

⸻

Status: Normative for Animica devnet/testnet. Mainnet parameters may further constrain reserve modes, SPLIT ratios, and mint schedules via governance.
