# Sync Incident Report — 2026-04-06

## Incident summary

### Healthy node (Contabo)
- RPC reachable (`127.0.0.1:8545/rpc`).
- P2P and peer connectivity operational.
- Head advanced to height 3, mined block accepted.
- Mempool inclusion path confirmed (`included=1`, `rejected=0`).
- Reward accounting and head hash progression were correct.

### Stalled follower (AWS)
- RPC reachable, peers visible, `network_best_height > local_head`.
- `sync force` reported success but follower stayed at height 2.
- `last_headers_accepted_count=0`, `headers_accepted_total=0`.
- Stall time increased and penalties accumulated.
- Effective useful peers collapsed while sync phase cycled without forward block import.

## Working hypotheses and status

1. **Peer selection starvation due to strict max-height filter** ✅ Confirmed as a likely primary wedge factor.
   - Sync and block peer selection previously filtered candidates to **only** the highest announced height.
   - In noisy topologies (duplicate endpoints / inflated heights), this can repeatedly prefer non-productive peers and starve proven peers that are slightly lower but valid.

2. **Operator false-positive from `sync force`** ✅ Confirmed.
   - RPC `sync.force` could return success after queueing background work without confirming any real transition.

3. **Insufficient branch diagnostics in sync decision path** ✅ Confirmed.
   - Block scheduling and header-discard branches did not always emit explicit structured reason codes.

## Code paths audited

- Header fetch / acceptance:
  - `P2PService._fetch_headers`
  - `P2PService._process_headers`
- Peer selection / eligibility:
  - `P2PService._eligible_sync_peers`
  - `P2PService._select_sync_peer`
  - `P2PService._select_block_peer`
- Block scheduling / request:
  - `P2PService._schedule_block_requests`
  - `P2PService._queue_block_requests`
- Sync entrypoint / operator UX:
  - `rpc.methods.sync.sync_force`

## Runtime checks to perform (post-patch)

1. Run `animica sync status --json` repeatedly and verify:
   - `headers_accepted_total` increments above 0.
   - `last_headers_accepted_count` becomes non-zero when behind.
   - `active_peers_for_headers` / block peer fields show actionable peers.

2. Confirm structured reason logs exist when stalled:
   - `HEADER_BATCH_DISCARDED`
   - `PEER_NOT_ACTIVATED`
   - `BLOCK_FETCH_NOT_SCHEDULED`

3. Confirm `animica sync force` behavior:
   - Returns `success=false` with `blockingReason` when no actual sync work starts.
   - Returns `success=true` only when sync round starts.

4. Validate follower convergence in two-node scenario:
   - follower head transitions `2 -> 3 -> ...` to leader head.

