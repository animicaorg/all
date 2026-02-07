# Before/After Visual: TX_NOTFOUND Fix

## Before Fix ❌

```
┌──────────────────────────────────────────────────────────┐
│ Problem: First NOTFOUND clears txid from ALL peers      │
└──────────────────────────────────────────────────────────┘

Step 1: Both peers know about transaction
┌─────────────┐         ┌─────────────┐
│  Peer A     │         │  Peer B     │
│  conn-1     │         │  conn-2     │
│             │         │             │
│ known_txids │         │ known_txids │
│  [0xe4d5..]│         │  [0xe4d5..]│
└─────────────┘         └─────────────┘
       │                       │
       └───────┬───────────────┘
               │
        ┌──────▼──────┐
        │   Node      │
        │  (empty     │
        │  mempool)   │
        └─────────────┘

Step 2: Node requests from Peer A (conn-1)
┌─────────────┐
│  Peer A     │◄──── TX_GET
│  conn-1     │
└─────────────┘

Step 3: Peer A responds NOTFOUND
┌─────────────┐
│  Peer A     │────► TX_NOTFOUND
│  conn-1     │
│             │
│ known_txids │      ╔═══════════════════════════╗
│  [CLEARED] │      ║ Bug: Clears from ALL peers║
└─────────────┘      ╚═══════════════════════════╝
                              │
                     ┌────────▼────────┐
                     │  Peer B         │
                     │  conn-2         │
                     │                 │
                     │ known_txids     │
                     │  [CLEARED]  ❌  │
                     └─────────────────┘

Step 4: No retry - transaction lost ❌
        ┌─────────────┐
        │   Node      │
        │  (mempool   │
        │   EMPTY)    │  ← Transaction never fetched!
        └─────────────┘
```

## After Fix ✅

```
┌──────────────────────────────────────────────────────────┐
│ Solution: Only clear from responding peer, retry others  │
└──────────────────────────────────────────────────────────┘

Step 1: Both peers know about transaction
┌─────────────┐         ┌─────────────┐
│  Peer A     │         │  Peer B     │
│  conn-1     │         │  conn-2     │
│             │         │             │
│ known_txids │         │ known_txids │
│  [0xe4d5..]│         │  [0xe4d5..]│
└─────────────┘         └─────────────┘
       │                       │
       └───────┬───────────────┘
               │
        ┌──────▼──────┐
        │   Node      │
        │  (empty     │
        │  mempool)   │
        └─────────────┘

Step 2: Node requests from Peer A (conn-1)
┌─────────────┐
│  Peer A     │◄──── TX_GET
│  conn-1     │
└─────────────┘

Step 3: Peer A responds NOTFOUND
┌─────────────┐
│  Peer A     │────► TX_NOTFOUND
│  conn-1     │
│             │      ╔════════════════════════════╗
│ known_txids │      ║ Fix: Only clear from Peer A║
│  [CLEARED] │      ╚════════════════════════════╝
└─────────────┘
                     ┌─────────────┐
                     │  Peer B     │
                     │  conn-2     │
                     │             │
                     │ known_txids │
                     │  [0xe4d5..]│✓ Still has it!
                     └─────────────┘

Step 4: Automatic retry to Peer B ✓
                     ┌─────────────┐
              ┌─────►│  Peer B     │
              │      │  conn-2     │
              │      └─────────────┘
         TX_GET             │
                           │ TX_DATA (transaction bytes)
                           ▼
                    ┌─────────────┐
                    │   Node      │
                    │             │
                    │ Mempool:    │
                    │ [0xe4d5..] │✓
                    └─────────────┘

Step 5: Success! Transaction mined ✓
                    ┌─────────────┐
                    │   Block N   │
                    │             │
                    │ Txs:        │
                    │ [0xe4d5..] │✓
                    └─────────────┘
```

## Key Differences

| Aspect | Before Fix ❌ | After Fix ✅ |
|--------|--------------|-------------|
| NOTFOUND clears | ALL peers | Only responding peer |
| Retry behavior | No retry | Automatic retry to other peers |
| Transaction fate | Lost forever | Successfully fetched |
| Mempool state | Empty | Contains transaction |
| Mining | No tx to mine | Transaction mined |

## Code Change Summary

**Before:**
```python
# Clear from ALL peers' known_txids
for peer_id, peer_state in self._peer_state.items():
    if txid in peer_state.known_txids:
        peer_state.known_txids.remove(txid)
```

**After:**
```python
# Clear only from responding peer
if state and txid in state.known_txids:
    state.known_txids.remove(txid)

# Check for other peers and retry
candidates = [p for p in sources if p != conn_id and self._peer_eligible(p)]
if candidates:
    next_peer = self._request_mgr.pick_peer(txid, candidates=candidates)
    await self._send_tx_get(next_peer, [txid])  # Retry!
```
