# Sync Root Cause Tracker

## RC-1: Max-height-only peer selection can starve productive peers
- **Symptom**: Follower sees higher network height but keeps accepting zero headers / no block progress.
- **Evidence**: `_select_sync_peer` and `_select_block_peer` previously reduced candidates to exact max advertised height.
- **Impact**: In noisy peer sets, slightly lower but valid/anchored peers are never chosen.
- **Fix status**: ✅ Patched.
- **Patch**:
  - Introduced near-tip window (`max_height - 2`) and local-head floor (`local+1`).
  - Added score bonuses for anchored/proven header-serving peers.
  - Kept anti-non-broadcasting behavior.

## RC-2: `sync.force` reported success without proving real sync transition
- **Symptom**: CLI/operator gets false reassurance: “triggered successfully”.
- **Evidence**: RPC layer spawned background task and returned success regardless of actual `started` state.
- **Fix status**: ✅ Patched.
- **Patch**:
  - Await force-sync execution with timeout.
  - Surface `started`, `error`, and `blockingReason`.
  - Return `success=false` when no work was actually started.

## RC-3: Missing structured branch reasons obscured failure mode
- **Symptom**: Hard to tell why headers/blocks not progressing.
- **Fix status**: ✅ Improved.
- **Patch**:
  - Added structured logs:
    - `HEADER_BATCH_DISCARDED`
    - `BLOCK_FETCH_NOT_SCHEDULED`
    - `PEER_NOT_ACTIVATED`

## Remaining caveats
- Full multi-node WAN repro still depends on environment-specific peer topology and latency.
- If checkpoint-anchor policy is enabled and peers never anchor, additional checkpoint-specific diagnostics may still need expansion.

