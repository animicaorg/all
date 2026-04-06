# Deterministic Sync Repro Plan (Leader + Follower)

## Goal
Reproduce and verify follower advancement from stale head to leader head with explicit operator observability.

## Topology
- Node A: leader/miner
- Node B: follower (starts behind)

## Setup
1. Start Node A with clean chain data.
2. Start Node B with clean chain data and explicit seed to Node A.
3. Confirm both RPC endpoints respond.

## Repro steps (stuck-state baseline)
1. On Node A, mine/create blocks to height `H` where `H >= 4`.
2. On Node B, ensure local head is lower (e.g., 2).
3. Run:
   - `animica node status`
   - `animica sync status --json`
   - `animica sync force`
4. Collect logs around:
   - peer selection
   - header acceptance/discard
   - block scheduling

### Expected old-bug behavior
- `network_best_height > local_head`
- `sync force` reports success
- `headers_accepted_total` remains 0
- no forward head advancement

## Verification steps (fixed behavior)
1. Apply patched build.
2. Repeat scenario above.
3. Verify:
   - `headers_accepted_total > 0`
   - `last_headers_accepted_count > 0` for at least one cycle
   - `BLOCK_FETCH_NOT_SCHEDULED` appears only with explicit reason when blocked
   - follower head increases and converges to leader head
   - `sync force` returns `success=false` with `blockingReason` if no work starts

## Suggested commands
```bash
animica node status
animica sync status --json
animica sync force
animica sync force --boost-seconds 30
```

## Exit criteria
- Follower reaches same head height/hash as leader.
- No silent stall with zero-accept headers while useful peer is available.
- Operator-visible reason codes explain any non-progress branch.

