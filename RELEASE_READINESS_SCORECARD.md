# Release Readiness Scorecard

Date: 2026-04-07

Overall readiness: 32 / 100

This score reflects that core backend truth is improving, but major product surfaces remain unvalidated or currently failing.

| Area | Score | Notes |
| --- | --- | --- |
| Setup and test harness | 4 / 5 | `setup.sh` now installs backend runtime deps and key backend suites run cleanly |
| Docker build hygiene | 3 / 5 | Context bloat fixed; explorer runtime path still blocked |
| Core node and sync CLI | 4 / 5 | `78 passed` in focused node and sync CLI suites |
| P2P and sync backend | 3 / 5 | Supervisor and sync completion tests pass; live convergence smoke still missing |
| Wallet and tx truthfulness | 3 / 5 | Wallet pending reservation accounting fixed; focused wallet and tx smoke passes |
| Explorer surfaces | 2 / 5 | `explorer-web` unit smoke passes, but ops runtime and live chain proof are still open |
| Studio surfaces | 1 / 5 | Focused provider smoke is red |
| Exchange surfaces | 1 / 5 | Admin web type-check is red; CEX e2e build is red |
| Wallet extension and GUIs | 1 / 5 | Not stabilized in this pass |
| Release docs and smoke tooling | 3 / 5 | Root audit docs now exist; sync and wallet smoke are green, frontend and exchange smoke are still red |

## Iteration 2026-04-07 A

### What was broken

- Setup, Docker, and CLI truth surfaces were inconsistent.

### Root cause

- Packaging drift, ops drift, and stale tests.

### Files changed

- `.dockerignore`
- `python/pyproject.toml`
- `setup.sh`
- `pytest.ini`
- `ops/docker/explorer.Dockerfile`
- `ops/docker/tests/test_compose_port_bindings.py`
- `python/animica/cli/node.py`
- `python/animica/cli/sync.py`
- `python/animica/cli/tests/test_node_cli.py`
- `python/animica/cli/tests/test_sync_cli.py`
- `p2p/tests/__init__.py`
- `rpc/deps.py`

### Tests added or updated

- Node and sync CLI tests
- Compose binding tests

### Validation run

- `pytest -q python/animica/cli/tests/test_node_cli.py python/animica/cli/tests/test_sync_cli.py`
- `pytest -q p2p/tests/test_sync_completion_status.py rpc/tests/test_p2p_supervisor.py`
- `pytest -q ops/docker/tests/test_compose_port_bindings.py`

### Remaining risks

- Explorer, studio, and exchange surfaces remain below RC standard.

## Iteration 2026-04-07 B

### What was broken

- Wallet pending outgoing accounting lied after canonical load.

### Root cause

- Runtime pending transaction metadata was dropped by wallet parsing.

### Files changed

- `python/animica/wallet/serialization.py`
- `python/animica/cli/tests/test_wallet_serialization.py`

### Tests added or updated

- Wallet serialization preservation test

### Validation run

- `pytest -q python/animica/cli/tests/test_wallet_show_output.py python/animica/cli/tests/test_wallet_serialization.py python/animica/cli/tests/test_tx_value_conversion.py rpc/tests/test_tx_canonical_serialization.py rpc/tests/test_tx_sendraw_params.py`

### Remaining risks

- Full wallet e2e and wallet extension parity are not yet proven.
