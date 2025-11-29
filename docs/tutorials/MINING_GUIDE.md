# Mine Animica locally (devnet or pool)

This guide walks through a full local mining setup: install dependencies, start a devnet with RPC/WS, run the built-in miner (CPU or GPU), and optionally expose Stratum so external miners can connect. All commands are copy-pasteable and default to localhost.

---

## 0) Prerequisites

- **Python 3.10+** (system Python or a virtual environment)
- **Git** and build tooling for Python packages (Rust toolchain optional for native speedups)
- Optional: GPU drivers/runtime if you want to try `--device cuda|rocm|opencl|metal`

Clone the repo and enter it:

```bash
git clone https://example.com/animica/all.git animica
cd animica
```

⸻

## 1) Install mining and RPC dependencies

Use a virtualenv if you prefer, then install the local packages needed for mining and the RPC server:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e core -e rpc -e mining
```

> If you plan to compile/execute contracts while mining, also install `-e vm_py` and `-e sdk/python`.

⸻

## 2) Bootstrap a devnet and RPC server

Initialize a fresh database from the bundled genesis, export it for the RPC server, then start the RPC/WS surfaces:

```bash
python -m core.boot \
  --genesis core/genesis/genesis.json \
  --db sqlite:///animica_dev.db

export ANIMICA_DB=sqlite:///animica_dev.db
python -m rpc.server --host 127.0.0.1 --port 8545
```

- HTTP JSON-RPC: `http://127.0.0.1:8545/rpc`
- WebSocket hub: `ws://127.0.0.1:8546/ws` (same base host/port; auto-bound by the server)

Leave this terminal running so the node advances and serves work templates.

⸻

## 3) Run the built-in miner (CPU or GPU)

Start the miner in another terminal. The defaults bind to the local RPC/WS endpoints; set `--device` to choose your backend:

```bash
source .venv/bin/activate  # if you created one
python -m mining.cli.miner \
  --threads 4 \
  --device cpu \
  --rpc http://127.0.0.1:8545 \
  --ws ws://127.0.0.1:8546
```

Expected output (truncated):

```text
INFO mining.templates refreshing work from head height=0 theta=…
INFO mining.hash_search found share d_ratio_ppm=712345 nonce=0x...
INFO mining.share_submitter accepted share template=…
```

Switch to a GPU by changing `--device cuda` (or `rocm|opencl|metal`) when the corresponding backend and drivers are available.

Handy environment overrides (alternatives to CLI flags):

- `MINER_THREADS`, `MINER_DEVICE`, `MINER_TARGET_SHARES_PER_SEC`
- `ANIMICA_RPC_URL`, `ANIMICA_WS_URL`, `ANIMICA_CHAIN_ID`

⸻

## 4) Inspect current work (one-shot)

Use the lightweight helper to print the current mining template via JSON-RPC:

```bash
python -m mining.cli.getwork --rpc-url http://127.0.0.1:8545 --chain-id 1 --pretty
```

Output is a JSON object containing `parentHash`, `thetaMicro`, `gammaCap`, `nonceDomain.mixSeed`, and coinbase data bound to the current head.

⸻

## 5) Expose Stratum for external miners

Bridge the node’s getWork API to Stratum v1 so third-party miners can connect:

```bash
python -m mining.cli.stratum_proxy start \
  --rpc-url http://127.0.0.1:8545 \
  --ws-url ws://127.0.0.1:8546/ws \
  --listen 0.0.0.0:3333 \
  --poll-interval 1.5
```

Point a Stratum miner at the proxy (example URI; adjust worker/address):

```bash
miner --url stratum+tcp://127.0.0.1:3333 --worker rig-1 --address anim1...
```

The proxy subscribes to new work over WS (with HTTP polling as a fallback) and forwards submitted shares back to the node.

⸻

## 6) Tuning and troubleshooting

- **Adaptive share target:** the miner auto-tunes share difficulty to keep submissions steady; deeper shares (`d_ratio_ppm` closer to Θ) are preferred when assembling blocks.
- **Proof attachments:** if AI/Quantum/Storage/VDF workers are running locally, the miner will attach verified proofs that increase Σψ and make block sealing easier.
- **Metrics:** scrape `/metrics` from the RPC server to watch `animica_miner_hashrate_shares_per_sec`, `animica_miner_shares_rejected_total{reason=…}`, and block seals.
- **Reset state:** stop the miner and RPC server, delete `animica_dev.db`, and rerun the bootstrap commands to start fresh.
