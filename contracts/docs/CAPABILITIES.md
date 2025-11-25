# Contract Capabilities Guide
**AI / Quantum / Data Availability (DA) / Randomness** — how to use off-chain and system capabilities from Animica smart contracts running on the deterministic Python-VM.

This guide explains:
- The **deterministic model** for capabilities (enqueue now, consume next block).
- Canonical **APIs, events, and storage** patterns for AI, Quantum, DA, and Randomness.
- **Gas/fee** considerations and **security** checklists.
- End-to-end **composition** examples (e.g., AI→DA oracle, Quantum RNG mixing with the beacon).

> All examples here target the Python-VM stdlib modules under `contracts/stdlib/capabilities/*`. Method names are stable and thin wrappers around the host-side providers in `capabilities/host/*` and the queue/result plumbing in `capabilities/jobs/*`.

---

## 0) Determinism Model (Must Read)

**Key rule**: Capability results are **not** returned synchronously to state-changing calls. Contracts **enqueue** work in block *H* and **consume** the result (if available) starting in block *H+1*. This gives:
- Single-source-of-truth for ordering (blockchain height),
- Replay-safe transcripts,
- Stable gas and resource accounting,
- Room for attestations/proofs to arrive on-chain.

### 0.1 Task ID
Every enqueue returns a **deterministic `task_id`**:

task_id = H(chainId | height | txHash | caller | payload)

(Implemented in `capabilities/jobs/id.py`; exact domain tags in `capabilities/schemas/*`.)

### 0.2 Result Read
- `read_*_result(task_id)` returns **(found: bool, data: bytes)** or reverts with standardized errors:
  - `NoResultYet` → not available at current height.
  - `AttestationError` → result failed verification (evidence rejected).
  - `LimitExceeded` → input/result too large (cap enforced).
- **Never** mutate state based on a result in the same block it was enqueued.

### 0.3 Gas & Fees
- **Intrinsic gas** for ABI + call frame,
- **Capability gas** to cover serialization and bounding,
- **Units-based fees** handled by the AICF/host bridges (off-chain economics):
  - AI units = prompt size × model coefficient (+ QoS),
  - Quantum units = circuit depth×width×shots (+ traps),
  - DA costs = bytes pinned, namespace pricing,
  - Randomness commit/reveal have minimal intrinsic costs; VDF handled by miners/participants.
- Contracts should **bound** inputs (length caps) *before* enqueue.

---

## 1) AI Compute

Invoke off-chain AI models with deterministic lifecycle and attestable outputs.

