# Animica Mainnet Chain Reset (Genesis Reset 2026-01)

This release performs a **hard chain reset** for mainnet while keeping **chain_id = 1**.
The genesis hash has changed, so all nodes must start syncing from height 0.

## What changed

- Chain ID remains **1**.
- Genesis hash changed (new genesis): `0xe020040d488c83dd86a1613c5a8017cf60e7ed725952426cef39ab584ac43fab`.
- P2P nodes refuse connections to peers with the old genesis.
- Datadir guard prevents reusing old chain data for chain_id=1.
- Target block interval updated to **300 seconds** via consensus retarget parameters.

## Reset steps (mainnet)

```bash
animica node down
animica node reset --network mainnet --yes
animica node up
```

## Notes

- Nodes will disconnect from peers with mismatched genesis hashes.
- If you see a genesis mismatch error, wipe the data directory or run `animica node reset`.

## Verifier Node Operators

For verifier nodes (144.126.133.21, 3.12.224.189), see [VERIFIER_NODE_RESTART.md](./VERIFIER_NODE_RESTART.md) for:
- Step-by-step restart procedures
- Genesis hash update without full reset
- State persistence across restarts
- Troubleshooting guidance

## Auto-Reset Option

To automatically handle genesis mismatches on startup:

```bash
# Enable auto-reset
animica node up --auto-reset-genesis-mismatch

# Or set environment variable
export ANIMICA_AUTO_RESET_GENESIS_MISMATCH=1
animica node up
```

This will detect genesis mismatches and automatically wipe old data before syncing with the new genesis.
