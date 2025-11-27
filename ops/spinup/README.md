# Spinup scripts

This directory contains Bash helpers for starting Animica devnet components with clear logging. Each script writes to `logs/spinup/*.log` (override with `LOG_DIR`). All scripts assume Docker Compose and target the dev profile defined in `tests/devnet/docker-compose.yml`.

## Scripts

- `spin_nodes.sh`: Start `node1` and `node2` (RPC/WS) from the compose stack.
- `spin_miner.sh`: Start the CPU miner (automatically brings up `node1`).
- `spin_web.sh`: Start the web-facing pieces (`services` + `explorer`), including their dependencies.
- `spin_all.sh`: Bring up the full devnet stack (nodes, miner, studio-services, explorer) in one go.

## Logging

Logs are timestamped and appended to `logs/spinup/<script>.log`. Pass `LOG_DIR=/custom/path` to place logs elsewhere. Compose output is streamed directly to the log file for postmortem inspection.

## Usage

From the repo root:

```bash
./ops/spinup/spin_nodes.sh          # nodes only
./ops/spinup/spin_miner.sh          # miner + node1
./ops/spinup/spin_web.sh            # services + explorer (and dependencies)
./ops/spinup/spin_all.sh            # everything
```

All scripts run with `set -euo pipefail`. Use `Ctrl+C` to stop the attached compose session; `docker compose -f tests/devnet/docker-compose.yml --profile dev down` removes the stack.
