# Master Release Audit

Date: 2026-04-07

## Scope

This audit replaces wishful summary files with executable evidence gathered directly from the repo on 2026-04-07.

Primary repo map used in this pass:

- Core chain: `rpc/`, `p2p/`, `mempool/`, `mempool2/`, `mining/`, `python/animica/cli/`
- Wallets and GUIs: `wallet/`, `wallet-qt/`, `wallet-extension/`, `apps/wallet-extension/`, `apps/miner-gui/`, `apps/miner-dashboard/`
- Explorer and web: `explorer2/`, `explorer-web/`, `web/`, `website/`, `apps/admin-web/`
- Studio and useful work: `studio-web/`, `studio-services/`, `studio-wasm/`, `apps/animica_studio/`, `aicf/`, `ena/`, `da/`
- Exchange and token surfaces: `cex/`, `contracts/`, `sdk/`
- Ops and release: `ops/`, `tests/`, `docs/`, `spec/`

## Current Snapshot

| Area | Status | Evidence |
| --- | --- | --- |
| Setup and test harness | Improved | `setup.sh` now installs backend runtime deps from `requirements.txt`; key CLI suites pass |
| Docker build hygiene | Improved | `.dockerignore` cut build context from multi-GB to about 4.68 MB |
| Node and sync CLI truthfulness | Green in focused scope | `pytest -q python/animica/cli/tests/test_node_cli.py python/animica/cli/tests/test_sync_cli.py` -> `78 passed` |
| Wallet pending reservation accounting | Fixed | `wallet show` pending outgoing now survives canonical wallet load; focused wallet and tx smoke passes |
| Explorer ops packaging | Partially fixed, still blocked | `ops/docker/explorer.Dockerfile` no longer fails on non-root script install, but runtime still points at `explorer.api:app` with no matching module found |
| Frontend surfaces | Mixed | `explorer-web` unit smoke passes; `studio-web` provider smoke fails; `apps/admin-web` type-check fails |
| Exchange surfaces | Blocked | `npm --prefix cex/tests/e2e run build` fails with `tsc: not found` |

## Validated Commands

- `pytest -q ops/docker/tests/test_compose_port_bindings.py` -> `8 passed`
- `pytest -q p2p/tests/test_sync_completion_status.py rpc/tests/test_p2p_supervisor.py` -> `5 passed`
- `pytest -q python/animica/cli/tests/test_node_cli.py python/animica/cli/tests/test_sync_cli.py` -> `78 passed`
- `pytest -q python/animica/cli/tests/test_wallet_show_output.py python/animica/cli/tests/test_wallet_serialization.py python/animica/cli/tests/test_tx_value_conversion.py rpc/tests/test_tx_canonical_serialization.py rpc/tests/test_tx_sendraw_params.py` -> `18 passed`
- `npm --prefix explorer-web test -- test/unit/sync.test.ts` -> `11 passed`
- `npm --prefix studio-web test -- test/unit/provider.test.ts` -> `2 failed`
- `npm --prefix apps/admin-web run type-check` -> `3 TypeScript errors`
- `npm --prefix cex/tests/e2e run build` -> `tsc: not found`
- `./scripts/smoke_sync_e2e.sh` -> `91 passed`
- `./scripts/smoke_wallet_and_tx.sh` -> `18 passed`
- `./scripts/smoke_frontend_surfaces.sh` -> fails on `studio-web` provider smoke
- `./scripts/smoke_exchange_surfaces.sh` -> fails on `apps/admin-web` and `cex/tests/e2e`

## Iteration 2026-04-07 A: Foundational Truth Pass

### What was broken

- Fresh backend/test environments were missing runtime packages needed by repo RPC and P2P tests.
- Docker build contexts were bloated by repository junk and local artifacts.
- `ops/docker/explorer.Dockerfile` tried to write `/usr/local/bin/start-explorer` after switching to a non-root user.
- `animica sync status` and related cache readers could crash on read-only paths because read helpers still created directories.
- Restricted environments could fail P2P sync tests because the fallback test allocator assumed loopback bind access.
- Several node and sync CLI tests were asserting outdated internal call shapes and pre-refactor command layouts.

### Root cause

- Setup and packaging drift: editable install of `python/` did not cover repo runtime dependencies.
- Ops drift: Docker packaging and compose assertions no longer matched the shipped security posture and container user model.
- Status/cache drift: read paths and persistence paths were not separated cleanly.
- Test drift: suites were validating old internal implementation details instead of user-visible contracts.

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

- Node CLI tests updated to assert bounded retry behavior and current compose shutdown behavior.
- Sync CLI tests updated to use follow mode for stall and progress monitoring assertions.
- Compose port binding tests updated to reflect intentional loopback-only RPC exposure.
- P2P tests hardened for restricted CI and sandbox environments.

### Validation run

- `pytest -q ops/docker/tests/test_compose_port_bindings.py`
- `pytest -q p2p/tests/test_sync_completion_status.py rpc/tests/test_p2p_supervisor.py`
- `pytest -q python/animica/cli/tests/test_node_cli.py python/animica/cli/tests/test_sync_cli.py`

### Remaining risks

- No live two-node leader/follower sync e2e was run in this pass.
- Explorer Docker runtime is still mismatched with the actual explorer codebase.
- Frontend, GUI, and exchange surfaces remain only partially audited.

## Iteration 2026-04-07 B: Wallet Truthfulness Pass

### What was broken

- `animica wallet show` reported `pending_outgoing = 0` even when the wallet file contained active locally reserved pending transactions.

### Root cause

- Canonical wallet parsing normalized wallet entries but silently dropped `pending_txs`, so locally tracked reservations disappeared on load before `wallet show` computed available balance.

### Files changed

- `python/animica/wallet/serialization.py`
- `python/animica/cli/tests/test_wallet_serialization.py`

### Tests added or updated

- Added a serialization test that preserves `pending_txs` through canonical parse.
- Existing wallet show output tests now pass without loosening their behavior contract.

### Validation run

- `pytest -q python/animica/cli/tests/test_wallet_show_output.py python/animica/cli/tests/test_wallet_serialization.py`
- `pytest -q python/animica/cli/tests/test_wallet_show_output.py python/animica/cli/tests/test_wallet_serialization.py python/animica/cli/tests/test_tx_value_conversion.py rpc/tests/test_tx_canonical_serialization.py rpc/tests/test_tx_sendraw_params.py`

### Remaining risks

- This fixes local pending reservation accounting, not full wallet send/receive e2e.
- Wallet extension surfaces are still not aligned to the current backend contract.

## Highest Priority Next Blockers

1. Align explorer ops packaging with the actual explorer runtime and prove the explorer reads live chain state.
2. Reproduce and fix the `studio-web` provider contract failure.
3. Reproduce and fix `apps/admin-web` type drift so the exchange admin surface builds again.
4. Turn current sync smokes into a live leader/follower e2e with block convergence proof.
5. Audit wallet extension raw transaction submission against current RPC behavior.
