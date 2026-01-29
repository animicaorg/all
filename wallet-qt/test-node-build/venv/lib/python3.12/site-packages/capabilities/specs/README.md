# Capabilities — Specs Index

This directory collects the **normative documentation** for the _capabilities_
subsystem: the deterministic system-call surface that contracts use to request
off-chain work or platform features, and the host-side machinery that executes,
accounts, and exposes results.

The capabilities covered here are:

- **Blob storage**: `blob_pin(ns, data)` → commitment (ties into `da/`)
- **Compute**: `ai_enqueue(model, prompt, …)` and `quantum_enqueue(circuit, …)`
- **Result read**: `read_result(task_id)` (deterministic, next-block visibility)
- **Zero-knowledge**: `zk_verify(circuit, proof, input)` → `bool` (+ metered units)
- **Randomness**: `random(n_bytes)` (deterministic stub; beacon mix when available)
- **Treasury hooks**: metered debit/credit for capability usage

The host-side counterparts live under `capabilities/host/*` and the durable
job/result plumbing under `capabilities/jobs/*`.

---

## Documents in this folder

- **[SYSCALLS.md](SYSCALLS.md)** — Contract-facing ABI, call shapes, return values,
  determinism and **gas charging** rules. This is what VM programs rely on.
- **[COMPUTE.md](COMPUTE.md)** — AI & Quantum flows: enqueue → attest/prove →
  resolve. Normalization to `proofs/` metrics, SLA signals, and safety rails.
- **[TREASURY.md](TREASURY.md)** — How capability usage is priced/charged,
  fee split & escrow interfaces, and settlement linkage to AICF.
- **[SECURITY.md](SECURITY.md)** — Determinism & replay resistance, size/CPU
  caps, input sanitizers, and abuse handling (DoS, griefing).
- **[RESULTS.md](RESULTS.md)** — Result lifecycle & visibility windows, record
  schema, indexing, retention, and pruning.

These five documents are **normative** unless otherwise stated.

---

## Related schemas (normative)

Located in `capabilities/schemas/`:

- `syscalls_abi.schema.json` — JSON schema for the contract-facing syscall shapes.
- `job_request.cddl` — CBOR envelope for enqueue requests (AI/Quantum).
- `job_receipt.cddl` — CBOR receipt returned on enqueue.
- `result_record.cddl` — CBOR record written when results are available.
- `zk_verify.schema.json` — JSON schema for `zk_verify` inputs.

CBOR canonicalization/validation helpers live in `capabilities/cbor/codec.py`
(normative on encoding behavior).

---

## Cross-module references

- **VM ↔ Capabilities**: `vm_py/runtime/syscalls_api.py` calls into
  `capabilities/runtime/abi_bindings.py`, which then dispatches to
  `capabilities/host/*` providers under determinism checks
  (`capabilities/runtime/determinism.py`).
- **Proofs ↔ Results**: Attestations normalized by
  `capabilities/jobs/attest_bridge.py` map to `proofs/*` metrics. Resolution is
  performed by `capabilities/jobs/resolver.py` when blocks are applied.
- **DA integration**: `blob_pin` bridges to `da/` via
  `capabilities/adapters/da.py`; commitments appear in headers through DA roots.
- **AICF linkage**: Host compute adapters enqueue to AICF via
  `capabilities/adapters/aicf.py`; payouts/pricing are specified in AICF docs.
- **Randomness**: Optional beacon mixing is defined in
  `capabilities/adapters/randomness.py` and `randomness/`.

---

## Validation & conformance

1. **Schema checks**
   - JSON: `syscalls_abi.schema.json`, `zk_verify.schema.json`
   - CBOR: `*.cddl` files for job/result envelopes
2. **Codec round-trips**
   - Canonical CBOR via `capabilities/cbor/codec.py`
3. **Test suite (authoritative)**
   - Run module tests:  
     ```bash
     pytest -q capabilities/tests
     ```
   - Key gates include:
     - `test_task_id_determinism.py` — deterministic task IDs
     - `test_enqueue_*_then_consume.py` — enqueue → resolve next block
     - `test_blob_pin.py`, `test_zk_verify.py`, `test_provider_limits.py`
     - `test_result_resolver_from_proofs.py` — proof→result linkage

If behavior diverges from tests, **tests win** (update docs alongside fixes).

---

## Versioning & change process

- Spec-affecting changes MUST:
  1. Update the relevant `*.md` here.
  2. Update schemas (`schemas/*.json|*.cddl`) and the codec if needed.
  3. Bump `capabilities/version.py`.
  4. Add/adjust tests in `capabilities/tests/` and vectors in
     `capabilities/test_vectors/`.
- Backward-incompatible wire changes require a schema/ABI version bump noted in
  **SYSCALLS.md** and in the host/provider negotiation (if applicable).

---

## Glossary

- **Deterministic** — Produces identical results on all nodes given the same
  inputs and chain state; no wall-clock or nondeterministic IO.
- **Receipt** — Proof of accepted enqueue; contains deterministic `task_id`.
- **Result record** — Durable output keyed by `task_id`, visible in/after the
  block it is resolved by.
- **SLA** — Service-level attributes (latency, traps/QoS) computed from proofs.

---

## Directory quick map

capabilities/
runtime/…          # VM bindings & determinism checks
host/…             # Host providers for blob/compute/zk/random/treasury
jobs/…             # Queue, receipts, resolver, result store & indexes
adapters/…         # Bridges to DA, AICF, randomness, execution
schemas/…          # JSON-Schema & CDDL (normative)
cbor/codec.py      # Canonical CBOR codec (normative)
rpc/…              # Optional read-only RPC + WS events
tests/…            # Conformance tests (authoritative)
specs/             # (this folder)

> Questions or proposals? Open a docs issue with: _what changes_, _why_,
> _schema diffs_, and _test plan_. Small PRs that update both spec and tests
> are preferred.

