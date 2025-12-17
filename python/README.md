# Animica Python toolbox

This directory packages the Python utilities that live under `animica/`,
including data-availability helpers, mempool policy tests, and the
stratum pool prototype. Installing it as a Python package allows tools
and tests elsewhere in the repo to import `animica` modules directly.

## Installation

From the repository root you can install the package in editable mode:

```bash
python -m pip install -e python
```

### Optional extras

- `stratum`: pull in the FastAPI + Uvicorn dependencies required for the
  `animica.stratum_pool` service.
- `dev`: install pytest for running the bundled test suite.

Example with extras:

```bash
python -m pip install -e "python[stratum,dev]"
```

### Stratum pool runtime

Run the pool via ``python -m animica.stratum_pool`` (or ``animica.mining.pool``) and
use ``--rpc-timeout``/``ANIMICA_STRATUM_RPC_TIMEOUT`` (falls back to
``ANIMICA_RPC_TIMEOUT``) to stretch JSON-RPC deadlines when pointing at slower
remote endpoints.
