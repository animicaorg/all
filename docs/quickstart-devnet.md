# Quickstart: devnet stack in one shell

This walkthrough takes a fresh checkout from clone → devnet → mining → dashboard with as few commands as possible.

## Prerequisites
- Python 3.10+ and `python -m venv`
- `pip`
- `pnpm` for the dashboard frontend

## 1) Clone & install dependencies
```bash
git clone https://github.com/animicaorg/all.git
cd all
python -m venv .venv
source .venv/bin/activate
python -m pip install -e "python[stratum]"
```

The editable install exposes the Animica console scripts such as `animica-node`, `animica-wallet`, `animica-pool`, and `animica-p2p`.

## 2) Start the devnet stack
With the virtual environment active:
```bash
ops/run.sh --profile devnet all
```

`all` launches the RPC node and Stratum pool in the background, then runs the miner dashboard (Vite dev server). The default bindings come from `ops/profiles/devnet.env`:
- `ANIMICA_NETWORK=devnet`
- `ANIMICA_RPC_URL=http://127.0.0.1:8545/rpc`
- `ANIMICA_STRATUM_BIND=0.0.0.0:3333`
- `ANIMICA_POOL_API_BIND=0.0.0.0:8550`
- `ANIMICA_RPC_DB_URI`, `ANIMICA_MINING_POOL_DB_URL`, and devnet P2P seeds

Once `pnpm dev` starts, open http://localhost:5173 to see the dashboard. Use `Ctrl+C` in that terminal to stop the dashboard; the script cleans up the background node and pool.

## 3) Mine a block with the CPU miner
In a new terminal (with `.venv` activated), point the built-in miner at the devnet RPC endpoint:
```bash
python -m mining.cli.miner start \
  --threads 2 \
  --device cpu \
  --rpc-url http://127.0.0.1:8545/rpc \
  --ws-url ws://127.0.0.1:8545/ws \
  --target 0.25
```

Leave the miner running until you see shares accepted and a block mined.

## 4) Verify the block in the explorer
The miner dashboard includes an explorer panel pointed at the devnet RPC. Refresh the page at http://localhost:5173 and confirm the reported tip height increased. You can double-check via the CLI:
```bash
animica-node head
```

When finished, stop the miner with `Ctrl+C` and exit the dashboard terminal to tear down the devnet services.
