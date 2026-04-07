# Subsystem Status Matrix

Date: 2026-04-07

| Subsystem | Primary paths | Status | Evidence | Next gate |
| --- | --- | --- | --- | --- |
| Core node CLI | `python/animica/cli/node.py` | Improved | Node CLI suite is green in focused scope | Extend to live node startup smoke |
| Sync CLI | `python/animica/cli/sync.py` | Improved | Sync CLI suite is green in focused scope | Add live two-node convergence smoke |
| RPC backend | `rpc/` | Partially validated | `rpc/tests/test_p2p_supervisor.py`, tx serialization and send param tests pass | Expand to mempool and mining e2e |
| P2P backend | `p2p/` | Partially validated | `p2p/tests/test_sync_completion_status.py` passes | Validate real peer churn and hello timeouts |
| Mempool | `mempool/`, `mempool2/` | Unknown / partially covered | Some tx RPC tests pass indirectly | Run propagation and mining inclusion e2e |
| Mining | `mining/` | Unknown / partially covered | No direct mining smoke run in this pass | Prove mempool inclusion and rewards end to end |
| Wallet CLI | `python/animica/cli/wallet.py` | Improved | Pending reservation accounting fixed; focused wallet smoke green | Add send/receive/mine/confirm smoke |
| Wallet extension | `wallet-extension/`, `apps/wallet-extension/` | Blocked | No stable green smoke in this pass | Reconcile raw tx submission contract |
| Wallet-qt | `wallet-qt/` | Unvalidated | No build or launch proof in this pass | Build and launch smoke |
| Explorer web | `explorer-web/` | Partially validated | `test/unit/sync.test.ts` passes | Connect to live backend and run explorer e2e |
| Explorer ops/runtime | `ops/docker/explorer.Dockerfile`, `ops/docker/entrypoints/explorer.sh` | Blocked | Build bug fixed; runtime app path still stale | Point at a real explorer app |
| Studio web | `studio-web/` | Blocked | Provider smoke fails | Fix provider contract and rerun smoke |
| Admin web / CEX admin | `apps/admin-web/` | Blocked | Type-check fails | Fix typing drift |
| CEX e2e harness | `cex/tests/e2e/` | Blocked | Build fails because `tsc` is unavailable in the current package context | Make harness buildable |
| AICF | `aicf/` | Unvalidated | Code surface exists, no current smoke run | Add focused smoke flow |
| ENA | `ena/` | Unvalidated | Code surface exists, no current smoke run | Add focused smoke flow |
| DA | `da/` | Unvalidated | Code surface exists, no current smoke run | Add snapshot and recovery smoke |
