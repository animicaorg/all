# PTL Architecture Diagram

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                             │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────────┐  │
│  │  animica CLI   │  │  Python SDK    │  │  TypeScript SDK  │  │
│  └────────┬───────┘  └────────┬───────┘  └────────┬─────────┘  │
└───────────┼──────────────────────┼───────────────────┼───────────┘
            │                      │                   │
            └──────────────────────┴───────────────────┘
                                   │
                            JSON-RPC over HTTP
                                   │
┌───────────────────────────────────▼───────────────────────────────┐
│                          RPC SERVER                               │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │              RPC Method Handlers                              ││
│  │  tx.submitRawTransaction  tx.get  tx.pending                 ││
│  │  tx.replicationStatus  debug.ptlStats  debug.ptlPeers        ││
│  └────────────────────────┬─────────────────────────────────────┘│
└───────────────────────────┼───────────────────────────────────────┘
                            │
                            │ deps.get("ptl_service")
                            │
┌───────────────────────────▼───────────────────────────────────────┐
│                      PTL SERVICE LAYER                            │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │                    PtlService                                 ││
│  │  • submit(tx_bytes) → (txid, entry)                          ││
│  │  • get(txid) → entry                                         ││
│  │  • update_status(txid, status)                               ││
│  │  • add_receipt(txid, peer_id, status, reason)                ││
│  │  • get_replication_status(txid) → detailed_status            ││
│  │  • maintenance_loop() [async background]                     ││
│  └────────────┬──────────────────────────────┬──────────────────┘│
│               │                              │                    │
│               │                              │                    │
│  ┌────────────▼────────────┐   ┌────────────▼─────────────────┐ │
│  │      PtlStore           │   │      PtlSelector             │ │
│  │   (SQLite Storage)      │   │  (Block Building)            │ │
│  │                         │   │                              │ │
│  │  • transactions table   │   │  • select_for_block()        │ │
│  │  • receipts table       │   │  • prioritize by fee/size    │ │
│  │  • indexes on status    │   │  • respect gas/size limits   │ │
│  └─────────────────────────┘   └──────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────┘
                            │
                            │ used by
                            │
┌───────────────────────────▼───────────────────────────────────────┐
│                     P2P RELAY LAYER                               │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │                  PtlRelayService                              ││
│  │  • on_ptl_announce(conn_id, txids)                           ││
│  │  • on_ptl_want(conn_id, txids)                               ││
│  │  • on_ptl_push(conn_id, items)                               ││
│  │  • on_ptl_ack(conn_id, data)                                 ││
│  │  • reconcile_loop() [async background]                       ││
│  └────────────────────────┬─────────────────────────────────────┘│
└───────────────────────────┼───────────────────────────────────────┘
                            │
                            │ P2P Protocol Messages
                            │
┌───────────────────────────▼───────────────────────────────────────┐
│                       P2P NETWORK                                 │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │  Message Types (p2p/messages_ptl.py):                        ││
│  │  • PTL_ANNOUNCE (0x0409) - announce available txids          ││
│  │  • PTL_WANT     (0x040A) - request specific txids            ││
│  │  • PTL_PUSH     (0x040B) - send requested transactions       ││
│  │  • PTL_ACK      (0x040C) - acknowledge receipt               ││
│  └──────────────────────────────────────────────────────────────┘│
└───────────────────────────┬───────────────────────────────────────┘
                            │
                ┌───────────┴──────────┐
                │                      │
        ┌───────▼────────┐     ┌──────▼─────────┐
        │   Remote Peer  │     │  Remote Peer   │
        │      Node A    │     │     Node B     │
        └────────────────┘     └────────────────┘
```

## Transaction Lifecycle Flow

```
1. CLIENT SUBMITS
   ┌────────────┐
   │  Client    │
   └──────┬─────┘
          │ animica tx send --min-peers 2
          │
          v
   ┌──────────────┐
   │ RPC: tx.     │
   │ submitRaw... │
   └──────┬───────┘
          │
          v

2. PTL STORES
   ┌──────────────────┐
   │  PtlService      │
   │  Status: NEW     │
   │         ↓        │
   │  Status: STORED  │
   └──────┬───────────┘
          │
          v
   ┌──────────────────┐
   │  PtlStore        │
   │  [SQLite]        │
   │  INSERT tx       │
   └──────────────────┘

3. ANNOUNCE TO PEERS
   ┌──────────────────┐
   │  PtlRelay        │
   │  announce_new... │
   └──────┬───────────┘
          │
          v
   ┌──────────────────┐
   │  P2P Network     │
   │  PTL_ANNOUNCE    │
   └──────┬───────────┘
          │
          v
   ┌──────────────────┐
   │  Remote Peers    │
   └──────────────────┘

4. PEERS REQUEST
   ┌──────────────────┐
   │  Remote Peers    │
   │  on_ptl_announce │
   └──────┬───────────┘
          │ PTL_WANT
          v
   ┌──────────────────┐
   │  Local Node      │
   │  on_ptl_want     │
   └──────┬───────────┘
          │ PTL_PUSH
          v

