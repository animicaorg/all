# Devnet spinup: node1 container exits immediately with code 0, causing spin_all.sh to fail

## Summary
The devnet stack now ships with an explicit node entrypoint and a documented env file. Copy the example env, then run the spin scripts; `node1` remains running (or surfaces a clear error) instead of exiting immediately with code 0.

## Environment
- Host: Ubuntu 24.04 (root on a VPS)
- Repo: https://github.com/animicaorg/all
- Branch: main
- Directory: /root/animica
- Command used to start devnet:

```bash
cd /root/animica
./ops/spinup/spin_all.sh
```

## Expected behavior
- `./ops/spinup/spin_all.sh` builds the devnet images and brings up:
  - `animica-node1` (primary node)
  - `animica-node2`
  - `animica-miner`
  - `animica-studio-services`
  - `animica-explorer`
- `node1` stays running as a long-lived node process (listening on the configured RPC / P2P ports), so the miner, explorer, and services all remain healthy.

## Fix
- `tests/devnet/node-entry.sh` now handles devnet node startup: it validates `GENESIS_PATH`, initializes the SQLite DB from genesis when missing, and execs `python -m rpc.server` as the `animica` user. Any missing/invalid config causes a clear, non-zero exit.
- `tests/devnet/env.devnet.example` documents all required variables (chain ID, ports, DB URIs, genesis path, miner defaults, logging). Copy it to `.env` to override defaults.
- `tests/devnet/docker-compose.yml` wires the env defaults and uses the new entrypoint for both nodes, so `node1` stays running (or surfaces a useful error).
- `ops/spinup/common.sh` now passes the env files to compose automatically, making `./ops/spinup/spin_all.sh` and `spin_nodes.sh` work from a fresh clone.

## How to run the devnet now

1) From a fresh clone, copy and adjust the env file:

```bash
cd /root/animica
cp tests/devnet/env.devnet.example tests/devnet/.env   # optional edits: ports, faucet key, miner threads
```

2) Start the full stack (nodes + miner + studio-services + explorer) with logs streamed to `logs/spinup/spin_all.log`:

```bash
./ops/spinup/spin_all.sh
```

3) Expected endpoints once health checks pass:

- RPC (HTTP): http://localhost:8545
- RPC (WS):   ws://localhost:8545/ws
- Explorer:   http://localhost:5173
- Studio-services API: http://localhost:8787

If `node1` is misconfigured, `spin_all.sh` prints the last 200 lines of its logs and exits non-zero so the failure is visible.
