# Sync Completion Incident — 2026-04-06

## Symptoms observed live
- Followers were able to connect, mine, and import blocks, but convergence telemetry was inconsistent.
- `target_height` could remain stale even after head advanced.
- `headers_accepted_total` and discard reason telemetry could conflict with observed progression.
- `next_block_needed_height` could report `None` while the node was still behind.
- `animica chain head` could print `Timestamp: ?` when timestamp `0` was valid.

## What was actually working
- Header and block paths were operational enough to move chain head forward.
- Block import progressed and transactions could land on-chain.

## What was broken
- Sync completion gating could short-circuit on stale `_sync_target_height`.
- Snapshot reporting used raw `_sync_target_height` instead of computed best target from checkpoint/network best.
- Catch-up status could hide the next missing height when the queue was briefly empty.
- Cache/telemetry refresh did not explicitly highlight stale field transitions.

## Incident impact
- Operators saw contradictory `SYNCING/SYNCED` status.
- Telemetry made real convergence difficult to verify.
- Troubleshooting duplicate/known-header states lacked precision.
