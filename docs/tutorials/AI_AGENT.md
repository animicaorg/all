# AI Agent — Enqueue AI Job & Consume Results

This tutorial shows how a **smart contract** can enqueue a small AI job and
**deterministically** consume its result on the **next block** using the
capabilities layer (see: `capabilities/` and `aicf/`).

The flow:

1) Contract calls `syscalls.ai_enqueue(model, prompt)` → gets a **task_id**  
2) Off-chain, AICF assigns a provider → job runs → produces output + proof(s)  
3) Verifier/adapters populate a result record (proofs → results)  
4) On the **next block**, contract calls `syscalls.read_result(task_id)` → bytes

> Determinism rule of thumb: **enqueue this block, read next block**.  
> If you try to read in the same block you’ll get “no result yet”.

---

## 0) Prereqs

- Devnet node, RPC, and miner running (see **Dev Quickstart**).
- Python SDK installed (`sdk/python`).
- VM CLI available (`vm_py/cli`).
- Optional: AICF service loop running (assigns jobs to mock providers).

---

## 1) Contract: `ai_agent.py`

A tiny “AI Agent” contract that requests a completion and later consumes it.

```python
# ai_agent.py — Minimal AI job requester/consumer for the Python-VM
# Exposes:
#   - request(model: bytes, prompt: bytes) -> bytes   # returns task_id
#   - consume(task_id: bytes) -> bytes                # returns result bytes (next block)
#   - last_task() -> bytes
#   - last_result() -> bytes
#   - result_ready(task_id: bytes) -> bool

from stdlib import storage, events, abi, syscalls

# storage keys
def _k_last_task()   -> bytes: return b"ai:last_task"
def _k_last_result() -> bytes: return b"ai:last_result"

def last_task() -> bytes:
    return storage.get(_k_last_task()) or b""

def last_result() -> bytes:
    return storage.get(_k_last_result()) or b""

def request(model: bytes, prompt: bytes) -> bytes:
    """
    Enqueue an AI job. Returns a deterministic task_id (bytes).
    Deterministic semantics: the result (if any) becomes readable next block.
    """
    abi.require(len(model) > 0, b"empty model")
    abi.require(len(prompt) > 0, b"empty prompt")

    # ai_enqueue returns a JobReceipt with at least: task_id (bytes)
    # The runtime binding normalizes to just task_id (bytes) for convenience.
    task_id = syscalls.ai_enqueue(model, prompt)  # bytes
    storage.set(_k_last_task(), task_id)

    events.emit(b"JobEnqueued", {b"model": model, b"task_id": task_id})
    return task_id

def result_ready(task_id: bytes) -> bool:
    """
    Cheap readiness probe (non-throwing) by attempting a peek-read.
    read_result returns b"" or None if not ready yet (depending on runtime).
    We standardize here: treat falsy as not-ready.
    """
    data = syscalls.read_result(task_id)
    return bool(data)

def consume(task_id: bytes) -> bytes:
    """
    Read the result for task_id. Must be called on/after the next block:
    - If not ready, revert.
    - If ready, persist and emit JobCompleted.
    """
    res = syscalls.read_result(task_id)  # returns bytes or empty/None if not ready
    abi.require(res is not None and len(res) > 0, b"result not ready")

    storage.set(_k_last_result(), res)
    events.emit(b"JobCompleted", {b"task_id": task_id, b"size": len(res)})
    return res

Notes
	•	The VM stdlib syscall surface is wired through capabilities/runtime to
deterministic host-side providers.
	•	ai_enqueue(model, prompt) returns a deterministic task_id computed
from (chainId | height | txHash | caller | payload).
See: capabilities/jobs/id.py.
	•	read_result(task_id) is deterministic and only returns a value once
the result has been resolved from proofs for a later block.

⸻

2) Compile the Contract

python -m vm_py.cli.compile ai_agent.py --out /tmp/ai_agent.ir
python -m vm_py.cli.inspect_ir /tmp/ai_agent.ir

You can also simulate locally with studio-wasm before deploying.

⸻

3) Deploy & Use (Python SDK)

The following script deploys the contract, submits a job, waits a block, and
consumes the result.

# deploy_and_run_ai_agent.py
import time
from pathlib import Path
from omni_sdk.config import Config
from omni_sdk.rpc.http import HttpRpc
from omni_sdk.wallet.mnemonic import new_mnemonic
from omni_sdk.wallet.signer import Dilithium3Signer
from omni_sdk.address import address_from_pubkey
from omni_sdk.tx.build import build_deploy_tx, build_call_tx
from omni_sdk.tx.send import send_and_await_receipt
from omni_sdk.contracts.client import ContractClient

RPC_URL = "http://127.0.0.1:8545"
CHAIN_ID = 1

cfg = Config(rpc_url=RPC_URL, chain_id=CHAIN_ID)
rpc = HttpRpc(cfg)

# owner account
mn = new_mnemonic()
signer = Dilithium3Signer.from_mnemonic(mn)
addr = address_from_pubkey("dilithium3", signer.public_key())

# load IR + minimal ABI for our three functions
ir = Path("/tmp/ai_agent.ir").read_bytes()
abi = {
  "functions": [
    {"name":"request","inputs":[{"name":"model","type":"bytes"},{"name":"prompt","type":"bytes"}],"returns":"bytes","mutates":True},
    {"name":"result_ready","inputs":[{"name":"task_id","type":"bytes"}],"returns":"bool"},
    {"name":"consume","inputs":[{"name":"task_id","type":"bytes"}],"returns":"bytes","mutates":True},
    {"name":"last_task","inputs":[],"returns":"bytes"},
    {"name":"last_result","inputs":[],"returns":"bytes"}
  ],
  "events": [
    {"name":"JobEnqueued","inputs":[{"name":"model","type":"bytes"},{"name":"task_id","type":"bytes"}]},
    {"name":"JobCompleted","inputs":[{"name":"task_id","type":"bytes"},{"name":"size","type":"u64"}]}
  ]
}

# 1) Deploy
tx0 = build_deploy_tx(from_address=addr, manifest={"abi": abi}, code=ir,
                      gas_price=1, gas_limit=700_000, nonce=0)
rcpt0 = send_and_await_receipt(rpc, signer.sign_tx(tx0, CHAIN_ID), 30)
assert rcpt0["status"] == "SUCCESS", rcpt0
contract = rcpt0["contractAddress"]
print("AI Agent deployed at:", contract)

client = ContractClient(rpc=rpc, address=contract, abi=abi)

# 2) Enqueue a job
model = b"animica/minilm"        # example string id
prompt = b"Summarize: PoIES incentivizes useful compute..."
tx1 = build_call_tx(from_address=addr, to_address=contract, abi=abi,
                    function="request", args=[model, prompt],
                    gas_price=1, gas_limit=200_000, nonce=1)
rcpt1 = send_and_await_receipt(rpc, signer.sign_tx(tx1, CHAIN_ID), 30)
print("request status:", rcpt1["status"])

# Parse task_id from return value (ContractClient can decode if preferred)
# For simplicity, fetch via "last_task" view:
res_task = client.call("last_task", [])
task_id = res_task if isinstance(res_task, (bytes, bytearray)) else res_task.get("return", b"")
print("task_id:", task_id.hex() if isinstance(task_id, (bytes, bytearray)) else task_id)

# 3) Wait for next block (dev miner usually ticks; otherwise sleep and poll head)
def head_number():
  head = rpc.call("chain.getHead", [])
  return head.get("number", 0)

start_h = head_number()
while head_number() <= start_h:
    time.sleep(1.0)

# 4) Try consume on/after the next block
tx2 = build_call_tx(from_address=addr, to_address=contract, abi=abi,
                    function="consume", args=[task_id],
                    gas_price=1, gas_limit=250_000, nonce=2)
rcpt2 = send_and_await_receipt(rpc, signer.sign_tx(tx2, CHAIN_ID), 60)
print("consume status:", rcpt2["status"])

# 5) Read the stored result
res = client.call("last_result", [])
if isinstance(res, (bytes, bytearray)):
    print("Result bytes len:", len(res))
    print("Preview:", res[:120])
else:
    print("Result:", res)

Troubleshooting
	•	If consume reverts with “result not ready”, wait another block or ensure the
AICF worker/adapter is running so that proofs → results are resolved.

⸻

4) (Optional) Observe Jobs via AICF Client

You can also poll job status out-of-band for monitoring:

from omni_sdk.aicf.client import AICFClient

aicf = AICFClient(cfg)
info = aicf.get_job(task_id)   # metadata (enqueued/assigned/completed)
print(info)

(Exact fields depend on your running policy/fixtures; see aicf/rpc/methods.py.)

⸻

5) Determinism & Economics
	•	Determinism: Contracts may only read results one or more blocks after
enqueue. The runtime enforces this to avoid non-deterministic outcomes.
	•	Accounting: The capabilities layer tracks units (AI tokens / cost) and
integrates with the treasury split (see capabilities/specs/TREASURY.md,
aicf/economics/).
	•	Security: AI outputs are accompanied by attestations/proofs and mapped to
consensus via proofs/ → capabilities/jobs/resolver.py.

⸻

6) Extending the Agent
	•	Add a method to pin prompts/outputs to DA (blob commitments) for audit.
	•	Emit richer events with content digests.
	•	Support multiple concurrent task_ids (store in a mapping keyed by caller).
	•	Add timeouts and cancel semantics (policy-dependent).

⸻

7) Common Errors
	•	“result not ready”: you called consume in the same block; wait ≥1 block.
	•	“empty prompt/model”: guard checks; send non-empty bytes.
	•	Gas too low: bump gas_limit for consume if output sizes are large.

⸻

8) What’s Next
	•	Wire a front-end in studio-web to enqueue prompts and stream results/events.
	•	Try quantum jobs with a similar flow (quantum_enqueue).
	•	Attach Poseidon/zk verification for model-specific predicates (future).

Happy building! 🤖
