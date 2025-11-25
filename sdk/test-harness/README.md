# Animica SDK — End-to-End (E2E) Test Harness

This harness runs a **single, canonical flow** across the Python, TypeScript, and Rust SDKs against a live node (local **devnet** or an already-running node):

1) **Deploy** the canonical `counter` contract (from shared fixtures)  
2) **Call** methods (`inc`, `get`) and verify deterministic results/events  
3) **(Optional)** Exercise DA/AICF/Randomness quick sanity calls when enabled

The harness is language-agnostic and reuses the *same* ABI/manifest and funded test accounts to ensure cross-SDK parity.

---

## Directory layout

sdk/test-harness/
├─ devnet_env.py                  # (helper) Spin up a local devnet or attach to running node
├─ contracts/
│  └─ counter/
│     ├─ contract.py             # Canonical Counter contract (Python-VM)
│     └─ manifest.json           # ABI + deploy metadata
├─ run_e2e_py.py                 # E2E via Python SDK
├─ run_e2e_ts.mjs                # E2E via TypeScript SDK (Node)
├─ run_e2e_rs.sh                 # E2E via Rust SDK (binary example)
├─ fixtures/
│  └─ accounts.json              # Pre-funded dev keys for tests (DO NOT use on real nets)
└─ ci_matrix.yml                 # Example CI job matrix (multi-language)

> **Note**: Paths above are referenced by the scripts; keep them as-is unless you also update the scripts.

---

## Prerequisites

- A running **Animica node** (local devnet recommended) that exposes:
  - **HTTP JSON-RPC** (e.g. `http://127.0.0.1:8545`)
  - **WebSocket** (e.g. `ws://127.0.0.1:8546`) for subscription tests
- Python **3.10+**
- Node.js **18+** and npm
- Rust **1.74+** (stable) with `cargo`

If you don’t have a node running, see `devnet_env.py --help` for a lightweight local bring-up (uses the repo’s canonical genesis and fixtures). You can also connect to an existing endpoint by setting the env vars below.

---

## Environment variables

All runners accept the following (with sensible defaults if omitted):

- `RPC_URL` — HTTP RPC endpoint (default `http://127.0.0.1:8545`)
- `WS_URL`  — WebSocket endpoint (default derived from `RPC_URL`)
- `CHAIN_ID` — Integer chain id (default read via `chain.getChainId`)

Language-specific overrides:
- **Python**: `OMNI_SDK_RPC_URL`, `OMNI_SDK_CHAIN_ID` (override the per-client opts)
- **TypeScript**: pass via CLI flags in `run_e2e_ts.mjs` or set `RPC_URL`
- **Rust**: env picked up by examples or flags forwarded by `run_e2e_rs.sh`

---

## One-time setup

From repository root:

### Python SDK
```bash
# install the Python SDK in editable mode
pipx run pip --version >/dev/null 2>&1 || python -m pip --version
python -m venv .venv && . .venv/bin/activate
pip install -U pip
pip install -e sdk/python

TypeScript SDK

pushd sdk/typescript
npm ci
npm run build
popd

Rust SDK

cargo build -p animica-sdk


⸻

Start (or attach to) a devnet

Option A — Attach to your running node (skip if already running):

export RPC_URL=http://127.0.0.1:8545
export WS_URL=ws://127.0.0.1:8546

Option B — Bring up a local devnet (helper script):

# This helper prints the chosen RPC/WS URLs and CHAIN_ID
python sdk/test-harness/devnet_env.py up
# To stop later:
# python sdk/test-harness/devnet_env.py down


⸻

Run the cross-language E2E flows

Python

. .venv/bin/activate
RPC_URL=${RPC_URL:-http://127.0.0.1:8545} \
CHAIN_ID=${CHAIN_ID:-0} \
python sdk/test-harness/run_e2e_py.py

TypeScript (Node)

node sdk/test-harness/run_e2e_ts.mjs \
  --rpc ${RPC_URL:-http://127.0.0.1:8545} \
  --chain ${CHAIN_ID:-0}

Rust

# The script builds and runs the example with env propagated
RPC_URL=${RPC_URL:-http://127.0.0.1:8545} \
CHAIN_ID=${CHAIN_ID:-0} \
bash sdk/test-harness/run_e2e_rs.sh

On success, each runner will print:
	•	the deploy tx hash and contract address
	•	gas usage stats (where available)
	•	return value from get after a sequence of inc calls
	•	decoded event logs (topics/data) matching the shared ABI

⸻

What the harness validates
	•	CBOR encoding / SignBytes domain for deploy/call
	•	PQ signing pipelines (Dilithium3 / SPHINCS+) through the SDKs
	•	Receipt decoding and deterministic logs/bloom hashing
	•	WS subscriptions (newHeads, events) basic stability
	•	Cross-SDK parity: identical selector/topic ids and ABI encoding

Optional checks (enabled when the corresponding services/nodes are available):
	•	DA: pin a small blob, read back, verify commitment
	•	AICF: enqueue a tiny AI job and poll for a result reference
	•	Randomness: fetch current beacon / light proof

⸻

Test accounts & safety

sdk/test-harness/fixtures/accounts.json contains pre-funded throwaway keys for local devnet only.
	•	Never use them on public networks.
	•	The Python/TS/Rust flows derive the same addresses for consistency.

⸻

CI usage

See sdk/test-harness/ci_matrix.yml for a minimal matrix:
	•	Boot a devnet (job 1)
	•	Run Python E2E
	•	Run TypeScript E2E
	•	Run Rust E2E

Artifacts (logs, tx traces) can be uploaded for debugging.

⸻

Troubleshooting
	•	Connection refused / timeouts
Check RPC_URL/WS_URL. Ensure the node is reachable and CORS allows local tests.
	•	CHAIN_ID mismatch
The harness queries chain.getChainId. If you supply CHAIN_ID, ensure it matches.
	•	PQ signer not available
Python/TS fall back gracefully where possible. Ensure optional PQ libs (e.g., liboqs/WASM) are enabled if you want hardware-backed performance. The test harness only requires functional sign/verify.
	•	Receipt missing / pending forever
Confirm your devnet is mining/producing blocks. The Python/TS/Rust clients default to polling and/or WS subscriptions.

⸻

Extending the harness
	1.	Drop a new example contract under contracts/<name>/.
	2.	Reference it from a new runner (clone run_e2e_*) and add assertions.
	3.	If you generate clients from ABI, use the codegen tools:
	•	Python: python -m sdk.codegen.cli --lang py --abi <abi.json> --out .
	•	TS (npx): npx animica-codegen --abi <abi.json> --out .

Keep flows hermetic: use shared fixtures and avoid external dependencies.

⸻

License

This harness is part of the Animica SDK and is provided under the repo’s license.

