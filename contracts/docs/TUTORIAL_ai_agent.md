# Tutorial: End-to-End AI Agent Contract (AICF)

This hands-on guide walks you from **zero → deployed AI agent** that:
1) Enqueues an AI job to the **AI Compute Fund (AICF)**,
2) Waits one block (deterministic next-block availability),
3) **Consumes** the result on-chain,
4) Emits useful **events**, stores results for later reads,
5) Is **verifiable** (source ↔ code hash), and safe under deterministic VM rules.

It uses the contract stdlib capability wrapper: `contracts/stdlib/capabilities/ai_compute.py`
and the host/runtime bridge from `capabilities/*`.

---

## 0) Prereqs

- **Python ≥ 3.11**
- A running **devnet/testnet** (see `tests/devnet/` or `ops/docker/docker-compose.devnet.yml`)
- Local env configured:

```bash
cd contracts
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env: RPC_URL, CHAIN_ID, (optional) FAUCET, DEPLOYER_MNEMONIC

The AICF queue/settlement runs in the devnet stack. On public testnets, providers submit proofs and results under SLA rules.

⸻

1) How AICF Works (Quick mental model)

Lifecycle: enqueue → provider executes → proof lands → next block result → (optional) settlement/payout
	•	Your contract calls ai_enqueue(model, prompt), returning a deterministic task_id.
	•	In the next block, the chain’s result resolver populates the result store if the provider produced a valid proof.
	•	Your contract calls ai_read_result(task_id) via the stdlib wrapper to deterministically consume the final bytes/logit-digest (result form depends on model/policy).
	•	Capabilities enforce determinism: inputs are length-capped, results become readable only after inclusion of the proof linkage (i.e., not within the same block).
	•	Costs: gas for enqueue + deterministic “units” priced off-chain by AICF. (Your contract observes results; treasury accounting happens via AICF modules.)

⸻

2) Contract Design

We’ll build a minimal AI Agent:
	•	init(owner): set admin
	•	submit(model, prompt) -> task_id: enqueue AI job; record the task for msg_sender
	•	available(task_id) -> bool: check if the result is readable yet (view)
	•	consume(task_id) -> bytes: read result, store it, emit event
	•	lastTaskOf(user) -> bytes: lookup last task id for a user
	•	getResult(task_id) -> bytes: return stored result (post-consume)
	•	Events: JobEnqueued{user, model, task_id}, JobConsumed{user, task_id, size}

We store:
	•	last_task:<user> → task_id
	•	result:<task_id> → result bytes (or digest)
	•	(Optional) model:<task_id> → model bytes for audit

⸻

3) Write the Contract

Create/inspect a new file alongside examples (we keep examples for reference; this tutorial shows a from-scratch version).

# contracts/examples/ai_agent/contract.py
from stdlib.storage import get, set
from stdlib.abi import require, revert
from stdlib.events import emit
from stdlib.access.ownable import only_owner_init, only_owner
# Capability wrapper (see contracts/stdlib/capabilities/ai_compute.py)
from stdlib.capabilities.ai_compute import ai_enqueue, ai_read_result

_K_OWNER = b"admin:owner"
def _k_last_task(user: bytes) -> bytes: return b"ai:last_task:" + user
def _k_result(task_id: bytes) -> bytes:  return b"ai:result:" + task_id
def _k_model(task_id: bytes) -> bytes:   return b"ai:model:"  + task_id

def init(owner: bytes):
    """
    Set contract owner exactly once (admin for future config hooks).
    """
    only_owner_init(owner)
    emit(b"Initialized", {"owner": owner})

def submit(model: bytes, prompt: bytes) -> bytes:
    """
    Enqueue an AI job with (model, prompt).
    Returns deterministic task_id (bytes).
    Stores last task for sender and emits JobEnqueued.
    """
    sender = msg_sender()
    # Input caps & basic checks (exact caps enforced by host too)
    require(0 < len(model) <= 64, "bad_model")
    require(0 < len(prompt) <= 8192, "prompt_too_big")  # example cap

    task_id = ai_enqueue(model, prompt)  # deterministic: H(chain|height|tx|sender|payload)
    set(_k_last_task(sender), task_id)
    set(_k_model(task_id), model)
    emit(b"JobEnqueued", {"user": sender, "model": model, "task_id": task_id})
    return task_id

def available(task_id: bytes) -> bool:
    """
    View: Returns True iff a result is deterministically readable now.
    """
    res = ai_read_result(task_id)
    return res is not None

def consume(task_id: bytes) -> bytes:
    """
    Read and persist the result once available.
    Emits JobConsumed with size for indexers.
    """
    sender = msg_sender()
    res = ai_read_result(task_id)
    require(res is not None, "not_ready")
    # Persist result for later reads by off-chain clients
    set(_k_result(task_id), res)
    emit(b"JobConsumed", {"user": sender, "task_id": task_id, "size": len(res)})
    return res

def lastTaskOf(user: bytes) -> bytes:
    return get(_k_last_task(user)) or b""

def getResult(task_id: bytes) -> bytes:
    return get(_k_result(task_id)) or b""

# VM-provided at runtime; in tests the harness injects the sender.
def msg_sender() -> bytes:
    return b""

Notes
	•	ai_enqueue returns a task_id immediately; result is unreadable in the same block.
	•	ai_read_result(task_id) returns bytes | None. It flips to bytes after result resolution in the next block.
	•	Persisting results allows cheap re-reads without re-hitting the capability host.

⸻

4) Lint → Compile → Package

# Lint determinism & style
python -m contracts.tools.lint_contract contracts/examples/ai_agent/contract.py

# Create a manifest (or use fixtures as a base)
cat > contracts/examples/ai_agent/manifest.json <<'JSON'
{
  "name": "AIAgent",
  "version": "1.0.0",
  "abi": [
    {"name":"init","inputs":[{"name":"owner","type":"address"}],"outputs":[]},
    {"name":"submit","inputs":[{"name":"model","type":"bytes"},{"name":"prompt","type":"bytes"}],"outputs":[{"type":"bytes"}]},
    {"name":"available","inputs":[{"name":"task_id","type":"bytes"}],"outputs":[{"type":"bool"}]},
    {"name":"consume","inputs":[{"name":"task_id","type":"bytes"}],"outputs":[{"type":"bytes"}]},
    {"name":"lastTaskOf","inputs":[{"name":"user","type":"address"}],"outputs":[{"type":"bytes"}]},
    {"name":"getResult","inputs":[{"name":"task_id","type":"bytes"}],"outputs":[{"type":"bytes"}]}
  ],
  "events": [
    {"name":"Initialized","fields":[{"name":"owner","type":"address"}]},
    {"name":"JobEnqueued","fields":[{"name":"user","type":"address"},{"name":"model","type":"bytes"},{"name":"task_id","type":"bytes"}]},
    {"name":"JobConsumed","fields":[{"name":"user","type":"address"},{"name":"task_id","type":"bytes"},{"name":"size","type":"u32"}]}
  ]
}
JSON

# Build package (IR + code hash + ABI)
python -m contracts.tools.build_package \
  --src contracts/examples/ai_agent/contract.py \
  --manifest contracts/examples/ai_agent/manifest.json \
  --out contracts/build/ai_agent.pkg.json


⸻

5) Local Sanity (VM-only, optional)

You can dry-run the ABI and storage pieces without AICF by stubbing calls,
but real result availability requires the devnet’s capabilities pipeline.
(For end-to-end, use the devnet flow below.)

⸻

6) Deploy to Devnet/Testnet

# (Optional) Faucet for devnet
python -m contracts.tools.deploy \
  --pkg contracts/build/ai_agent.pkg.json \
  --init-args '["<OWNER_ADDR_HEX>"]' \
  --faucet

# Deploy
python -m contracts.tools.deploy \
  --pkg contracts/build/ai_agent.pkg.json \
  --init-args '["<OWNER_ADDR_HEX>"]'
# → prints tx hash, receipt, and deployed address
export AI_AGENT_ADDR=<DEPLOYED_ADDRESS>


⸻

7) Enqueue → Next Block → Consume

7.1 Submit a Job

python -m contracts.tools.call \
  --addr $AI_AGENT_ADDR \
  --abi contracts/examples/ai_agent/manifest.json \
  --fn submit \
  --args '["gpt-mini-1","Write a 5-word slogan about animica."]'
# → returns task_id (hex/bytes). Save it:
export TASK_ID=<RETURNED_TASK_ID_HEX>

Watch node logs or the AICF queue to see the job assignment and proof processing.

7.2 Check availability (same block should be false)

python -m contracts.tools.call \
  --addr $AI_AGENT_ADDR \
  --abi contracts/examples/ai_agent/manifest.json \
  --fn available \
  --args "[\"$TASK_ID\"]"
# → false initially

7.3 Wait for next block (or mine one on devnet)

On devnet you can kick the built-in miner or wait a few seconds.

7.4 Consume the result

python -m contracts.tools.call \
  --addr $AI_AGENT_ADDR \
  --abi contracts/examples/ai_agent/manifest.json \
  --fn consume \
  --args "[\"$TASK_ID\"]"
# → bytes result; also emits JobConsumed(user, task_id, size)

7.5 Read again (from storage)

python -m contracts.tools.call \
  --addr $AI_AGENT_ADDR \
  --abi contracts/examples/ai_agent/manifest.json \
  --fn getResult \
  --args "[\"$TASK_ID\"]"


⸻

8) Using the SDK (Python) for a richer script

# scripts/ai_roundtrip.py
import os, json, time
from omni_sdk.config import Config
from omni_sdk.rpc.http import HttpClient
from omni_sdk.contracts.client import ContractClient

RPC = os.environ["RPC_URL"]
CHAIN_ID = int(os.environ["CHAIN_ID"])
ADDR = os.environ["AI_AGENT_ADDR"]

cfg = Config(rpc_url=RPC, chain_id=CHAIN_ID)
rpc = HttpClient(cfg)
abi = json.load(open("contracts/examples/ai_agent/manifest.json"))
agent = ContractClient(rpc, ADDR, abi["abi"])

# 1) submit
task_id = agent.call("submit", b"gpt-mini-1", b"List 3 values of Animica.")
print("task_id:", task_id.hex())

# 2) poll availability (simple)
for _ in range(30):
    avail = agent.call("available", task_id)
    if avail:
        break
    time.sleep(1)
assert avail, "result not ready in time"

# 3) consume
result = agent.call("consume", task_id)
print("result bytes:", result)


⸻

9) Verify Source (Studio Services)

python -m contracts.tools.verify \
  --addr $AI_AGENT_ADDR \
  --src contracts/examples/ai_agent/contract.py \
  --manifest contracts/examples/ai_agent/manifest.json

This recompiles and ensures the code hash matches on-chain bytecode.

⸻

10) Observability
	•	Events:
	•	JobEnqueued(user, model, task_id)
	•	JobConsumed(user, task_id, size)
	•	Metrics (devnet compose):
	•	aicf_queue_* (enqueue/assign throughput)
	•	aicf_sla_* (traps/QoS/latency)
	•	capabilities_* (enqueue/read hits/misses)
	•	Explorer-lite: check the blocks panel and PoIES breakdown; use the events view to spot your agent events.

⸻

11) Security, Determinism & Costs
	•	Determinism
	•	Inputs are length-capped; reject huge prompts.
	•	No I/O other than the capability syscall; result readable next block only.
	•	task_id is deterministic over (chainId|height|txHash|caller|payload).
	•	Abuse resistance
	•	Consider role/owner gating for submit or add per-user quotas.
	•	Add minimum fee or escrow logic if you’re subsidizing jobs.
	•	Consider DA pinning for large outputs: store the commitment on-chain, keep bytes off-chain.
	•	Storage
	•	Storing raw bytes can be expensive. Prefer compact forms:
	•	Output digest, URI, or DA commitment + NMT root.
	•	Add GC or overwrite policies if outputs are temporary.
	•	SLA & Attestation
	•	Providers are verified (TEE/QPU attestations) and scored under SLA.
	•	Your contract doesn’t parse attestations; it trusts the chain’s mapping to results through proofs.

⸻

12) Troubleshooting
	•	not_ready on consume: A block hasn’t finalized with your result yet; wait/mine one more.
	•	No result ever arrives: Inspect AICF queue logs; the job may have timed out or been re-queued.
	•	prompt_too_big: Respect caps; compress/trim prompts or pin via DA and pass a digest.
	•	Verification mismatch: Ensure the exact contract.py + manifest.json used in build_package.

⸻

13) Next Steps
	•	Chain prompts & tools: Combine AI outputs with DA blobs and Randomness.
	•	Pay-for-compute: Integrate treasury/escrow so requesters pre-fund jobs.
	•	Moderation & guardrails: Pre-hash or structure prompts; restrict models.
	•	Batching: Add multi-submit + multi-consume methods for throughput.

References
	•	contracts/stdlib/capabilities/ai_compute.py
	•	capabilities/* (host/provider/result store, RPC)
	•	aicf/* (queue, SLA, pricing, settlement)
	•	contracts/examples/ai_agent/*
	•	sdk/python quickstarts
