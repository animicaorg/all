# AI Agent (example)

A minimal, audit-friendly contract that demonstrates **on-chain → off-chain AI compute**
via Animica’s capabilities layer and the **AICF** (AI Compute Fund). It shows how a
contract can:

- deterministically **enqueue** an AI job (model + prompt) in transaction *T*,
- obtain a canonical **task_id = H(chainId‖height‖txHash‖caller‖payload)**,
- and **consume the result next block** (transaction *T+Δ*, ≥ 1 block later) in a way
  that is deterministic and fully verifiable once a provider’s proof lands on-chain.

This example is intentionally small and strongly typed so you can use it as a scaffold
for agent-style contracts (summarizers, classifiers, prompt routers, etc.).

---

## What you get

- `contract.py` — a compact contract that:
  - exposes `request(model: bytes, prompt: bytes) -> bytes` which enqueues an AI job and
    **returns the deterministic `task_id`**,
  - exposes `read(task_id: bytes) -> (bool, bytes)` which **reads the result next block**
    (returns `(ok, output)`; `ok=False` if not available yet),
  - emits canonical events (`JobRequested`, `JobResult`) to aid indexers/UI.
- `manifest.json` — ABI & metadata compatible with:
  - `vm_py` runtime (local simulation),
  - SDKs (Python/TS/Rust) for deploy & calls,
  - studio-web (browser IDE) & studio-services (verification).

> This example uses the stdlib wrapper `contracts/stdlib/capabilities/ai_compute.py`
> under the hood, which enforces input caps, encodes the job payload, and guards the
> **next-block consumption rule** for determinism.

---

## Determinism & lifecycle

**Block N (enqueue):**
1. `request(model, prompt)`:
   - Normalizes inputs (length caps, UTF-8 OK), computes an envelope,
   - Calls `ai_enqueue(model, prompt)` (host syscall),
   - Receives a **receipt** containing deterministic `task_id`,
   - Stores `task_id` if you choose, and emits `JobRequested(task_id, model_tag, prompt_hash)`.

**Between N and N+1:**
- Off-chain: AICF assigns the job to an eligible provider.
- Provider computes result, returns an output digest and a verifiable **AIProof**.
- Miners/validators include the proof in a block if valid.

**Block N+1+ (read):**
1. `read(task_id)`:
   - Calls `read_result(task_id)` (host syscall),
   - If the corresponding proof was accepted in a prior block, returns `(True, output)`,
   - Otherwise `(False, b"")`.
   - Emits `JobResult(task_id, ok, output_hash, units_used)` if `ok=True`.

**Why “next block”?**  
To keep execution deterministic, contracts must not depend on *intra-block* off-chain
effects. The capabilities runtime enforces that results are **only consumable starting
the next block** after enqueue, so every node observes the same state transitions.

---

## Prerequisites

- A local devnet or testnet with:
  - **Node RPC** running,
  - **Capabilities** module enabled,
  - **AICF** queue & at least one provider (for real results; otherwise the studio
    simulator can be used for local runs).
- Tooling:
  - `vm_py` (for compile/sim),
  - **SDK** of your choice (Python/TS/Rust) for deploy & calls, or studio-web.

Helpful references already in this repo:
- Python SDK quickstart: `sdk/python/examples/ai_enqueue_then_consume.py`
- Studio web template: `studio-web/src/fixtures/templates/ai_agent/*`
- Capabilities docs: `capabilities/specs/COMPUTE.md`, `capabilities/specs/SYSCALLS.md`

---

## ABI (high level)

