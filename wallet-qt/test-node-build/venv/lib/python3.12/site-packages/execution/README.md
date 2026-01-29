# `execution/` — Deterministic State Machine (Python-VM host)

This module implements Animica’s **execution layer**: a deterministic Python-VM host with canonical gas accounting, receipts, and a serial (plus experimental optimistic) scheduler. It is designed to be *pure* and *replayable*: given the same inputs (genesis, block/tx bundle, params), every correct node must produce identical post-state and receipts.

---

## What lives here

- **Types & helpers**: `execution/types/*` (status, events, contexts, gas types)
- **Gas**: `execution/gas/*` — table loader, intrinsic costs, metering, refunds
- **State**: `execution/state/*` — accounts, storage, journaling, snapshots, receipts
- **Runtime**: `execution/runtime/*` — Tx/Block dispatcher, transfers/contracts, fee split
- **Scheduler**: `execution/scheduler/*` — serial baseline + optimistic prototype
- **Receipts**: `execution/receipts/*` — build/encode receipts (CBOR)
- **Adapters**: `execution/adapters/*` — bridges to core DBs, DA caps, params
- **CLI**: `execution/cli/*` — run single tx or apply a CBOR block locally
- **Fixtures & tests**: `execution/fixtures/*`, `execution/tests/*`
- **Spec notes**: `execution/specs/*` — GAS/STATE/SCHEDULER/RECEIPTS

---

## Invariants (must always hold)

1. **Determinism**
   - No wall-clock, filesystem, network, or process I/O from the runtime.
   - Randomness API is deterministic and seeded from the **tx hash** (tests cover stability).

2. **Canonical encoding**
   - Inputs/outputs use deterministic CBOR (sorted keys, canonical ints); “SignBytes” match `spec/*`.

3. **Gas correctness**
   - Intrinsic gas depends only on tx kind + sizes.
   - Metering is monotone; negative gas or overflow is impossible (checked arithmetic).
   - Refunds are bounded and finalized exactly once (see `gas/refund.py`).

4. **State & receipts**
   - State transitions are atomic per tx: either full commit or revert with a precise receipt.
   - Receipt bloom/hash are stable and derive solely from logged events and gas usage.

5. **Isolation**
   - Contract execution is sandboxed (Python-VM). Any host capability (DA/pinned blobs, AI/Quantum, zk) is mediated by feature-flagged adapters and accounted for in gas.

6. **Reproducible roots**
   - Given the same pre-state, params, and ordered tx list, the post-state root and receipts root MUST match across nodes.

---

## Quickstart (local, no node required)

> Requirements: Python 3.11+, `pip`, optional `uvloop` for speed. From repo root:

```bash
# (1) Install module in editable mode with test deps
python -m venv .venv && source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"  # or `pip install -e .` plus your test extras

# (2) Smoke-test fixtures: run a single transfer tx against an in-memory state
python -m execution.cli.run_tx \
  --genesis execution/fixtures/genesis_state.json \
  --tx execution/fixtures/tx_transfer_valid.cbor

Expected output (abbrev.): status SUCCESS, gasUsed, logs (may be empty), and a receipt hash.

Try a failing case:

python -m execution.cli.run_tx \
  --genesis execution/fixtures/genesis_state.json \
  --tx execution/fixtures/tx_transfer_insufficient.cbor
# -> status REVERT or OOG/InsufficientBalance per the fixture, deterministic gasUsed

Apply a whole block (CBOR):

python -m execution.cli.apply_block \
  --genesis execution/fixtures/genesis_state.json \
  --block path/to/block.cbor \
  --print-head

These CLIs operate purely in-process; no DB is required. For persistence with the core node, use the adapters below.

⸻

Using adapters (persist to core DB)

When running alongside the node:
	•	execution/adapters/state_db.py bridges reads/writes to core/db/state_db.py (SQLite/Rocks).
	•	execution/adapters/block_db.py stores receipts/logs alongside blocks.
	•	execution/adapters/params.py loads ChainParams from core/types/params.py.

A typical node will:
	1.	Decode a block → execution/runtime/executor.apply_block
	2.	Persist new balances/storage via state_db adapter
	3.	Build receipts (receipts/builder.py), encode to CBOR, and attach to the block record

⸻

Gas & params
	•	Gas tables are resolved from spec/opcodes_vm_py.yaml and bundled defaults in vm_py/gas_table.json.
	•	The execution layer loads resolved costs via execution/gas/table.py.
	•	Chain knobs (limits, refund caps) come from spec/params.yaml via adapters/params.py.

⸻

Schedulers
	•	Serial (scheduler/serial.py): Canonical, deterministic baseline; always enabled.
	•	Optimistic (scheduler/optimistic.py): Experimental parallelism with conflict detection:
	•	Captures read/write sets (scheduler/lockset.py)
	•	Merges non-conflicting results, reverts conflicting txs and re-runs serially
	•	Always yields the same final state as pure serial

Enable via a feature flag or CLI option where provided; never changes on-chain semantics.

⸻

Receipts & logs
	•	execution/state/events.py collects logs during runtime; order is deterministic.
	•	execution/receipts/builder.py → Receipt dataclass (status, gasUsed, logs)
	•	execution/receipts/encoding.py → canonical CBOR
	•	execution/receipts/logs_hash.py → logs bloom/root utilities

⸻

Development workflow

Run unit tests:

pytest -q execution/tests

Common targets:
	•	test_transfer_apply.py — transfer semantics & deterministic state root
	•	test_intrinsic_gas.py — boundary OOG conditions
	•	test_scheduler_serial.py / test_scheduler_optimistic.py — scheduler guarantees
	•	test_receipts_hash.py — encoding stability vs. spec vectors
	•	test_access_list_build.py — trace → access-list helpers

Type checking:

pyright execution  # or mypy if configured


⸻

Embedding the executor (Python snippet)

from execution.runtime.executor import apply_block
from execution.adapters.params import load_chain_params
from execution.state.accounts import Account
from execution.state.view import StateView
from execution.state.journal import Journal

params = load_chain_params(...)           # load from core/spec
state = {}                                # toy KV (addr -> Account), use adapters for real DB
journal = Journal(state)

# `block` is a decoded, typed object (see core/types/block.py)
result = apply_block(block, params, journal)
print(result.state_root, result.receipts_root)
journal.commit()                          # persist if you’re not using adapters


⸻

Safety notes
	•	The execution layer never performs external I/O during tx execution.
	•	Any optional capability (DA pin, AI/Quantum, zk.verify) is mediated through adapters with strict determinism guards and length caps; by default these are no-ops in local runs.
	•	Do not swap gas tables or params at runtime without a network upgrade; this breaks consensus.

⸻

See also
	•	VM: vm_py/ — validator, compiler, interpreter, stdlib, gas table
	•	Core: core/ — canonical CBOR, types, DBs, block import
	•	Consensus: consensus/ — PoIES scorer, Θ retarget, fork choice
	•	Specs: execution/specs/* and spec/* — schemas & rules

