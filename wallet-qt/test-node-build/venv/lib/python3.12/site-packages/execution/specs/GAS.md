# GAS.md — Gas Accounting & Refund Rules

This document specifies how **Animica** charges, meters, refunds, and settles gas during transaction and block execution. It is consensus-critical and must match the implementation in:

- `execution/gas/{table,intrinsic,meter,refund}.py`
- `execution/runtime/{fees,executor}.py`
- `vm_py/gas_table.json` and `spec/opcodes_vm_py.yaml` (opcode/builtin costs)
- `spec/tx_format.cddl` (for size-related intrinsic costs)

> **Determinism:** Given the same inputs (params, block context, state, tx list), gas results (**gasUsed**, **gasRefundApplied**, **payer/taker balances**) are byte-for-byte identical on all nodes.

---

## 1) Parameters & Inputs

Pulled from `spec/params.yaml` (and exposed via `core/types/params.py`):

- **Intrinsic gas constants**
  - `G_TX_BASE_TRANSFER`, `G_TX_BASE_DEPLOY`, `G_TX_BASE_CALL`
  - `G_TX_SIG_PQ` (per-signature domain cost), `G_ACCESS_LIST_ITEM`, `G_ACCESS_LIST_STORAGE_KEY`
  - `G_TX_PER_BYTE` (canonical envelope + data bytes; CBOR length accounted)
  - (optional) `G_BLOB_BASE`, `G_BLOB_PER_CHUNK` (when DA blobs are attached)

- **Runtime opcode/builtin costs**
  - From `spec/opcodes_vm_py.yaml` → resolved by `execution.gas.table`.

- **Refund policy**
  - `REFUND_CAP_RATIO` ∈ (0, 1]  (e.g., 0.2) — maximum refund as a fraction of **gasUsed**.
  - Allowed refund sources are enumerated in `execution/gas/refund.py`.

- **Fee split policy**
  - `BASE_FEE_MODE` = `off` | `static` | `EIP1559_like`
  - `TREASURY_SPLIT` (0…1) — optional share of base component to treasury, remainder burned.
  - `COINBASE_TIP_SPLIT` (defaults to 1.0) — portion of the *tip* paid to the coinbase.

### Block/Tx Inputs

- `blockCtx`: `baseFee` (if enabled), `coinbase`, `treasury`, `chainId`, height/time.
- `tx`: `gasLimit`, `maxFeePerGas`, `maxPriorityFeePerGas`, envelope bytes, kind, access list, optional blobs.

---

## 2) Intrinsic Gas

Before any stateful work, charge intrinsic gas:

G_intrinsic(tx) =
kind_base(tx.kind)                                   // transfer|deploy|call
	•	G_TX_SIG_PQ                                          // account signature domain
	•	G_TX_PER_BYTE * len(tx.envelope_bytes_cbor)
	•	sum_i ( G_ACCESS_LIST_ITEM + G_ACCESS_LIST_STORAGE_KEY * keys_i )
	•	blob_overheads(tx)                                   // if blobs present

- If `G_intrinsic > tx.gasLimit` ⇒ **reject statelessly** in mempool / admission path.
- Intrinsic gas is **non-refundable**.

---

## 3) Gas Metering (Runtime)

Execution uses a monotone meter:

meter = GasMeter(limit = tx.gasLimit - G_intrinsic)
for each step:
meter.debit(cost)      // throws OOG if exceeded
…perform operation…

**OOG semantics:**
- On first debit that would exceed remaining gas:
  - Status = `OOG`
  - Discard all state changes and logs from the tx.
  - **Refunds do not apply** on OOG.
  - `gasUsed = G_intrinsic + (tx.gasLimit - meter.remaining_before_fault)`
  - Proceed to settlement (Section 5).

**REVERT semantics:**
- Contract-triggered `REVERT`:
  - Discard state changes and logs.
  - `gasUsed` includes all metered work up to the revert point.
  - Refunds **do** apply (capped), see Section 4.

**SUCCESS semantics:**
- Commit state, logs, events as produced.
- Refunds **do** apply (capped), see Section 4.

---

## 4) Refunds

Refunds are accumulated during execution strictly from **allowed sources** (e.g., storage clear, safe cache release), via `RefundTracker.credit(amount)`.

- Let `R_raw` be the accumulated refund.
- Let `U` = `gasUsed_pre_refund` (intrinsic + metered runtime).
- Apply cap:

R_cap = floor(REFUND_CAP_RATIO * U)
R_applied = min(R_raw, R_cap)
gasUsed = U - R_applied

- Refunds apply on **SUCCESS** and **REVERT**, never on **OOG**.
- Refunds **cannot** reduce `gasUsed` below the intrinsic component.

---

## 5) Fee Calculation & Settlement