```json
{
  "name": "AiAgent",
  "functions": [
    {
      "name": "request",
      "inputs": [
        { "name": "model",  "type": "bytes" },
        { "name": "prompt", "type": "bytes" }
      ],
      "outputs": [{ "name": "task_id", "type": "bytes" }],
      "stateMutability": "write"
    },
    {
      "name": "read",
      "inputs": [{ "name": "task_id", "type": "bytes" }],
      "outputs": [
        { "name": "ok",     "type": "bool"  },
        { "name": "output", "type": "bytes" }
      ],
      "stateMutability": "write"
    }
  ],
  "events": [
    {
      "name": "JobRequested",
      "inputs": [
        { "name": "task_id",    "type": "bytes", "indexed": true },
        { "name": "model_tag",  "type": "bytes", "indexed": false },
        { "name": "prompt_hash","type": "bytes", "indexed": false }
      ]
    },
    {
      "name": "JobResult",
      "inputs": [
        { "name": "task_id",    "type": "bytes", "indexed": true },
        { "name": "ok",         "type": "bool",  "indexed": false },
        { "name": "output_hash","type": "bytes", "indexed": false },
        { "name": "units_used", "type": "uint" , "indexed": false }
      ]
    }
  ]
}

Notes:
	•	model can be a short tag (e.g. b"gemma-2b-instruct") or a catalog id.
	•	prompt is raw bytes to keep ABI simple (UTF-8 strings work fine).
	•	output is the provider’s result bytes. For large text, you may wish to store a hash
on-chain and pin the full artifact via DA (see da_blob capability).

⸻

Build & simulate locally

You can build a deployable package (manifest + code) and simulate calls entirely in
Python (no node):

# From repo root
python -m contracts.tools.build_package \
  --source contracts/examples/ai_agent/contract.py \
  --manifest contracts/examples/ai_agent/manifest.json \
  --out-dir contracts/build

# Optional: simulate with studio-wasm (browser) via studio-web
pnpm -C studio-web dev  # then open the AI Agent template and run "Simulate"


⸻

Deploy & interact (Python SDK)

The simplest end-to-end path is to reuse the SDK example:

# 1) Deploy with Python SDK
python sdk/python/examples/ai_enqueue_then_consume.py \
  --manifest contracts/examples/ai_agent/manifest.json \
  --source   contracts/examples/ai_agent/contract.py \
  --rpc      ${RPC_URL:-http://127.0.0.1:8545} \
  --chain    ${CHAIN_ID:-1337} \
  --mnemonic "$DEPLOYER_MNEMONIC"

That script will:
	1.	Compile & deploy the contract, printing the deployed address,
	2.	Call request(model, prompt) and print the task_id,
	3.	Wait until the next block and then call read(task_id) to fetch the result,
	4.	Print a concise JSON transcript.

If you prefer ad-hoc calls, the Python SDK offers a generic ContractClient:

from omni_sdk.contracts.client import ContractClient
from omni_sdk.rpc.http import HttpClient

rpc  = HttpClient(rpc_url="http://127.0.0.1:8545", chain_id=1337)
abi  = json.load(open("contracts/examples/ai_agent/manifest.json"))["abi"]
addr = "<deployed-address>"

client = ContractClient(rpc_url=rpc.rpc_url, chain_id=rpc.chain_id, address=addr, abi=abi, signer=<your_signer>)

task_id = client.write("request", b"gemma-2b-instruct", b"Write a haiku about Animica.")  # returns bytes

ok, output = client.write("read", task_id)
print(ok, output.decode("utf-8", errors="ignore"))


⸻

Gas & fees (mental model)
	•	On-chain gas: request and read each cost normal VM gas (ABI decode,
storage/events) + capability intrinsic charges (bounded & deterministic).
	•	Off-chain reward: The AICF prices jobs in “units” based on the provider’s
attested work (model/time/params) and splits rewards per policy (AICF/ECONOMICS.md).
	•	Who pays? This example assumes the caller pays on-chain gas; off-chain units and
splits are subsidized by the AICF (configurable). For “bill the requester” patterns,
see the treasury hooks and escrow examples.

⸻

Security & invariants
	•	Next-block rule: Never treat read_result(task_id) as available in the same block
as request. The stdlib wrapper enforces this; do not bypass it.
	•	Input caps: model and prompt lengths are capped to keep block size bounded.
Large artifacts should be pinned via DA (da_blob capability) and referenced by hash.
	•	Auditability: Store task_id on-chain and emit events. Off-chain systems can join
with AICF job/receipt logs to produce fully verifiable traces.
	•	Replays: task_id derivation binds to (chainId, height, txHash, caller, payload).
Reusing the same input in a new block yields a different task_id, avoiding ambiguous
reads.

⸻

Troubleshooting
	•	NoResultYet on read(task_id): Ensure at least one block has passed since the
enqueue tx was included. If still false after several blocks, confirm a provider is
live and proofs are being accepted (see AICF dashboards or explorer-web).
	•	LimitExceeded: Input too large. Shorten prompt or store the full prompt in DA
and pass its commitment instead.
	•	AttestationError (in provider logs): The provider’s TEE/QoS proof failed policy
checks; the result will not be consumable.

⸻

Extending this example

Common evolutions:
	•	Routing: Add a small on-chain router that transforms user input into a specific
model catalog id and prompt template (with bounded parameters).
	•	Chaining: Issue multiple requests with different prompts and aggregate the
results on-chain (e.g., majority vote) using DA for large intermediates.
	•	Payment gating: Integrate with the escrow splitter to require deposits before
allowing requests.

⸻

Related templates & tests
	•	Studio IDE template: studio-web/src/fixtures/templates/ai_agent/*
	•	End-to-end SDK demo: sdk/python/examples/ai_enqueue_then_consume.py
	•	Capabilities test vectors: capabilities/test_vectors/enqueue_and_read.json

⸻

Quick checklist (you’re ready when)
	•	request returns a task_id and emits JobRequested
	•	After ≥1 block, read(task_id) returns (True, output) and emits JobResult
	•	Explorer shows the proof in the block that made the result available
	•	AICF dashboard shows units accounted and (optional) payout accrued

