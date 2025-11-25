# Quantum RNG — example contract

A minimal, end-to-end example that derives verifiable randomness by **enqueuing a
quantum circuit with traps**, then **consuming the provider’s output on the next
block** via the `capabilities` runtime. This is meant for **devnet/testing**. In
production you’ll usually combine quantum-sourced bytes with the chain beacon
(see `contracts/stdlib/capabilities/randomness.py`) or a commit-reveal to reduce
bias and provider influence.

> **One-block delay:** Results are available *no earlier* than the block after
> the enqueue. The provider returns a proof (QuantumProof) that feeds the
> next-block resolution path.

---

## What this example shows

- A contract method that **requests** quantum randomness by enqueuing a small
  circuit (Hadamards + measurement), embedding **trap qubits** to verify that
  the device behaved quantumly.
- A **deterministic task ID** (from capability layer) that binds the request to
  `chainId | height | txHash | caller | payload`.
- A **result read** path that succeeds only after the proof lands and the
  capability resolver populates the result store.
- Simple **eventing** so UIs can watch request/fulfillment.

---

## Contract interface (ABI summary)

> See the adjacent `manifest.json` for the canonical ABI.

- `request(bits: int=256, shots: int=256, trap_rate: int=16) -> bytes32 task_id`  
  Enqueue a quantum job that returns ~`bits` of randomness. `shots` controls
  sampling repetitions; `trap_rate` controls the density of trap checks
  (1 trap per `trap_rate` data qubits). Emits `Requested(task_id, bits, shots, trap_rate)`.

- `poll(task_id: bytes32) -> (ready: bool, out: bytes)`  
  Read the result record deterministically. Returns `(False, b"")` if not ready
  yet; `(True, bytes)` once fulfilled. Emits `Fulfilled(task_id, out)` on the
  first successful read.

- `last() -> bytes`  
  The most recent fulfilled output (cached in storage for convenience).

- **Events**
  - `Requested(task_id: bytes32, bits: uint32, shots: uint32, trap_rate: uint16)`
  - `Fulfilled(task_id: bytes32, out: bytes)`

**Notes**

- Outputs are raw bytes produced by the provider circuit post-processing (uniform
  extraction in the example). You may further mix with the chain beacon.
- The example sanitizes inputs to keep costs bounded and to preserve determinism.

---

## How it works (one-page flow)

Block N (Tx A calls request)
└─ Contract -> capabilities.host.compute.quantum_enqueue(circuit, shots, traps)
└─ Deterministic task_id = H(chainId|height|txHash|caller|payload)
└─ Emits Requested(task_id, …)

Between N and N+1
└─ Provider runs circuit, returns digest + traps outcomes
└─ Node receives QuantumProof → proofs.verify → consensus accepts

Block N+1
└─ capabilities.jobs.resolver ingests proof → result_store[task_id] = bytes
└─ Any Tx can call poll(task_id)
└─ If present: returns (True, bytes), emits Fulfilled(…)

---

## Circuit & traps (quick primer)

The example builds a tiny parameterized circuit:

- For randomness: apply `H` on selected qubits, measure in Z basis.
- For traps: interleave trap qubits with known outcomes (e.g., X/Y basis checks).
- The **trap ratio** (1 trap per *k*) and minimum **pass threshold** are enforced
  by the off-chain QuantumProof verifier. The contract itself only enqueues and
  later reads an attested result.

For reference formats, see:
- `capabilities/fixtures/quantum_circuit.json`
- `proofs/quantum_attest/*` and `proofs/quantum.py`

---

## Quickstart

### 0) Prereqs

