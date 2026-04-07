# Release Blockers

Date: 2026-04-07

## Prioritized Blockers

| Priority | Blocker | Status | Evidence |
| --- | --- | --- | --- |
| P0 | Explorer ops runtime points at a non-existent app path | Open | `ops/docker/entrypoints/explorer.sh` defaults to `explorer.api:app`; no matching module was found in the current codebase |
| P0 | Studio web provider surface is contract-drifted | Open | `npm --prefix studio-web test -- test/unit/provider.test.ts` fails because provider helpers return promises where tests expect synchronous provider access |
| P0 | Exchange admin web does not type-check | Open | `npm --prefix apps/admin-web run type-check` fails with `AdminRole` and `Admin` shape mismatches in `src/contexts/AuthContext.tsx` |
| P0 | CEX e2e harness is not build-ready in the current workspace | Open | `npm --prefix cex/tests/e2e run build` fails with `tsc: not found` |
| P1 | No live leader/follower sync convergence smoke exists in the new RC harness | Open | Current proof is limited to CLI and supervisor tests |
| P1 | Wallet extension backend contract is unvalidated against current RPC behavior | Open | Focused wallet extension smoke has not been stabilized in this pass |
| P1 | Explorer truth against live backend is not yet proven | Open | Only explorer-web unit smoke was run |

## Resolved or Reduced This Iteration

- Backend runtime dependency drift in `setup.sh`
- Docker build context bloat due missing `.dockerignore`
- Explorer Dockerfile non-root script installation failure
- Sync CLI cache path write-on-read bug
- Wallet `pending_outgoing` accounting losing `pending_txs` at load time
- Node and sync CLI test drift around retry loops and shutdown command shape

## Iteration 2026-04-07 A

### What was broken

- Setup and Docker drift hid real backend and ops failures behind missing dependencies and noisy build contexts.
- Sync and node status tests were failing against current behavior even where the shipped CLI contract was acceptable.

### Root cause

- Packaging and test harness drift.
- Ops packaging drift.
- Test drift.

### Files changed

- `.dockerignore`
- `python/pyproject.toml`
- `setup.sh`
- `ops/docker/explorer.Dockerfile`
- `ops/docker/tests/test_compose_port_bindings.py`
- `python/animica/cli/node.py`
- `python/animica/cli/sync.py`
- `python/animica/cli/tests/test_node_cli.py`
- `python/animica/cli/tests/test_sync_cli.py`
- `p2p/tests/__init__.py`
- `rpc/deps.py`

### Tests added or updated

- Focused CLI and compose test updates

### Validation run

- `pytest -q python/animica/cli/tests/test_node_cli.py python/animica/cli/tests/test_sync_cli.py`
- `pytest -q ops/docker/tests/test_compose_port_bindings.py`

### Remaining risks

- Explorer runtime still blocked
- Live sync e2e still missing

## Iteration 2026-04-07 B

### What was broken

- Wallet pending reservation accounting was false after canonical load.

### Root cause

- `pending_txs` metadata was discarded during canonical wallet parsing.

### Files changed

- `python/animica/wallet/serialization.py`
- `python/animica/cli/tests/test_wallet_serialization.py`

### Tests added or updated

- Pending tx preservation test in wallet serialization

### Validation run

- `pytest -q python/animica/cli/tests/test_wallet_show_output.py python/animica/cli/tests/test_wallet_serialization.py`

### Remaining risks

- Wallet extension and full send/receive e2e are still open.
