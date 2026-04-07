# Chain Sync Status Matrix

Date: 2026-04-07

| Component | Status | Evidence | Notes |
| --- | --- | --- | --- |
| Backend runtime installability | Improved | `setup.sh` installs `requirements.txt` and verifies backend imports | Removes a real false-negative test harness blocker |
| Node status CLI | Green in focused scope | Included in `78 passed` node and sync CLI run | Bounded retry behavior now tested against current contract |
| Sync status CLI cache handling | Fixed | Read paths no longer create directories; persistence failures no longer crash status reads | Prevents false operator failures on restricted paths |
| P2P supervisor | Green in focused scope | `rpc/tests/test_p2p_supervisor.py` passes | Not a substitute for live peer churn validation |
| Sync completion backend | Green in focused scope | `p2p/tests/test_sync_completion_status.py` passes | Still need live leader/follower convergence proof |
| Compose networking policy | Green in focused scope | `ops/docker/tests/test_compose_port_bindings.py` passes | RPC loopback-only exposure is now explicitly tested |
| Docker node packaging | Improved | Build context and dependency issues reduced; no current packaging failure reproduced in this pass | Full runtime startup smoke still missing |
| Docker miner packaging | Improved | Build context issue removed | Full runtime mining smoke still missing |
| Docker explorer packaging | Partially fixed | Non-root script install bug fixed | Runtime entrypoint still targets stale app path |
| Live chain convergence | Open | No new live two-node e2e run in this pass | Highest remaining sync blocker |