- A local **devnet** (see `tests/devnet/docker-compose.yml`) or your own node.
- Environment:
  ```bash
  export RPC_URL="http://127.0.0.1:8545"
  export CHAIN_ID=1337
  export DEPLOYER_MNEMONIC="..."   # or use your test keystore

1) Build the package

You can use the generic contracts Makefile:

# From repo root
make build EX=quantum_rng
# Artifacts land under contracts/build/

Or call the tool directly:

python -m contracts.tools.build_package \
  --source contracts/examples/quantum_rng/contract.py \
  --manifest contracts/examples/quantum_rng/manifest.json \
  --out contracts/build/quantum_rng.pkg.json

2) Deploy

Either with the generic deploy tool:

python -m contracts.tools.deploy \
  --source   contracts/examples/quantum_rng/contract.py \
  --manifest contracts/examples/quantum_rng/manifest.json \
  --rpc "$RPC_URL" --chain-id "$CHAIN_ID" --mnemonic "$DEPLOYER_MNEMONIC" --json

Or via SDK (Python) minimal sketch:

from omni_sdk.config import Config
from omni_sdk.rpc.http import HttpClient
from omni_sdk.contracts.client import ContractClient
import json

cfg = Config(rpc_url="$RPC_URL", chain_id=int("$CHAIN_ID"))
rpc = HttpClient(cfg)
abi = json.load(open("contracts/examples/quantum_rng/manifest.json"))["abi"]
code = open("contracts/examples/quantum_rng/contract.py","rb").read()

# Use your wallet signer (omitted for brevity), build deploy tx, send...
# Then instantiate client once the address is known:
c = ContractClient(rpc=rpc, address="<DEPLOYED_ADDRESS>", abi=abi)

3) Request and consume randomness

Using the call helper

# Enqueue (returns task_id)
python -m contracts.tools.call \
  --rpc "$RPC_URL" --chain-id "$CHAIN_ID" \
  --manifest contracts/examples/quantum_rng/manifest.json \
  --address <DEPLOYED_ADDRESS> \
  --fn request --args '{"bits":256,"shots":256,"trap_rate":16}'
# => prints task_id (0x… or bytes32)

# Wait one block (miners must be running), then poll:
python -m contracts.tools.call \
  --rpc "$RPC_URL" --chain-id "$CHAIN_ID" \
  --manifest contracts/examples/quantum_rng/manifest.json \
  --address <DEPLOYED_ADDRESS> \
  --fn poll --args '{"task_id":"0x..."}'
# => {"ready": true, "out": "0x..."}

Using SDK (Python) sketch

task_id = c.call("request", {"bits":256,"shots":256,"trap_rate":16})
# Wait one block…
ready, out = c.call("poll", {"task_id": task_id})
if ready:
    print("Random bytes:", out)
print("Last:", c.call("last", {}))


⸻

Gas & costs
	•	Enqueue (request): bounded by payload size (circuit descriptor) and
syscall base cost. The example caps bits/shots to avoid abuse.
	•	Fulfillment is off-chain; the on-chain acceptance of the proof is under
consensus rules (PoIES). Your contract execution for poll is tiny (read).
	•	Mixing with the beacon or commit-reveal increases on-chain cost slightly
but improves adversarial robustness.

⸻

Determinism & safety
	•	The task_id derivation is deterministic and ties the request to specific
chain state and caller—see capabilities/jobs/id.py.
	•	Reads are time-gated: poll never returns a value in the same block as
request. Expect at least one block latency.
	•	The example does not attempt to slash or price providers; that’s handled
by AICF (staking/SLA). You can query AICF separately if you want provider
metadata when showing UI hints.

⸻

Recommended patterns (production)
	•	Bias resistance: XOR quantum bytes with the chain beacon for the same
round, or require a user commit-reveal as an input.
	•	Limit surface: Enforce per-address request caps and charge fees (e.g., a
protocol fee via your own treasury).
	•	Audit trail: Emit the task_id and, optionally, the beacon round used
for mixing. This helps off-chain verifiers and explorers.

⸻

Troubleshooting
	•	poll returns (False, "") indefinitely
	•	The node may not be mining/advancing; ensure a miner is running.
	•	The provider didn’t submit a valid proof; check node logs for QuantumProof
verification failures.
	•	High gas for request
	•	Reduce bits/shots, or raise trap_rate (fewer traps).
	•	Addressing
	•	The network uses bech32m anim1… or 0x… (hex) depending on tooling;
both are supported by the call tool and SDK.

⸻

Files in this example
	•	contract.py – The contract source using the capabilities runtime:
quantum.enqueue(...) and read_result(...) patterns.
	•	manifest.json – ABI + metadata used by the SDK and tools.
	•	(Optional) local tests: see contracts/examples/ai_agent/tests_local.py for a
similar pattern with next-block reads; you can adapt for quantum_rng.

⸻

Security disclaimer

This is a demonstration. Quantum providers are verified by trap-based
proofs and SLA, but a determined adversary may still attempt to bias outcomes
(e.g., by withholding). Robust applications should mix sources and design
their state machines to be fail-safe on missing/late results.

