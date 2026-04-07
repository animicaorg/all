# Animica Python toolbox

This directory packages the Python utilities that live under `animica/`,
including data-availability helpers, mempool policy tests, and the
stratum pool prototype. Installing it as a Python package allows tools
and tests elsewhere in the repo to import `animica` modules directly.

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
