# Sync Stalls: Diagnosis and Recovery

This guide explains how to diagnose nodes that appear stuck during sync (e.g. headers and
blocks no longer advancing) and how the node recovers without manual "force" loops.

## Quick diagnosis

Run:

```bash
animica debug sync-dump
```

This dumps:

- Local head height/hash
- Best advertised peer head height/hash
- Current sync phase
- In-flight header/block counts
- Pending queue lengths
- Last progress timestamps
- Recent sync errors and the peer associated with them

If the best peer head is not advancing, the network itself may be stalled. If peer heads
are higher than the local head but no progress occurs, inspect the error fields for:

- `last_header_error`
- `last_block_error`
- `last_block_error_peer`
- `stall_reason`

## Common stall causes

1. **Bad block or validation failure**
   - `last_block_error` will indicate verification failures.
   - Compare the failing height/hash across peers to detect a consensus split.

2. **Peer serving issues**
   - Peers may advertise a higher head but refuse blocks.
   - Look for repeated `stall_reason` and peer-specific errors.

3. **State machine mismatch**
   - `sync_phase` stuck in headers/blocks while no in-flight requests exist can
     indicate a stalled pipeline. The watchdog will reset and re-request headers.

4. **Network head not advancing**
   - Best peer head equals local head for a long period. This is not a local stall.

## Automatic recovery behavior

The node runs an internal sync watchdog that:

1. Detects lack of progress.
2. Re-queues headers or blocks.
3. Refreshes peers and resets the sync pipeline.
4. Attempts snapshot-based recovery if configured and safe (P2P discovery first).

If snapshots are enabled, the node only applies a snapshot when it is ahead of the local
head and trusted (manifest signatures or majority peer agreement).

When the block queue is empty while headers are ahead, the node periodically logs a
diagnostic summary explaining why no block requests are issued (queue, inflight, last
error, and stall reason). Snapshot recovery attempts are rate-limited with cooldowns
and per-window caps to avoid endless retry loops.

## Sync target convergence and inflight resets (new)

The sync loop now tracks a single **target tip** derived from the best peer head
(highest total work if known, otherwise highest height; tie-break by earliest peer
timestamp and peer score). Sync only considers itself complete when the local head
**hash** matches this target tip hash — not merely when heights match.

If the local head height equals the target tip height but hashes differ, the node will
log `Sync target hash mismatch; continuing sync to target tip`, re-request headers from
the target peer, and proceed with a reorg if the peer chain wins fork choice.

To avoid stuck in-flight state, the watchdog now resets stale in-flight requests when
no new progress is observed for `SYNC_NO_PROGRESS_TIMEOUT_S` (default 60s). It also
expires in-flight items older than `SYNC_INFLIGHT_TTL_S` (default 120s) and caps retries
per item with `SYNC_INFLIGHT_MAX_RETRIES` (default 3). Logs to watch for:

- `Sync target updated` (new target tip selected)
- `Sync target hash mismatch; continuing sync to target tip`
- `Missing parent detected`
- `Sync inflight reset`

These settings are environment overrides and are safe to tune without protocol changes.

## What to report

When reporting stalls, include:

- Output of `animica debug sync-dump`
- Peer count and best peer head height/hash
- Any block validation error or mismatch reason
- Snapshot recovery status (`snapshot_last_error`, `snapshot_last_attempt_at`)
