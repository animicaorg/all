# AICF Client Guide — How Contracts & Users Request Compute

This guide explains how **smart contracts** and **end users** request AI/Quantum compute through the **AI Compute Fund (AICF)**. It covers the call flow, budgets, result retrieval, SDK usage, and common errors.

> Code anchors
>
> - Contract syscalls: `vm_py/stdlib/syscalls.py` (forwarded by `capabilities/runtime/abi_bindings.py`)
> - Host provider: `capabilities/host/compute.py`, `capabilities/jobs/*`
> - Result consumption (next block): `capabilities/host/result_read.py`, `capabilities/jobs/resolver.py`
> - Proofs → settlement: `aicf/integration/proofs_bridge.py`, `aicf/economics/*`
> - SDKs: `sdk/python/omni_sdk/aicf/client.py`, `sdk/typescript/src/aicf/client.ts`

---

## 1) Big picture

1. A **contract** invokes a syscall to enqueue an AI/Quantum job.
2. The **task id** is deterministic:  
   `task_id = H(chainId | height | txHash | caller | payload)` (see `capabilities/jobs/id.py`).
3. The job is matched to an attested provider by AICF. The provider runs it **off-chain**.
4. The provider publishes **proofs/evidence** in a subsequent block.  
   The node **resolver** maps those proofs back to `task_id` and writes a **ResultRecord**.
5. The contract (or a later transaction) calls `read_result(task_id)` to **consume** the result.  
   Results are **single-consumption** and **available from the next block** after proof inclusion.

---

## 2) Contract-side API (Python VM)

Contracts call a tiny, deterministic syscall surface (gas-metered, size-capped).

### 2.1 Enqueue

