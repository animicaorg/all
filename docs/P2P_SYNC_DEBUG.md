# P2P Sync Debugging

## Eligibility checks

Header/block sync only selects peers that have completed a HELLO/HELLO_ACK
handshake and match the local network. The eligibility gate requires:

- `chain_id` matches the local chain ID.
- `genesis_hash` is present and matches the local genesis hash.
- Handshake completed (`hello_done`) and the peer is marked `ready_for_sync`.
- The peer advertises sync capability (`sync`, `blocks`, or `headers`) **or**
  reports a non-zero `head_height`.

Peers that fail any check are marked ineligible with a reason and excluded
from header/block selection.

## RPC: `p2p.syncDebug`

Use `p2p.syncDebug` to inspect sync internals:

```
animica rpc p2p.syncDebug
```

The response includes:

- Connected peers with handshake fields (chain ID, genesis hash, capabilities).
- Eligible vs. ineligible peers and reasons.
- The current header locator used for `getheaders`.
- Recent header request/response events.
- Expected local `chain_id` and `genesis_hash`.
