# Debugging Contracts on VM(Py)
**Topics:** tracing strategies, structured logs, receipts, gas reports  
**Audience:** contract authors, node/RPC operators, SDK/tooling maintainers

This guide collects practical techniques for understanding what your Python-VM contract did during a call, why it failed, and how much gas it used—**without** breaking determinism.

---

## 1) Mental Model: Where Signals Come From

- **Contract-level events** → emitted via `stdlib.events.emit(name: bytes, args: dict)`.  
  Surface in **receipts** (topics/data), decodable by SDKs & Explorer.

- **Execution results** → `ApplyResult` / `Receipt` (status, gasUsed, logs, bloom).  
  Accessible through RPC: `tx.getTransactionReceipt`.

- **Static gas estimate** → compiler pass (`vm_py/compiler/gas_estimator.py`) over IR.

- **Node logs (structured)** → JSON logs from `core/`, `rpc/`, `execution/`, etc.  
  Correlate by `request_id`, `tx_hash`, `block_hash`.

- **Studio/WASM simulator** → local, deterministic “dry-runs” with result + event capture.

> There is **no printf** inside contracts; use **events** as your primary debug channel.

---

## 2) Contract-Level Debug Events (Recommended)

Add compact, developer-scoped events (remove or gate before production):

```python
from stdlib import events, abi, storage, hash

def _dbg(label: bytes, payload: dict):
    # Keep keys/values small; payload keys should be bytes
    events.emit(b"DBG", {b"l": label, b"p": payload})

def inc(caller: bytes):
    key = b"state:v1:counter"
    raw = storage.get(key) or (0).to_bytes(8, "big")
    val = int.from_bytes(raw, "big") + 1
    storage.set(key, val.to_bytes(8, "big"))
    _dbg(b"inc", {b"by": caller, b"new": val})

Why events: they’re deterministic, included in receipts, easy to search/index, and won’t violate sandboxing.

⸻

3) Reading Receipts & Decoding Events

Via Python SDK

from omni_sdk.rpc.http import RpcClient
from omni_sdk.contracts.events import decode_events
from omni_sdk.types.core import Receipt

rpc = RpcClient(url="http://localhost:8545", timeout_s=5)
txh = "0x…"  # your tx hash
rcp: Receipt | None = rpc.call("tx.getTransactionReceipt", [txh])

assert rcp and rcp["status"] == "SUCCESS", rcp
events = decode_events(rcp["logs"])
for e in events:
    print(e["name"], e["args"])  # e.g., DBG {'l': 'inc', 'p': {'by': 'anim1…', 'new': 42}}
print("gasUsed:", rcp["gasUsed"])

Via TypeScript SDK

import { HttpClient } from "@animica/sdk/rpc/http";
import { decodeEvents } from "@animica/sdk/contracts/events";

const rpc = new HttpClient({ url: "http://localhost:8545" });
const rcp = await rpc.call("tx.getTransactionReceipt", ["0x…"]);
if (!rcp || rcp.status !== "SUCCESS") throw new Error("failed");

const evs = decodeEvents(rcp.logs);
evs.forEach(e => console.log(e.name, e.args));
console.log("gasUsed:", rcp.gasUsed);


⸻

4) Gas: Intrinsic, Static Upper Bound, Dynamic Usage
	•	Intrinsic gas (envelope/kind/access list/blob): execution/gas/intrinsic.py.
	•	Static estimate (upper bound) from IR: vm_py/compiler/gas_estimator.py.
	•	CLI: python -m vm_py.cli.inspect_ir --in path/to.ir (prints ops + static gas).
	•	Dynamic usage (actual) from ApplyResult.gasUsed in the receipt.

Workflow
	1.	Compile → inspect IR & static estimate.
	2.	Simulate locally (Studio/WASM) to sanity-check usage.
	3.	Send tx → compare dynamic gasUsed vs static estimate; add margin.

⸻

5) Local Simulation (Studio/WASM)

Great for rapid debugging without a node or state writes.
	•	Library: studio-wasm
	•	API (TS): simulateCall, compileSource, estimateGas

Example:

import { simulateCall, compileSource, estimateGas } from "@animica/studio-wasm";

const { ir, manifest } = await compileSource(srcBytes, manifestJson);
const est = await estimateGas({ ir, manifest, method: "inc", args: [] });
const out = await simulateCall({ ir, manifest, method: "inc", args: [] });
// out.events → includes your DBG events


⸻

6) Node & RPC Logs (Structured)

Enable JSON logs for correlation:
	•	Config: see core/logging.py, rpc/middleware/logging.py.
	•	Run:
	•	Set env ANIMICA_LOG=json (or use your config file).
	•	RPC logs include request_id, method, duration_ms, status.
	•	Execution logs include tx_hash, block_height, gas_used.

Examples (jq-friendly)

# Only errors
journalctl -u animica-node | jq 'select(.level=="error")'

# Correlate by tx hash
journalctl -u animica-node | jq 'select(.tx_hash=="0x...")'


⸻

7) Common Failures & How to Investigate

OOG (Out Of Gas)
	•	Symptom: status="OOG"; partial state rolled back.
	•	Fix: check static estimate; raise gas limit; inspect expensive loops or hashing.

Revert
	•	Symptom: status="REVERT"; optional error payload in receipt/log.
	•	Fix: ensure abi.require(...) predicates; emit precise reason codes (short ASCII).

ValidationError (Compile/Load)
	•	Forbidden imports/builtins, recursion, or type errors.
	•	Use vm_py/cli/compile.py and vm_py/cli/inspect_ir.py to pinpoint.

ForbiddenImport
	•	The sandbox denies os, time, random, networking.
	•	Replace with provided deterministic stdlib (hash, storage, events, random stub).

⸻

8) Tactics for Better Observability
	•	Event taxonomies: DBG for dev; ParamChanged, Upgraded, Paused for ops.
	•	Stable keys: keep event arg keys short & consistent (b"by", b"new", b"old").
	•	Monotonic counters: store version/epoch for quick state diffs.
	•	Deterministic hashing: when you must attest to bytes, emit sha3_256(data) not the raw bytes.

⸻

9) CLI Aids (Local)
	•	Run a call against a manifest
python -m vm_py.cli.run --manifest examples/counter/manifest.json --call inc
	•	Inspect IR & static gas
python -m vm_py.cli.inspect_ir --in out.ir
	•	Apply a block to a DB (debug)
python -m execution.cli.apply_block --db sqlite:///animica.db --block path/to/block.cbor

⸻

10) Receipt Anatomy (Reference)

A minimal successful receipt looks like:

{
  "status": "SUCCESS",
  "gasUsed": 12345,
  "logs": [
    {
      "address": "anim1…",
      "topics": ["DBG"],
      "data": {"l":"inc","p":{"by":"anim1…","new":42}}
    }
  ],
  "bloom": "0x…"
}


⸻

11) Checklists

Before Sending a Tx
	•	Compile succeeds; IR passes validation.
	•	Static gas estimate reviewed; gas limit has margin.
	•	Inputs ABI-encoded; lengths capped.

After Failure
	•	Inspect receipt status & logs for reason codes.
	•	Compare dynamic gasUsed to static estimate (identify hotspots).
	•	Grep JSON logs by tx_hash for execution hints.

⸻

12) Pointers
	•	docs/vm/SANDBOX.md — allowed imports, determinism notes
	•	docs/vm/GAS_MODEL.md — metering details
	•	docs/spec/RECEIPTS_EVENTS.md — receipt/log format & hashing
	•	studio-wasm/ — simulator for browser & local workflows
	•	sdk/ — Python & TS helpers for RPC, events, and deployments

