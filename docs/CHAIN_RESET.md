# Animica Mainnet Chain Reset (Genesis Reset 2026-05)

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