```python
# vm_py/stdlib/syscalls.py (re-exported in contract sandbox)
from stdlib import abi, storage
from stdlib import events
from stdlib.syscalls import ai_enqueue, quantum_enqueue, read_result

# Example: simple inference requester
def request_infer(model: bytes, prompt: bytes, max_units: int) -> bytes:
    """
    Returns deterministic task_id (bytes). Off-chain compute will run under the given budget.
    """
    # OPTIONAL: enforce local caps; avoid unbounded costs
    if len(prompt) > 4096:
        abi.revert(b"prompt too large")

    task_id = ai_enqueue(model=model, prompt=prompt, max_units=max_units)
    # Persist so a later call can fetch the result (optional)
    storage.set(b"last_task", task_id)
    events.emit(b"AICFJobQueued", {b"model": model, b"task_id": task_id})
    return task_id

Parameters
	•	model: bytes — model identifier (policy-allowed; see network policy).
	•	prompt: bytes — input payload (capped by policy).
	•	max_units: int — budget ceiling; pricing explained below.

Return
	•	task_id: bytes — content-addressable, unique, deterministic.

2.2 Read result (next block)

def consume_last_result() -> bytes:
    task_id = storage.get(b"last_task") or abi.revert(b"no task")
    data = read_result(task_id)  # returns bytes or reverts if not yet available
    events.emit(b"AICFResult", {b"task_id": task_id, b"size": len(data)})
    return data

Notes
	•	Results are readable once (consumed). Subsequent reads revert with NoResultYet or AlreadyConsumed.
	•	Availability is next block after proof inclusion (deterministic).

⸻

3) Fees, budgets, and payouts
	•	Client budget: max_units bounds what the contract is willing to pay for the job.
	•	Pricing is defined in aicf/economics/pricing.py and governed by policy; units are scheme-specific
(e.g., ai_units for AI, quantum_units for quantum shots/depth).
	•	Treasury split: provider / treasury / miner per aicf/economics/split.py.
	•	The submitter transaction pays gas for enqueuing and later result consumption; compute reward is paid from AICF funds or job escrow (depending on network policy).

⸻

4) SDK usage (end users & dapps)

End users don’t call AICF directly; they call your contract methods which use the syscalls. Below are helper snippets to trigger contract methods and subscribe to events.

4.1 Python SDK

from omni_sdk.rpc.http import RpcClient
from omni_sdk.contracts.client import ContractClient
from omni_sdk.tx.send import send_and_await
from omni_sdk.wallet.keystore import Keystore

rpc = RpcClient(base_url="http://localhost:8545", chain_id=1)
wallet = Keystore.load("~/.animica/keystore.json").unlock("pass")
acct = wallet.default_account()

# Assume ABI/addr known
ai_contract = ContractClient(rpc, address="anim1...", abi=... , signer=acct)

# 1) Enqueue
tx = ai_contract.tx("request_infer", model=b"llama-3.1-mini", prompt=b"Hello, world", max_units=2000)
receipt = send_and_await(rpc, tx)
task_id = receipt["logs"][0]["data"]["task_id"]

# 2) Wait one or more blocks (or subscribe WS for AICF proof inclusion), then read:
tx2 = ai_contract.tx("consume_last_result")
receipt2 = send_and_await(rpc, tx2)
result_bytes = receipt2["return_data"]
print("Result:", result_bytes.decode("utf-8", "ignore"))

4.2 TypeScript SDK (Browser / Node)

import { HttpClient } from "@animica/sdk/rpc/http";
import { ContractClient } from "@animica/sdk/contracts/client";
import { Keystore } from "@animica/sdk/wallet/keystore";

const rpc = new HttpClient({ baseUrl: import.meta.env.PUBLIC_RPC_URL, chainId: 1 });
const ks = await Keystore.fromFile("keystore.json"); await ks.unlock("pass");
const signer = ks.default();

const c = new ContractClient(rpc, "anim1...", /* abi */ ABI, signer);

// Enqueue
const tx = await c.tx("request_infer", { model: new TextEncoder().encode("llama-3.1-mini"),
                                         prompt: new TextEncoder().encode("Hello"),
                                         max_units: 2000 });
const rec = await tx.wait();

// Parse task_id from event or return value depending on your contract
const taskId = rec.logs[0].data.task_id as Uint8Array;

// Later / next block
const tx2 = await c.tx("consume_last_result", {});
const rec2 = await tx2.wait();
const result = new TextDecoder().decode(rec2.return_data);
console.log("Result:", result);

4.3 Event subscriptions (optional)

You can subscribe to:
	•	jobAssigned, jobCompleted via AICF WS (if mounted),
	•	your contract’s AICFJobQueued / AICFResult logs via the standard events stream.

See sdk/*/rpc/ws.ts examples.

⸻

5) Models, circuits, and policy
	•	The network enforces an allowlist of model identifiers and quantum capabilities via policy roots.
	•	Inputs are sanitized and size-capped at the syscall shim (capabilities/runtime/determinism.py).
	•	ZK-based post-checks (e.g., ZKML) require pinned VKs (zk/registry/vk_cache.json) and are verified by the node’s ZK hooks (zk/integration/omni_hooks.py).

⸻

6) Determinism & timing
	•	task_id derivation is deterministic (see §1).
	•	Results become readable after inclusion of provider proofs (usually next block, worst-case per policy timeout / retries).
	•	If no valid proof arrives within the timeout window, the job expires and may be re-queued by your contract logic if desired.

⸻

7) Common errors & handling

Contract-level reverts you may observe:
	•	NotDeterministic — payload too large or disallowed shape.
	•	LimitExceeded — max_units too low for minimal run.
	•	NoResultYet — read attempted before proof inclusion or after consumption.
	•	AttestationError — provider evidence invalid (rare; triggers slash on provider).
	•	QueueFull / RateLimited — transient; try again later.

Your contract should surface clear messages and optionally keep minimal job state (e.g., task_id → status map) so dapps can render progress.

⸻

8) Design patterns
	•	Two-step read: split request_* and consume_* into separate calls; store task_id for the user.
	•	Budget bands: accept a max_units hint from the caller; clamp to safe min/max on-chain.
	•	Result normalization: define a schema for result bytes (e.g., JSON; CBOR) and validate before emitting events.

⸻

9) Security notes
	•	Providers must pass TEE attestation and trap checks; misbehavior leads to slashing.
	•	Inputs are hashed into the transcript; private prompts should be encrypted client-side if you need confidentiality (then decrypt off-chain; be mindful of trust assumptions).
	•	Do not store raw sensitive outputs on-chain; store references, commitments, or summaries.

⸻

10) Quick checklist
	•	Contract methods for enqueue + consume
	•	Input caps & budget validation
	•	Events for UX (AICFJobQueued, AICFResult)
	•	Dapp retries and status polling/subscriptions
	•	Clear error messages and fallbacks

⸻

11) Further reading
	•	docs/aicf/OVERVIEW.md, docs/aicf/SLA.md, docs/aicf/JOB_API.md, docs/aicf/SECURITY.md
	•	docs/randomness/OVERVIEW.md (beacon seeding)
	•	docs/zk/FORMATS.md, zk/registry/* (if using ZK post-verify)
	•	vm_py/specs/ABI.md, capabilities/specs/SYSCALLS.md
	•	SDK quickstarts: sdk/python/README.md, sdk/typescript/README.md