### 1.1 API (stdlib)
```python
from stdlib.capabilities.ai_compute import (
    ai_enqueue,          # (model: bytes, prompt: bytes) -> bytes task_id
    ai_result_available, # (task_id: bytes) -> bool
    ai_read_result,      # (task_id: bytes) -> (bool found, bytes result)
)

	•	model is a short identifier (e.g., b"animica/text-1"). Contracts should enforce an allowlist.
	•	prompt is bounded by caps (see capabilities/runtime/determinism.py).

1.2 Events

Emit events so UIs/indexers can follow the flow:
	•	AIJobEnqueued(task_id, model, prompt_hash)
	•	AIJobConsumed(task_id, bytes32 result_hash)

from stdlib.hash import sha3_256
from stdlib.events import emit

def _ev_enqueued(task_id, model, prompt):
    emit(b"AIJobEnqueued", {
        "task_id": task_id,
        "model": model,
        "prompt_hash": sha3_256(prompt)
    })

def _ev_consumed(task_id, result):
    emit(b"AIJobConsumed", {
        "task_id": task_id,
        "result_hash": sha3_256(result)
    })

1.3 Minimal Contract Pattern

from stdlib.abi import require
from stdlib.storage import get, set
from stdlib.capabilities.ai_compute import ai_enqueue, ai_read_result, ai_result_available

_K_LAST_TASK = b"ai:last_task"
_K_LAST_RES  = b"ai:last_result"

ALLOWED_MODELS = { b"animica/text-1", b"animica/vision-1" }

def request_ai(model: bytes, prompt: bytes) -> bytes:
    require(model in ALLOWED_MODELS, "model_not_allowed")
    require(0 < len(prompt) <= 4096, "bad_prompt_len")
    task_id = ai_enqueue(model, prompt)
    set(_K_LAST_TASK, task_id)
    _ev_enqueued(task_id, model, prompt)
    return task_id

def consume_ai(task_id: bytes) -> bytes:
    found, result = ai_read_result(task_id)
    require(found, "NoResultYet")
    set(_K_LAST_RES, result)
    _ev_consumed(task_id, result)
    return result

1.4 Attestation & Security Notes
	•	Results are coupled to proofs (proofs/ai.py), including TEE attestation, traps receipts, and QoS. The host rejects unverified outputs and surfaces AttestationError.
	•	Contracts should not parse untrusted large JSON in-state; store a hash or a DA commitment (see §3).

⸻

2) Quantum Compute (with Traps)

Submit quantum circuits and verify providers via trap-circuit evidence.

2.1 API (stdlib)

from stdlib.capabilities.quantum import (
    q_enqueue,             # (circuit_json: bytes, shots: int, traps: int) -> bytes task_id
    q_result_available,    # (task_id: bytes) -> bool
    q_read_result,         # (task_id: bytes) -> (bool found, bytes result_json)
)

Contracts must bound:
	•	len(circuit_json) (e.g., ≤ 8 KiB),
	•	1 ≤ shots ≤ 8192,
	•	0 ≤ traps ≤ min(shots, 1024).

2.2 Events
	•	QJobEnqueued(task_id, shots, traps, circuit_hash)
	•	QJobConsumed(task_id, result_hash, traps_ratio)

from stdlib.hash import sha3_256
from stdlib.events import emit

def _q_ev_enq(task_id, circuit_json, shots, traps):
    emit(b"QJobEnqueued", {
        "task_id": task_id,
        "shots": shots,
        "traps": traps,
        "circuit_hash": sha3_256(circuit_json),
    })

def _q_ev_consume(task_id, result_json, traps_ratio: int):
    emit(b"QJobConsumed", {
        "task_id": task_id,
        "result_hash": sha3_256(result_json),
        "traps_ratio_ppm": traps_ratio,  # parts-per-million fixed-point
    })

2.3 Consumption

The host maps provider evidence to metrics (proofs/quantum.py). If traps fail thresholds (per policy), AttestationError is raised.

⸻

3) Data Availability (DA)

Pin binary blobs, obtain a namespaced Merkle commitment (NMT root), and reference it on-chain.

3.1 API (stdlib)

from stdlib.capabilities.da_blob import (
    da_pin_blob,     # (namespace: int, data: bytes) -> bytes32 commitment
    da_max_bytes,    # () -> int (cap)
)

3.2 Pattern: Oracle Update

Store only the commitment on-chain; clients fetch via DA APIs and verify with light proofs.

from stdlib.abi import require
from stdlib.storage import set
from stdlib.events import emit

_K_LAST_COMMIT = b"da:last_commit"
ORACLE_NS = 24  # example application namespace

def submit_oracle_blob(data: bytes) -> bytes:
    require(0 < len(data) <= da_max_bytes(), "bad_size")
    commit = da_pin_blob(ORACLE_NS, data)
    set(_K_LAST_COMMIT, commit)
    emit(b"OracleBlobCommitted", { "ns": ORACLE_NS, "commitment": commit })
    return commit

Notes
	•	The DA commitment is linked into block headers (DA root). Light clients verify inclusion via da/sampling/light_client.py & SDK helpers.
	•	Use namespaces to segment application data.

⸻

4) Randomness (Beacon + Commit/Reveal)

Read the beacon output, and optionally participate with commit/reveal.

4.1 API (stdlib)

from stdlib.capabilities.randomness import (
    rand_get_beacon,    # () -> bytes32 beacon
    rand_commit,        # (salt: bytes32, payload: bytes) -> bytes32 commit_id
    rand_reveal,        # (salt: bytes32, payload: bytes) -> None
    rand_round_info,    # () -> (round_id: int, open: bool, reveal_open: bool)
)

	•	The beacon advances per randomness/beacon/* with a VDF proof verified by consensus/miners.
	•	commit then reveal in the appropriate windows (see randomness/commit_reveal/*).

4.2 Example: Lottery Mix

from stdlib.abi import require
from stdlib.storage import get, set
from stdlib.events import emit
from stdlib.hash import sha3_256
from stdlib.capabilities.randomness import rand_get_beacon, rand_commit, rand_reveal, rand_round_info

_K_LAST_COMMIT = b"lottery:last_commit"
_K_LAST_SEED   = b"lottery:last_seed"

def open_round(user_payload: bytes, salt32: bytes) -> bytes:
    require(len(salt32) == 32, "bad_salt")
    rid, open, _ = rand_round_info()
    require(open, "round_closed")
    commit_id = rand_commit(salt32, user_payload)
    set(_K_LAST_COMMIT, commit_id)
    emit(b"LotteryCommitted", {"round": rid, "commit_id": commit_id})
    return commit_id

def finalize_round(user_payload: bytes, salt32: bytes) -> bytes:
    rid, _, rev = rand_round_info()
    require(rev, "reveal_closed")
    rand_reveal(salt32, user_payload)
    # Mix with beacon for final seed (extract-then-xor style off-chain; on-chain we store hash)
    seed = sha3_256(rand_get_beacon() + user_payload)
    set(_K_LAST_SEED, seed)
    emit(b"LotterySeed", {"round": rid, "seed_hash": seed})
    return seed


⸻

5) Composition Patterns

5.1 AI → DA Oracle
	1.	AIJobEnqueued (prompt → result),
	2.	In H+1, AIJobConsumed and da_pin_blob(result),
	3.	Emit OracleBlobCommitted(commitment).

Benefits:
	•	On-chain state small: store only hash/commitment.
	•	Users verify data via DA light proofs.

5.2 Quantum RNG → Beacon Mix
	1.	q_enqueue with a noise circuit; consume in H+1,
	2.	Combine q_result_json.bytes (or its digest) with rand_get_beacon() → derive app seed,
	3.	Store seed hash and expose a view.

5.3 Guarded Workflows

Combine RBAC and pausability (see contracts/docs/PATTERNS.md):
	•	ROLE_COMPUTE to enqueue AI/Quantum work,
	•	Pause all enqueue paths in incidents,
	•	Timelock gated upgrades that change allowed models/circuits.

⸻

6) ABI Guidance

Expose slim ABIs. Prefer hashes/commitments rather than raw large outputs.

{
  "name": "AIAgent",
  "functions": [
    {"name": "request_ai", "inputs":[{"name":"model","type":"bytes"},{"name":"prompt","type":"bytes"}], "outputs":[{"type":"bytes"}]},
    {"name": "consume_ai", "inputs":[{"name":"task_id","type":"bytes"}], "outputs":[{"type":"bytes"}]},
    {"name": "submit_oracle_blob", "inputs":[{"name":"data","type":"bytes"}], "outputs":[{"type":"bytes32"}]},
    {"name": "open_round", "inputs":[{"name":"payload","type":"bytes"},{"name":"salt32","type":"bytes"}], "outputs":[{"type":"bytes"}]},
    {"name": "finalize_round", "inputs":[{"name":"payload","type":"bytes"},{"name":"salt32","type":"bytes"}], "outputs":[{"type":"bytes"}]}
  ],
  "events": [
    {"name":"AIJobEnqueued","fields":[{"name":"task_id","type":"bytes"},{"name":"model","type":"bytes"},{"name":"prompt_hash","type":"bytes32"}]},
    {"name":"AIJobConsumed","fields":[{"name":"task_id","type":"bytes"},{"name":"result_hash","type":"bytes32"}]},
    {"name":"OracleBlobCommitted","fields":[{"name":"ns","type":"u32"},{"name":"commitment","type":"bytes32"}]},
    {"name":"LotteryCommitted","fields":[{"name":"round","type":"u64"},{"name":"commit_id","type":"bytes"}]},
    {"name":"LotterySeed","fields":[{"name":"round","type":"u64"},{"name":"seed_hash","type":"bytes32"}]}
  ]
}


⸻

7) Security & Determinism Checklists

7.1 AI / Quantum
	•	Enforce length caps on prompt / circuit_json.
	•	Enforce allowlists for models/circuit types.
	•	Treat returned data as untrusted → store hash or DA commitment, not raw blobs.
	•	Handle NoResultYet and AttestationError explicitly.
	•	Emit events with hashes, not full contents.

7.2 DA
	•	Validate namespace and max bytes before pinning.
	•	Store commitment; expose getters; no need to store the whole blob.
	•	Consider retention and pinning policy off-chain.

7.3 Randomness
	•	Use current beacon only; avoid caching across long windows unless documented.
	•	Respect commit & reveal window timings via rand_round_info.
	•	When mixing with external bytes (e.g., quantum), publish only hash.

7.4 General
	•	All capability calls protected by pause and role gates where appropriate.
	•	All variable-sized inputs checked against caps.
	•	No same-block dependence between enqueue and consume.

⸻

8) Testing Patterns
	•	Unit / Local VM:
	•	Mock result availability by injecting fixtures (see capabilities/tests/test_enqueue_*).
	•	Verify events and storage updates (hashes/commitments only).
	•	Integration (devnet):
	•	Run tests/integration/test_capabilities_ai_flow.py and ...quantum_flow.py.
	•	DA round-trip: test_da_post_and_verify.py.
	•	Randomness round lifecycle: test_randomness_beacon_round.py.
	•	Property / Fuzz:
	•	Bound input generators; ensure enqueue→consume monotonic over height.
	•	Fuzz DA proofs & randomness timing windows for negative paths.

⸻

9) Reference Map
	•	Contracts stdlib: contracts/stdlib/capabilities/{ai_compute,quantum,da_blob,randomness}.py
	•	Host providers: capabilities/host/{compute,blob,random,zk}.py
	•	Jobs & results: capabilities/jobs/{id,queue,result_store,resolver}.py
	•	Proof adapters: proofs/{ai,quantum}.py and capabilities/jobs/attest_bridge.py
	•	DA: da/* (NMT, erasure, sampling, light client)
	•	Randomness: randomness/* (commit/reveal, VDF, beacon)
	•	Specs: capabilities/specs/*, da/specs/*, randomness/specs/*
	•	SDK helpers: sdk/python/omni_sdk/{da,aicf,randomness}/*

⸻

10) Full Example: AI → DA Oracle (End-to-End)

from stdlib.abi import require
from stdlib.storage import get, set
from stdlib.events import emit
from stdlib.hash import sha3_256
from stdlib.capabilities.ai_compute import ai_enqueue, ai_read_result
from stdlib.capabilities.da_blob import da_pin_blob, da_max_bytes
from stdlib.capabilities.randomness import rand_get_beacon

_K_AI_TASK     = b"oracle:ai_task"
_K_DA_COMMIT   = b"oracle:commitment"
NS_ORACLE      = 24
ALLOWED_MODELS = { b"animica/text-1" }

def oracle_request(model: bytes, prompt: bytes) -> bytes:
    require(model in ALLOWED_MODELS, "model_not_allowed")
    require(0 < len(prompt) <= 4096, "bad_prompt_len")
    tid = ai_enqueue(model, prompt)
    set(_K_AI_TASK, tid)
    emit(b"OracleRequested", {"task_id": tid, "model": model, "prompt_hash": sha3_256(prompt)})
    return tid

def oracle_publish() -> bytes:
    tid = get(_K_AI_TASK)
    require(tid and len(tid) > 0, "no_task")
    found, result = ai_read_result(tid)
    require(found, "NoResultYet")
    # Optional: mix with beacon for an app-specific digest
    _ = rand_get_beacon()
    # Pin to DA
    require(len(result) <= da_max_bytes(), "result_too_large")
    commit = da_pin_blob(NS_ORACLE, result)
    set(_K_DA_COMMIT, commit)
    emit(b"OracleBlobCommitted", {"task_id": tid, "commitment": commit})
    return commit

This contract:
	•	Requests AI output,
	•	Publishes the result to DA in the next block,
	•	Exposes an on-chain commitment that off-chain clients can verify with DA light proofs.

⸻

Design for clarity. Enqueue with strict bounds, consume one block later, emit hashes/commitments, and lean on DA/Randomness and AICF proofs for verifiable, deterministic effects.
