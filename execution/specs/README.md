# Execution Specs — Overview, Pointers & Invariants

This directory documents the execution layer of **Animica**: how transactions are applied, gas is charged, state is mutated, and receipts are produced — deterministically and verifiably.

## Document map

- [`STATE.md`](./STATE.md) — account/storage model, roots, snapshot/journaling rules.
- [`GAS.md`](./GAS.md) — intrinsic costs, metering, refunds, and fee accounting.
- [`SCHEDULER.md`](./SCHEDULER.md) — serial vs optimistic scheduling and merge/consistency guarantees.
- [`RECEIPTS.md`](./RECEIPTS.md) — receipt shape, hashing/bloom, ordering, and CBOR encoding.
- Gas table source of truth (opcodes & builtins): `spec/opcodes_vm_py.yaml` (resolved at runtime by `execution.gas.table`).

## Non-negotiable invariants (must hold on every node)

### Determinism
- **Pure inputs → unique outputs.** Given `(ChainParams, prior state root, block context, ordered txs)`, the **post-state root**, **receipts root**, and **events/logs bloom** are deterministic.
- **No ambient I/O.** Execution is free of wall-clock time, filesystem/network I/O, RNG, or host nondeterminism. All time/entropy comes from the **block context** and deterministic VM/runtime shims.
- **Canonical encoding.** Any bytes that enter hashing/signing (e.g., receipt hashes) use deterministic CBOR (canonical map ordering; minimal integers).
- **Stable ordering.** Within a block, transactions execute strictly in list order. Events/logs are emitted in program order and committed in that same order.

### State model
- **Account invariants:**
  - `balance ≥ 0`, `nonce ≥ 0`, `code_hash` is immutable except on deploy/self-destruct operations (if/when enabled).
  - Nonce is **strictly monotonic** per account across successful txs (even on REVERT, nonce handling follows `STATE.md` rules).
- **Storage invariants:**
  - Key/value writes are journaled; `commit()` is atomic, `revert()` fully restores pre-checkpoint contents.
- **Root determinism:**
  - The state root is a canonical Merkle over a lexicographically ordered set of key→value pairs (see `STATE.md`). Equal logical state ⇒ equal root bytes.

### Gas & fees
- **No over-spend:** `gas_used ≤ gas_limit`.
- **Monotone meter:** `GasMeter.debit` is monotone; on failure, no further execution occurs beyond the faulting op.
- **Refund cap:** `gas_refund ≤ REFUND_CAP × gas_used` (cap in `GAS.md`). Final `gas_charged = gas_used − min(gas_refund, cap)`.
- **Accounting conservation:** Debits/credits (payer, coinbase, treasury/burn) conserve total supply per the policy in `GAS.md`.
- **Intrinsic gas checked first** (tx envelope size/kind/access-list), then runtime gas is charged as instructions execute.

### Receipts & events
- **Receipt determinism:** For a fixed execution trace, the receipt (status, gasUsed, logs) and its hash are identical across nodes.
- **Event ordering:** Logs are ordered exactly as emitted; bloom/filtering is computed over that order.
- **Encoding:** Receipt CBOR conforms to `spec/tx_format.cddl` and `RECEIPTS.md`, byte-for-byte reproducible.

### Scheduler consistency
- **Serial is canonical.** Results of `SCHEDULER.md#Serial` are the reference semantics.
- **Optimistic = Serial on commit.** Any optimistic/parallel execution that **commits** must be observationally equivalent to serial: same state root, same receipts/logs per tx, same gas accounting.
- **Conflict safety:** If conflicts are detected during optimistic execution, the merge must **not** commit partial side-effects; conflicting txs are re-executed or reverted per `SCHEDULER.md`.

### Access lists
- **Soundness:** Access lists reflect the read/write footprint of execution (`access_list/build.py`).
- **Merge rules:** Union/intersection composition for batching follows `access_list/merge.py`; results are deterministic.

## Interfaces & artifacts

- **Block/Tx contexts:** Defined in `execution.types.context`. Only these provide timing/coinbase/chainId inputs to execution.
- **Results:** `execution.types.result.ApplyResult` is the canonical in-memory result (status/gas/logs/stateRoot).
- **Receipts:** Built via `execution.receipts.builder` and encoded by `execution.receipts.encoding`.
- **Adapters:** Bridges into core DBs and params live in `execution.adapters.*` and must not introduce nondeterminism.

## Change control & compatibility

- **Gas table changes** (new opcodes or cost adjustments) require:
  1) bumping `execution/version.py`,  
  2) updating spec vectors and `vm_py/gas_table.json` (if applicable),  
  3) re-generating any published compatibility matrices.
- **Encoding/schema changes** to tx/receipt/header formats are coordinated in `spec/*.cddl` and mirrored here; any drift is a consensus risk.
- **Scheduler policy** changes must keep the **Serial = ground truth** property and pass the cross-suite determinism tests.

## Test pointers

- Unit tests: `execution/tests/*` cover intrinsic gas, transfers, receipts hashing, scheduler (serial/optimistic), snapshots, access-list build, and end-to-end block execution.
- Fixtures: `execution/fixtures/*` provide canonical genesis state and sample txs.
- Run: `pytest -q execution/tests` (or the repo-level test runner).

## Glossary

- **ApplyResult** — deterministic summary of a tx execution.
- **Intrinsic gas** — cost independent of runtime (kind/size/access list).
- **Refund** — bounded credit applied at the end of execution.
- **Serial/Optimistic** — scheduler modes; optimistic must reduce to serial semantics.

---

If a future change would violate any invariant above, it **must not** ship without an explicit consensus upgrade plan and updated vectors/specs.
