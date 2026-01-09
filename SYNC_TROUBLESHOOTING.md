# Sync Troubleshooting

This guide covers the sync watchdog, stall recovery actions, and snapshot-based recovery.

## Sync watchdog

The node tracks sync progress (`head_height`, `head_hash`, and timestamps). If there is
no progress for the configured watchdog timeout while peers are connected, the node
escalates recovery actions:

1. Re-queue block requests and rotate peers.
2. Refresh peer dialing/backoffs and re-request headers/blocks.
3. Reset the headers → blocks pipeline state (without wiping DB).
4. Trigger snapshot recovery when the stall persists and cooldown allows.

The current sync phase and watchdog decisions are visible via:

- `animica sync status`
- RPC `sync.getStatus` / `node.getStatus`

## Snapshot recovery

Snapshot recovery is rate-limited and only applies snapshots that are clearly ahead
of the local head (or when forced due to persistent stalls). The manifest must match
the current `chain_id` (and network parameters when available). If signature verification
is required and fails, the snapshot is not applied.

Use these environment variables to tune recovery:

- `ANIMICA_SNAPSHOT_AUTO`
- `ANIMICA_SNAPSHOT_COOLDOWN_SECS`
- `ANIMICA_SNAPSHOT_MIN_ADVANCE_BLOCKS`
- `ANIMICA_SNAPSHOT_TRUSTED_PUBKEYS`
- `ANIMICA_SNAPSHOT_REQUIRE_SIGNATURE`

## Common issues

### Stuck at a height with peers connected

Check `animica sync status` for:

- `stall_reason` and `stall_elapsed_s`
- `snapshot_last_attempt_at` and `snapshot_last_error`
- `eligible_peers_for_blocks`

If the watchdog is repeatedly resetting state without progress, verify that peers are
advertising a higher head and that snapshot URLs are configured.

### Snapshot download failures

Verify that the manifest URLs are reachable and that chunk hashes match. If you require
signatures, ensure `ANIMICA_SNAPSHOT_TRUSTED_PUBKEYS` is set and the manifest includes
valid signature metadata.
