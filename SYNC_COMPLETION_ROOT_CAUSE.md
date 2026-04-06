# Sync Completion Root Cause

## Root cause summary
1. **Stale target short-circuit:** `_sync_once()` could return `TARGET_REACHED` whenever local height met `_sync_target_height`, even when `network_best_height` was higher.
2. **Snapshot target mismatch:** `SyncStatusSnapshot.target_height` emitted raw `_sync_target_height`, while synchronization logic used a computed target candidate set (target/checkpoint/network best).
3. **Catch-up visibility gap:** `_next_block_needed()` only looked at queued hashes; when queue was empty but the node was still behind, it returned `None`.
4. **Telemetry ambiguity:** discard accounting fallback collapsed partial discards into `duplicate_headers`, masking “already known/overlapping” states.
5. **CLI timestamp truthiness bug:** `chain head` used `or`, so timestamp `0` became `?`.

## Why convergence was unreliable
- Once `_sync_target_height` became stale-low, the short-circuit could stop header polling before latest network tip was reached.
- Operators observed apparent completion while behind true network best.

## Fix strategy
- Gate `TARGET_REACHED` by `network_best_height`.
- Centralize target updates through a helper with explicit set/clear diagnostics.
- Emit computed target in sync snapshot.
- Provide fallback `next_block_needed_height` from head/target context when queue is empty.
- Add structured diagnostics for target, header, block, convergence, and cache refresh events.