5. PEERS ACKNOWLEDGE
   ┌──────────────────┐
   │  Remote Peers    │
   │  on_ptl_push     │
   └──────┬───────────┘
          │ PTL_ACK
          v
   ┌──────────────────┐
   │  PtlService      │
   │  add_receipt()   │
   └──────┬───────────┘
          │
          v
   ┌──────────────────┐
   │  Check ack_count │
   │  >= min_peer_acks│
   └──────┬───────────┘
          │ YES
          v
   ┌──────────────────┐
   │  Status:         │
   │  ATTESTED        │
   └──────────────────┘

6. MINING
   ┌──────────────────┐
   │  Miner           │
   │  get_for_mining()│
   └──────┬───────────┘
          │
          v
   ┌──────────────────┐
   │  PtlSelector     │
   │  select by       │
   │  fee/size/age    │
   └──────┬───────────┘
          │
          v
   ┌──────────────────┐
   │  Block Built     │
   │  mark_included() │
   └──────┬───────────┘
          │
          v
   ┌──────────────────┐
   │  Status:         │
   │  INCLUDED        │
   └──────────────────┘
```

## Anti-Entropy Reconciliation

```
Every 10 seconds:

┌──────────────┐                    ┌──────────────┐
│   Node A     │                    │   Node B     │
└──────┬───────┘                    └──────┬───────┘
       │                                   │
       │  1. Get pending txids             │
       │                                   │
       │  2. PTL_ANNOUNCE [tx1,tx2,tx3]    │
       ├───────────────────────────────────>│
       │                                   │
       │                                   │  3. Check local PTL
       │                                   │     Missing: tx2
       │                                   │
       │  4. PTL_WANT [tx2]                │
       │<───────────────────────────────────┤
       │                                   │
       │  5. Fetch tx2 from store          │
       │                                   │
       │  6. PTL_PUSH [tx2_data]           │
       ├───────────────────────────────────>│
       │                                   │
       │                                   │  7. Store tx2
       │                                   │
       │  8. PTL_ACK [tx2]                 │
       │<───────────────────────────────────┤
       │                                   │
       │  9. Record receipt                │
       │                                   │
       v                                   v
```

## Database Schema

```
┌──────────────────────────────────────────────────────────────┐
│                    ptl_transactions                          │
├──────────────────┬───────────────────────────────────────────┤
│ txid             │ BLOB PRIMARY KEY                          │
│ tx_bytes         │ BLOB NOT NULL                             │
│ status           │ TEXT NOT NULL                             │
│ received_at      │ REAL NOT NULL                             │
│ updated_at       │ REAL NOT NULL                             │
│ origin           │ TEXT NOT NULL                             │
│ fee              │ INTEGER NOT NULL DEFAULT 0                │
│ size             │ INTEGER NOT NULL DEFAULT 0                │
│ nonce            │ INTEGER                                   │
│ sender           │ BLOB                                      │
│ reject_reason    │ TEXT                                      │
│ included_height  │ INTEGER                                   │
│ finalized_height │ INTEGER                                   │
│ expire_at        │ REAL                                      │
└──────────────────┴───────────────────────────────────────────┘
  Indexes: status, received_at, updated_at, expire_at, (sender,nonce)

┌──────────────────────────────────────────────────────────────┐
│                      ptl_receipts                            │
├──────────────────┬───────────────────────────────────────────┤
│ id               │ INTEGER PRIMARY KEY AUTOINCREMENT         │
│ txid             │ BLOB NOT NULL                             │
│ peer_id          │ TEXT NOT NULL                             │
│ timestamp        │ REAL NOT NULL                             │
│ status           │ TEXT NOT NULL                             │
│ reason           │ TEXT                                      │
├──────────────────┴───────────────────────────────────────────┤
│ FOREIGN KEY (txid) REFERENCES ptl_transactions(txid)        │
└──────────────────────────────────────────────────────────────┘
  Index: txid
```

## Configuration Hierarchy

```
1. Environment Variables (highest priority)
   ├─ ANIMICA_TX_SYSTEM=ptl
   ├─ ANIMICA_PTL_MIN_PEER_ACKS=2
   ├─ ANIMICA_PTL_TTL_SECONDS=3600
   └─ ... (see core/ptl/config.py)

2. Config File (if implemented)
   └─ ~/.animica/config.yaml

3. Defaults (lowest priority)
   └─ PtlConfig defaults in code
```

## Message Flow Example

```
Scenario: Node A submits tx, Node B and C replicate

Time  Node A              Node B              Node C
────────────────────────────────────────────────────────
t=0   submit(tx1)
      status=STORED
      
t=1   PTL_ANNOUNCE ──────> receive
      [tx1]          ────────────────> receive
      
t=2                        PTL_WANT
                     <────── [tx1]
                                        PTL_WANT
                                  <───── [tx1]
                                  
t=3   PTL_PUSH ──────────> receive     
      [tx1_data]                        
      PTL_PUSH ─────────────────────> receive
      [tx1_data]
      
t=4                        store(tx1)
                           status=STORED
                                        store(tx1)
                                        status=STORED
                                        
t=5                        PTL_ACK
                     <────── [tx1, "ack"]
                                        PTL_ACK
                                  <───── [tx1, "ack"]
                                  
t=6   add_receipt(B)
      add_receipt(C)
      ack_count=2
      status=ATTESTED ✓
```

This diagram shows the complete flow from submission to attestation with 2 peers.
