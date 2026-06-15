# Animica Python toolbox

This directory packages the Python utilities that live under `animica/`,
including data-availability helpers, mempool policy tests, and the
stratum pool prototype. Installing it as a Python package allows tools
and tests elsewhere in the repo to import `animica` modules directly.

## Mine & earn — one command

```bash
pip install --upgrade animica

# Runs EVERYTHING, bound to your Animica address (auto-creates a wallet if you
# have none): SHA3 mining + ENA useful-work, plus model training + serving on a
# GPU, plus Bittensor serving on a qualified GPU (>=16 GB VRAM). Joins the pool
# and the one global model. Every reward — PoW, useful-work, training, serving,
# Bittensor — pays out in ANM to your address.
animica up

animica up --plan          # show exactly what will run on this machine first
animica up --pool-host pool.animica.org --pool-id <pool>   # target a pool
```

`animica up` advertises miner version **1.0.0**; the pool rejects older miners,
so keep it upgraded. Qualified GPUs (>=16 GB VRAM) also serve Bittensor, with all
earnings bound to ANM (no external TAO/XMR payout). Full details:
https://pool.animica.org/mining-onboard

<details><summary>Component commands (advanced — <code>animica up</code> runs these for you)</summary>

```bash
animica miner dual-mine <anm-address> --pool-host pool.animica.org  # PoW only
animica ena worker start --worker-id <id>                           # useful-work
animica ena pool serve <pool-id> --worker-id <id>                   # serve a model
animica bittensor overview                                          # Bittensor pool status
```
</details>

## Installation

From the repository root you can install the package in editable mode:

```bash
python -m pip install -e "python[operator,dev]"
```

### Optional extras

- Base package: now includes the backend runtime dependencies required by
  `rpc.server`, the ENA node, and the Stratum pool (`fastapi`,
  `uvicorn[standard]`, `prometheus-client`).
- `backend`, `ena`, `stratum`, `operator`: compatibility aliases kept for
  operator/install scripts and older docs. They resolve to the same runtime
  dependency set as the base package.
- `dev`: pytest, mypy, ruff, respx, and other local development tools.

Example with extras:

```bash
python -m pip install -e "python[stratum,dev]"
```

### Stratum pool runtime

Preferred operator path:

```bash
animica stratum up --daemon --profile asic_sha256 --rpc-url http://127.0.0.1:8545/rpc
animica stratum status
animica stratum down
```

Lower-level entrypoint:

```bash
python -m animica.stratum_pool --profile asic_sha256
```

### Validation helpers

The repo now ships executable smoke helpers for the repaired setup/runtime path:

```bash
./scripts/smoke_backend_imports.sh
./scripts/smoke_ena.sh
./scripts/smoke_stratum.sh
./scripts/smoke_setup_install.sh
```