### 5.1 Effective prices

Following an 1559-like envelope (if enabled):

base = (BASE_FEE_MODE == off)      ? 0
: (BASE_FEE_MODE == static)   ? params.baseFee
:                               blockCtx.baseFee          // dynamic

tipMax = tx.maxPriorityFeePerGas
feeMax = tx.maxFeePerGas
tip = min(tipMax, max(0, feeMax - base))                        // bounded tip
price_effective = min(feeMax, base + tip)                       // ≤ feeMax

- Mempool policy may enforce a higher floor; execution honors what’s signed.

### 5.2 Amounts

Let `G = gasUsed` after refunds. Then:

fee_total = G * price_effective
base_component = G * base
tip_component  = G * (price_effective - base)

treasury_take = base_component * TREASURY_SPLIT
burn_amount   = base_component - treasury_take

coinbase_credit = tip_component * COINBASE_TIP_SPLIT

**Conservation:**

- `payer.balance -= fee_total`
- `treasury.balance += treasury_take` (if configured)
- `burn += burn_amount` (or assign to designated burn sink)
- `coinbase.balance += coinbase_credit`

> If `BASE_FEE_MODE = off`, then `base = 0`, all fees are **tip** to coinbase.

### 5.3 Failure charging

- **SUCCESS / REVERT:** charge `fee_total` computed with post-refund `G`.
- **OOG:** refunds don’t apply; set `G = gasUsed_pre_refund`; charge fully.

Insufficient balance cases are handled **pre-execution** by state checks (admission). At settlement time, the payer must have been debited via journaled write; otherwise the tx must have been rejected earlier.

---

## 6) Ordering & Aggregation

- Gas is metered per tx; receipts record `gasUsed`.
- Block-level aggregates:
  - `gasUsedSum` = sum of per-tx `gasUsed`.
  - `burnSum`, `treasurySum`, `coinbaseTips` are derivable and exported via metrics.

---

## 7) VM & Builtins Charging

- Every VM opcode/builtin has a deterministic cost from `execution.gas.table` (resolved from `spec/opcodes_vm_py.yaml`).
- Capability shims (`capabilities/runtime/*`) expose **metered** calls; their costs are accounted as **runtime gas** and may also produce **off-chain cost units** for economics (non-consensus).

---

## 8) Algorithm (Normative Pseudocode)

```python
def apply_tx(tx, ctx, state):
    # 1) intrinsic
    G_intr = calc_intrinsic(tx, params)
    ensure(G_intr <= tx.gasLimit, InvalidIntrinsic)

    meter = GasMeter(limit = tx.gasLimit - G_intr)
    refunds = RefundTracker()
    try:
        status, logs = execute_runtime(tx, ctx, state, meter, refunds)  # may raise OOG
        U = G_intr + (tx.gasLimit - meter.remaining)
        R_cap = floor(params.REFUND_CAP_RATIO * U)
        R_applied = min(refunds.total(), R_cap)
        G_used = U - R_applied
    except OutOfGas:
        status = OOG
        logs = []
        # no refunds on OOG
        G_used = G_intr + (tx.gasLimit - meter.remaining_before_fault)

    # 2) fees
    base = resolve_base_fee(ctx, params)
    price_eff, base_part, tip_part = price_breakdown(tx, base)
    fee_total = G_used * price_eff

    # 3) settlement (journaled; atomic with receipt write)
    debit(payer(tx), fee_total)
    if base > 0:
        burn(base_part * (1 - params.TREASURY_SPLIT))
        credit(treasury, base_part * params.TREASURY_SPLIT)
    credit(ctx.coinbase, tip_part * params.COINBASE_TIP_SPLIT)

    # 4) receipt
    return Receipt(status=status, gasUsed=G_used, logs=logs)


⸻

9) Invariants & Edge Cases
	•	gasUsed ≤ gasLimit always.
	•	Refund cap guarantees gasUsed ≥ G_intr.
	•	Changing any gas constants/opcodes requires:
	•	Updating spec/opcodes_vm_py.yaml, vm_py/gas_table.json, tests under execution/tests.
	•	Bumping module version and regenerating vectors.

⸻

10) Testing Pointers
	•	execution/tests/test_intrinsic_gas.py — boundary conditions.
	•	execution/tests/test_receipts_hash.py — stable encoding.
	•	execution/tests/test_scheduler_* — serial vs optimistic equivalence.
	•	vm_py/tests/test_gas_estimator.py — static bounds ≥ dynamic usage.

⸻

11) Compatibility Notes
	•	When DA blobs are enabled, blob-related intrinsic components must match da/schemas/blob.cddl sizing rules and execution/adapters/da_caps.py.
	•	Fee split hooks (treasury/burn) must not read external clocks or sources; they only use blockCtx and params.

End of spec.
