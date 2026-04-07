# Setup And Dependency Audit

## Summary

The broken setup flow was real and reproducible:

- `setup.sh` expected `fastapi` and `prometheus_client` during verification.
- `python/pyproject.toml` did not declare those backend runtime dependencies in
  the base package metadata.
- `python/README.md` told operators to run `pip install -e python`, which left
  backend imports unresolved unless `requirements.txt` or `python[dev]` happened
  to be installed separately.
- `setup.sh` also hard-failed on `pip/setuptools/wheel` self-upgrade in offline
  or restricted environments before package installation even began.

## Proven Root Cause

- `pip show animica` before the fix exposed only core CLI deps and omitted
  `fastapi`, `uvicorn`, and `prometheus-client`.
- Backend verification in `setup.sh` therefore depended on side effects from
  `requirements.txt` and `python[dev]`, not from the package metadata itself.
- Repo-local runtime imports also revealed adjacent breakage:
  - `rpc.server` imported `rpc.methods.phase2`, which pulled `method` from
    `rpc.jsonrpc` and triggered a circular import.
  - `ena.services.ena_node.main` configured `FileHandler(Config.LOG_FILE)`
    before the log directory existed and crashed on import.

## Fixes Applied

- Moved backend runtime dependencies into `python/pyproject.toml` base
  `project.dependencies`.
- Added compatibility extras: `backend`, `ena`, `stratum`, `operator`.
- Kept `dev` focused on development/test tooling.
- Updated `setup.sh` to install `python[operator,dev]`.
- Made `setup.sh` tolerate offline `pip/setuptools/wheel` bootstrap failure.
- Expanded install verification to import:
  - `fastapi`
  - `prometheus_client`
  - `rpc.server`
  - `ena.services.ena_node.main`
  - `animica.stratum_pool.cli`
- Fixed the RPC circular import in `rpc/methods/phase2.py`.
- Added smoke helpers:
  - `scripts/smoke_backend_imports.sh`
  - `scripts/smoke_setup_install.sh`

## Files Changed

- `python/pyproject.toml`
- `setup.sh`
- `rpc/methods/phase2.py`
- `python/README.md`
- `scripts/smoke_backend_imports.sh`
- `scripts/smoke_setup_install.sh`

## Validation

```bash
./scripts/smoke_backend_imports.sh
./scripts/smoke_setup_install.sh
PYTHONPATH=/root/animica/python:/root/animica .venv/bin/pytest -q \
  python/animica/tests/test_backend_runtime_imports.py \
  ena/tests/test_ena_node_service.py \
  python/animica/tests/test_ena_e2e_smoke.py \
  ena/tests/test_model_registry.py \
  ena/tests/test_rate_limiter.py
```

## Remaining Risks

- Fresh installs still require network access the first time dependencies are
  resolved from PyPI.
- `ena/` and `rpc/` remain repo-local modules rather than separately packaged
  distributions, so source-checkout workflows remain the supported path.
- FastAPI `on_event` and Pydantic v1-style validators still emit deprecation
  warnings and should be modernized before a strict production release.
