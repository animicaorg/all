# capabilities/ — Deterministic off-chain “syscalls” for contracts

This package provides the **deterministic capability layer** that lets Animica smart contracts
(requested by the deterministic Python VM) interact with off-chain functionality **without breaking consensus**.

It does this by exposing a small set of *host-provided syscalls* (blob pinning, AI/Quantum job enqueue,
reading results from the previous block, zk.verify, deterministic randomness mixing, and treasury hooks),
and by enforcing strict **determinism, sizing, and attestation rules** so every honest node makes the
same state transition.

---

## What lives here

- `capabilities/host/*` — syscall provider registry + concrete host implementations (blob, compute, zk, random, treasury).
- `capabilities/jobs/*` — deterministic job IDs, persistent queue, receipts, result store, indexes, resolver.
- `capabilities/adapters/*` — bridges to **DA**, **AICF**, **zk**, **randomness** and **execution** subsystems.
- `capabilities/runtime/*` — bindings used by `vm_py` to call into the host layer safely.
- `capabilities/schemas/*` — JSON-Schema & CDDL for syscall ABIs, job requests, receipts, results, zk.verify.
- `capabilities/rpc/*` — optional read-only APIs and WS events to observe jobs/results (no signing on server).
- `capabilities/cli/*` — developer helpers to enqueue/list/inject results during local/devnet runs.
- Tests, benches, and type markers.

Closely related modules:
- **vm_py**: user contracts import `from stdlib import syscalls` which binds into `capabilities/runtime`.
- **da**: blob pin/get and proof surfaces used by `host/blob.py` and adapters.
- **aicf**: assignment, attestation normalization, SLA/pricing for AI/Quantum jobs.
- **proofs**: evidence/attestation verifiers whose outputs are consumed by the resolver.
- **execution**: treasury integration and result consumption within block application.

---

## Determinism model

**Goal:** Every node that executes a block must produce the same state transitions even if off-chain
compute happens elsewhere.

We achieve this via:

1. **Enqueue-then-consume (N→N+1):**  
   - A contract may *enqueue* a job during block **N** via `ai_enqueue(…)` or `quantum_enqueue(…)`.  
   - The job’s deterministic **`task_id`** is derived purely from consensus inputs:  
     `task_id = H(domain | chainId | height | txHash | caller | payload)` (SHA3-256).  
   - The contract may **read** the result starting in block **N+1** via `read_result(task_id)`.
   - Nodes reject attempts to read results earlier than N+1 (`NoResultYet`).

2. **Write-once receipts & proofs:**  
   Off-chain providers return evidence; the **resolver** validates evidence → emits normalized records
   into the result store (or maps evidence to on-chain **Proof*** envelopes included by miners). All nodes
   ingest the same finalized record keyed by `task_id`.

3. **Strict sizing & caps:**  
   Inputs and outputs have byte caps; jobs have timeout/TTL. Oversize or late results are ignored. All caps
   are part of chain params/policy.

4. **Pure hashing & domain separation:**  
   Every syscall uses explicit domains/personalization strings; all IDs, receipts, and proofs are hashed
   deterministically.

5. **Gas & units accounting:**  
   - Enqueue charges base gas + per-byte input.  
   - `zk.verify` charges by verified units.  
   - Reading a result charges per-byte output.  
   Gas tables live under `execution/` and `vm_py/gas_table.json`; hooks are in `capabilities/host/*`.

6. **No nondeterministic I/O from contracts:**  
   Contracts cannot perform network/file/time I/O. All external effects flow through these syscalls only.

---

## Contract-facing API (via `stdlib.syscalls`)

From a contract, import the safe façade:

