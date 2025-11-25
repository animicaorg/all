# Write Your First Python Contract

Welcome! This tutorial walks you from **zero → deployed** with Animica’s deterministic **Python VM** (“VM(Py)”).  
You’ll create a tiny **Counter** contract, run it locally, then deploy and call it on a node.

> Helpful refs:
> - VM overview: `docs/vm/OVERVIEW.md`
> - ABI: `docs/vm/ABI.md`
> - Determinism & sandbox: `docs/vm/SANDBOX.md`
> - Gas: `docs/vm/GAS_MODEL.md`
> - Examples: `vm_py/examples/`

---

## 0) Prerequisites

Choose **one** of these paths:

- **Browser-only (fastest):** Use **Studio Web** (WASM VM) — no installs.
- **Local CLI/SKD:**
  - Python 3.10+ and `pipx` or `pip`  
  - (Optional) a local devnet (see `docs/dev/QUICKSTART.md`)

Install useful tools:

```bash
# VM(Py) & Studio tools (from repo root if editable), otherwise pip install when published
pip install -e vm_py/ studio-wasm/ sdk/python/ studio-services/ || true

# Or just install the Python SDK if you only want deployment & calls:
pip install omni-sdk


⸻

1) Contract Anatomy

A contract is plain Python compiled to a small IR and executed by the deterministic VM.
You may only import from the VM’s stdlib (exposed as from stdlib import ...).

Create counter/contract.py:

# counter/contract.py
from stdlib import storage, events, abi

# Storage layout: a single integer under key b"counter"
KEY = b"counter"

def init(start: int = 0) -> None:
    """Optional: called at deploy with initial value."""
    storage.set(KEY, start)
    events.emit(b"Init", {"value": start})

def inc(by: int = 1) -> int:
    """Increase the counter by 'by' and return the new value."""
    # Read current value (default 0 if unset)
    current = storage.get(KEY, default=0)
    # Bounds & type safety (fail: Revert)
    abi.require(by >= 0, b"NEGATIVE_INCREMENT")
    new_value = current + by
    storage.set(KEY, new_value)
    events.emit(b"Inc", {"by": by, "value": new_value})
    return new_value

def get() -> int:
    """Return the current counter value."""
    return storage.get(KEY, default=0)

Determinism Rules (must follow!)
	•	No imports beyond stdlib (no os, time, random, requests, …).
	•	No floating point; keep integers & bytes.
	•	Bounded loops & recursion limits; avoid unbounded data growth.
	•	All I/O goes through stdlib APIs (storage, events, hash, treasury, syscalls).

⸻

2) ABI & Manifest

The manifest binds contract functions to the on-chain ABI shape.

Create counter/manifest.json:

{
  "name": "Counter",
  "version": "1.0.0",
  "abi": {
    "functions": [
      {"name": "init", "inputs": [{"name": "start", "type": "int"}], "outputs": []},
      {"name": "inc",  "inputs": [{"name": "by",    "type": "int"}], "outputs": [{"type": "int"}]},
      {"name": "get",  "inputs": [],                                "outputs": [{"type": "int"}]}
    ],
    "events": [
      {"name": "Init", "args": {"value": "int"}},
      {"name": "Inc",  "args": {"by": "int", "value": "int"}}
    ]
  },
  "resources": {
    "storage_keys": ["counter"]
  },
  "caps": {
    "max_gas": 200000,
    "max_code_bytes": 65536
  }
}

Tip: See vm_py/specs/ABI.md for full type list and validation rules.

⸻

3) Compile & Simulate (pick one)

A) Studio Web (no install)
	1.	Open Studio Web (from website’s Studio page).
	2.	Create a new project; add counter/contract.py and counter/manifest.json.
	3.	Click Compile → should show IR size & gas estimate.
	4.	Switch to Simulate, call:
	•	init(start=5) → expect event Init and storage counter=5
	•	inc(by=2) → returns 7 with event Inc
	•	get() → returns 7

B) VM(Py) CLI (local)

# Pretty-print IR & static gas estimate
python -m vm_py.cli.inspect_ir --manifest counter/manifest.json --source counter/contract.py

# Quick run: calls without chain state (local ephemeral storage)
python -m vm_py.cli.run --manifest counter/manifest.json --source counter/contract.py --call init --args '{"start": 3}'
python -m vm_py.cli.run --manifest counter/manifest.json --source counter/contract.py --call inc  --args '{"by": 4}'
python -m vm_py.cli.run --manifest counter/manifest.json --source counter/contract.py --call get


⸻

4) Deploy to a Node

You need:
	•	An account with funds (Wallet extension or SDK-managed key)
	•	RPC URL & chainId (see website/chains/*.json)

Deploy with Python SDK

# deploy_counter.py
import json
from omni_sdk.config import Config
from omni_sdk.tx import build, send
from omni_sdk.contracts import deployer

RPC_URL  = "http://localhost:8545"   # or a public/test RPC
CHAIN_ID = 1                         # adjust to your network

cfg = Config(rpc_url=RPC_URL, chain_id=CHAIN_ID)

# Load sources
manifest = json.load(open("counter/manifest.json","r"))
source   = open("counter/contract.py","rb").read()

# Build a deploy tx (gas will be estimated by node/policy)
tx = deployer.Deployer.build_deploy_tx(manifest=manifest, source_py=source, init_args={"start": 5})

# Sign & send (using an unlocked local key — see wallet docs for key management)
# For demo: use a dev keypair; in production load from secure keystore or wallet extension flow.
signed = build.sign_local(tx, private_key_hex="0x<devkey>")  # PQ signer if enabled in SDK config
receipt = send.send_and_wait(cfg, signed, timeout_s=60)

print("Deployed at address:", receipt["contractAddress"])

Then call methods:

from omni_sdk.contracts.client import ContractClient

addr = receipt["contractAddress"]
counter = ContractClient(cfg, address=addr, abi=manifest["abi"])

print("inc(2) ->", counter.call_write("inc", {"by": 2}))  # returns new value, waits for receipt
print("get()  ->", counter.call_read("get"))              # read-only call via simulation/RPC

Alternative: Deploy via Studio Services /deploy endpoint (see studio-services/README.md).

⸻

5) Events, Receipts & Logs

Each write call yields a Receipt:
	•	status (SUCCESS/REVERT/OOG)
	•	gasUsed
	•	logs (topics + data, ABI-decoded by SDK)
	•	blockHash, txHash

Use ContractClient.events() to filter past logs or subscribe to newHeads and decode events.

⸻

6) Gas Basics
	•	Static estimate from compiler provides an upper bound.
	•	Actual gasUsed may be lower (refunds) but never exceeds the bound unless logic branches.
	•	Intrinsic gas for transfer/deploy/call documented in execution/specs/GAS.md.

Tips:
	•	Keep loops small and bounded.
	•	Prefer fixed-size storage & calldata.
	•	Emit compact events (use ints/bytes; avoid nested dicts).

⸻

7) Debugging Failures

Common errors & fixes:

Error	Meaning	Fix
ValidationError: forbidden import	Non-stdlib import	Remove/replace with stdlib
Revert: NEGATIVE_INCREMENT	abi.require guard failed	Pass valid args
OOG	Out-of-gas	Increase gas limit or optimize
InvalidAccess	Storage/cap limits	Declare correct keys/caps in manifest
TypeError	ABI mismatch	Ensure types match ABI exactly

Use:

# Detailed trace locally
python -m vm_py.cli.run --manifest counter/manifest.json --source counter/contract.py --call inc --args '{"by": 1}' --trace


⸻

8) Testing

Minimal pytest example:

# tests/test_counter.py
import json
from vm_py.runtime.loader import load
from vm_py.runtime.engine import Engine

def test_counter_inc():
    manifest = json.load(open("counter/manifest.json"))
    source   = open("counter/contract.py","rb").read()
    mod = load(manifest=manifest, source_py=source)

    eng = Engine(mod)
    eng.call("init", {"start": 1})
    out = eng.call("inc", {"by": 2})
    assert out == 3
    assert eng.call("get") == 3

Run:

pytest -q


⸻

9) Production Checklist
	•	No disallowed imports; passes vm_py/tests/test_validator.py-style checks
	•	ABI verified; types stable
	•	Gas bound confirmed with real traces
	•	Events are versioned and documented
	•	Storage keys & migration path defined
	•	Optional syscalls (DA/AI/Quantum/zk/random) reviewed for determinism & limits

⸻

10) Next Steps
	•	See docs/vm/PATTERNS.md for access control & upgrade approaches
	•	Explore capabilities/ for AI/Quantum/DA/zk syscalls
	•	Read docs/dev/DEBUGGING.md for tracing and metrics
	•	Package your contract + ABI as an artifact for verification via studio-services

You’ve built, simulated, deployed, and called your first Python contract on Animica—nice work!
