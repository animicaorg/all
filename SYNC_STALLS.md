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
4. Attempts snapshot-based recovery if configured and safe.

If snapshots are enabled, the node only applies a snapshot when it is ahead of the local
head and trusted (manifest signatures or majority peer agreement).

## What to report

When reporting stalls, include:

- Output of `animica debug sync-dump`
- Peer count and best peer head height/hash
- Any block validation error or mismatch reason