```python
from stdlib import syscalls

# 1) Pin a blob into DA (returns commitment/NMT root)
commitment = syscalls.blob_pin(ns=24, data=b"...bytes...")

# 2) Enqueue AI job (returns deterministic task_id)
task_id = syscalls.ai_enqueue(model=b"tiny-demo", prompt=b"count primes up to 100")

# 3) Enqueue Quantum trap-circuit job
q_task = syscalls.quantum_enqueue(circuit=b'{"depth":8,"width":4}', shots=256)

# 4) Read result (allowed starting next block)
status, output = syscalls.read_result(task_id)
if status == b"OK":
    # use 'output' bytes deterministically
    pass

# 5) Verify a zk proof (pure, no job queue)
ok, units = syscalls.zk_verify(circuit=b"...", proof=b"...", public=b"...")

# 6) Deterministic random bytes (stub mixes in beacon when available)
r = syscalls.random(32)

Returned tuples and byte payloads are canonical and size-capped. Errors raise VmError subclasses
mapped from CapError kinds: NotDeterministic, LimitExceeded, NoResultYet, AttestationError.

⸻

Syscall catalog
	•	blob_pin(ns: u32, data: bytes) -> commitment: bytes32
Validates namespace and size; streams into the DA adapter; returns content commitment (NMT root).
	•	ai_enqueue(model: bytes, prompt: bytes, opts?: bytes) -> task_id: bytes32
Deterministic enqueue of an AI job. opts is an opaque CBOR/JSON blob (model-specific but size-capped).
	•	quantum_enqueue(circuit: bytes, shots: u32, opts?: bytes) -> task_id: bytes32
Deterministic enqueue of a Quantum job. Circuit format is provider-agnostic JSON/CBOR (normalized).
	•	read_result(task_id: bytes32) -> (status: bytes3, output: bytes)
status is one of b"OK", b"ERR", b"TTL". output is a byte string (possibly empty).
Enforced N→N+1 rule; results are prunable after a retention window.
	•	zk_verify(circuit: bytes, proof: bytes, public: bytes) -> (ok: bool, units: u32)
Pure verifier; returns success and costed “units” used for gas/metrics. No queue.
	•	random(n: u32) -> bytes
Deterministic PRNG seeded from tx hash; optionally mixes entropy from the beacon when present,
with a transcript that remains deterministic across nodes.

All bytes are length-prefixed internally and validated against capabilities/config.py limits.

⸻

Result lifecycle
	1.	Enqueue: Contract calls *_enqueue; the host writes a JobRequest (CBOR per schemas/job_request.cddl)
with deterministic task_id and a JobReceipt (per schemas/job_receipt.cddl).
	2.	Assignment & compute (off-chain): AICF picks a provider, runs the task, prepares an output digest and
attestation bundle (TEE/quantum/storage as applicable).
	3.	Proof/attestation ingestion:
	•	If miners include a Proof* envelope, the resolver maps it to the queued job via (task_id|nullifier)
and writes a ResultRecord (per schemas/result_record.cddl).
	•	Otherwise (devnet tools), a trusted operator may inject a matching result for testing only.
	4.	Consumption: Starting block N+1, read_result(task_id) returns the normalized record. Results are
available to contracts and also mirrored in read-only RPC for UIs.
	5.	GC: After a retention window, results are pruned; artifacts may persist in DA/S3 if pinned.

⸻

Host architecture
	•	Provider registry (host/provider.py): Binds syscall names to implementations; enforces size/gas caps.
	•	Blob (host/blob.py): Bridges to da/ for pin/get; returns commitments with namespace validation.
	•	Compute (host/compute.py): Deterministic enqueue for AI/Quantum; no direct side-effects to VM.
	•	Result read (host/result_read.py): Constant-time map from task_id → ResultRecord, with N→N+1 checks.
	•	ZK (host/zk.py): Local or adapter-backed verifiers returning (ok, units).
	•	Random (host/random.py): Deterministic PRNG; optional beacon mixing via randomness/ adapter.
	•	Treasury (host/treasury.py): Hooks for fee debit/credit when capabilities imply ledger movements.
	•	Jobs core:
	•	jobs/id.py — derives task_id with domain separation and SHA3-256.
	•	jobs/queue.py — persistent queue (SQLite/Rocks) + in-mem fast path.
	•	jobs/receipts.py, jobs/result_store.py, jobs/index.py, jobs/resolver.py — normalize evidence → results.
	•	Adapters:
	•	adapters/da.py — DA post/get/proof.
	•	adapters/aicf.py — enqueue → AICF queue; pull completions; ID compatibility.
	•	adapters/zk.py — zk verifier integration.
	•	adapters/randomness.py — beacon read/mix.
	•	adapters/execution_state.py — safe read/write to execution DB when needed.
	•	adapters/proofs.py — map proofs/ envelopes → ResultRecord.
	•	Observation APIs (optional):
	•	rpc/methods.py and rpc/ws.py expose read-only endpoints and events: cap.getJob, cap.listJobs,
cap.getResult, jobCompleted WS broadcast. No signing or secrets server-side.
	•	Metrics: Prometheus counters/histograms in capabilities/metrics.py.

⸻

Configuration

See capabilities/config.py for defaults; typical knobs:
	•	Feature flags: enable/disable individual syscalls (enable_ai, enable_quantum, enable_blob, enable_zk).
	•	Sizing: max_input_bytes, max_output_bytes, max_queue_depth.
	•	Timing: result_ttl_blocks, enqueue_fee_base, per_byte_cost.
	•	Security: allowed namespaces, model/circuit allowlists, attestation requirements, provider quotas.
	•	Storage: SQLite/Rocks DB URIs for queue/result stores; optional S3 for artifacts.
	•	RPC/WS: toggle read-only cap.* routes.

Configuration is loaded alongside node config and may be surfaced through RPC capabilities.getParams (read-only).

⸻

CLI quickstart (devnet)

Enqueue a tiny AI job and read it next block:

# Enqueue (returns task_id)
python -m capabilities.cli.enqueue_ai --model demo --prompt "count to 5"

# List queued jobs
python -m capabilities.cli.list_jobs

# (devnet only) Inject a matching result to simulate completion
python -m capabilities.cli.inject_result --task <TASK_ID> --status OK --output 68656c6c6f

# Query result via read-only RPC (if mounted)
curl -s localhost:8545/cap/result/<TASK_ID> | jq

See also:
	•	cli/enqueue_quantum.py — submit a small trap-circuit.
	•	cli/list_jobs.py — filter by status/caller.
	•	cli/inject_result.py — test-only path for local loops.

⸻

Validation & schemas
	•	ABI surface: schemas/syscalls_abi.schema.json
	•	Job request/receipt/result: schemas/job_request.cddl, schemas/job_receipt.cddl, schemas/result_record.cddl
	•	ZK verify: schemas/zk_verify.schema.json

All wire objects are canonical CBOR/JSON with deterministic ordering.

⸻

Testing

Pytests cover:
	•	Deterministic task IDs across nodes & heights.
	•	Enqueue → resolver → read_result (AI/Quantum).
	•	Blob pin/get round-trips via DA adapter.
	•	ZK verify pass/fail paths and unit accounting.
	•	Input/output size caps and provider limits.
	•	Read-only RPC mount.

Run: pytest -q capabilities/

⸻

Security notes
	•	No server-side signing: All signing remains client-side (wallet or SDK). Capability endpoints are read-only.
	•	Attestation required: AI/Quantum results must include evidence meeting policy; invalid evidence → AttestationError.
	•	Replay & nullifiers: Results are bound to task_id and windowed; replays are rejected; DA pins use content addressing.
	•	DoS control: Per-caller caps, size caps, TTLs, quotas, and token-bucket rate limits (optional RPC layer).
	•	Transparency: Normalized ResultRecord includes digests and provenance pointers for audits.

⸻

Versioning

This package follows semantic versioning. The current version is exported as capabilities.__version__.

⸻

See also
	•	capabilities/specs/* for normative syscall semantics and gas charging.
	•	aicf/* for provider registry, SLA/pricing, and settlement.
	•	proofs/* for evidence formats and verifiers.
	•	da/* for content addressing, NMT roots, and availability proofs.

