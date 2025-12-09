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

**Note:** If you run `./setup.sh` from the repository root, it automatically installs the package with both `dev` and `stratum` extras, so the Stratum pool functionality (including `animica miner run-pool`) is available immediately without additional steps.

### Optional extras

- `stratum`: pull in the FastAPI + Uvicorn dependencies required for the
  `animica.stratum_pool` service. **Installed by default via setup.sh.**
- `dev`: install pytest for running the bundled test suite. **Installed by default via setup.sh.**

Example with extras:

```bash
python -m pip install -e "python[stratum,dev]"
```
