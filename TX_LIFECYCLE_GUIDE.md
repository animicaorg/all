# Transaction Lifecycle & Multi-Node Mining Guide

## Overview

Animica implements a production-grade transaction pipeline where **any miner can mine any transaction** propagated on the network. This document explains the complete lifecycle from submission to confirmation.

## Core Design Principles

1. **Canonical Serialization**: All transactions use deterministic CBOR encoding
2. **Stable TxID**: SHA3-256 hash of canonical CBOR (includes signatures)
3. **P2P Gossip**: Transactions propagate via INV/GET/DATA protocol
4. **Mempool Convergence**: Eventual consistency across all nodes
5. **Deterministic Selection**: Block template building uses priority-based ordering

## Transaction Lifecycle

### 1. Creation & Signing

**Location**: Wallet or SDK

```python
from animica.tx.signing import build_signable_tx_bytes
from pq.py import sign

# Build transaction body
tx_body = {
    "chainId": 1,
    "from": sender_address,
    "to": recipient_address,
    "nonce": next_nonce,
    "value": amount_in_wei,
    "gasLimit": 21000,
    "maxFee": gas_price_in_wei,
    "data": b"",
}

# Create signable bytes (canonical CBOR)
sign_bytes = build_signable_tx_bytes(tx_body)

# Sign with PQ algorithm (Dilithium3 or SPHINCS+)
signature = sign(sign_bytes, private_key, alg="dilithium3")

# Create signed envelope
tx_envelope = {
    "tx": tx_body,
    "sigs": [signature],
}

# Serialize to canonical CBOR
import cbor2
raw_tx = cbor2.dumps(tx_envelope, canonical=True)

# Compute txid
import hashlib
txid = hashlib.sha3_256(raw_tx).digest()
```

### 2. Submission via RPC

**Location**: Node A (any node on the network)

**Endpoints**:
- `tx_send_raw_transaction(rawTx: str)` - Submit pre-signed raw CBOR
- `eth_sendTransaction(params)` - SDK-style envelope

**Process**:
```
User → RPC → Sync Gate → Validation → Mempool → P2P Broadcast
```

**Validation Steps**:
1. Parse CBOR envelope
2. Verify chain ID matches
3. Extract sender from signature
4. Check nonce (v1) or validity window (v2)
5. Verify balance sufficiency
6. Check gas price meets minimum
7. Admit to mempool

**Success Response**:
```json
{
  "txid": "0x...",
  "status": "pending"
}
```

### 3. Mempool Admission

**Location**: `/rpc/mempool_service.py::MempoolService.submit()`

**Admission Checks**:
- ✅ Chain ID match
- ✅ No duplicate TX (by txid)
- ✅ No replay (check tx_index history)
- ✅ Sender extracted successfully
- ✅ Nonce valid (v1) or time window valid (v2)
- ✅ Gas limit > 0
- ✅ Gas price ≥ min_gas_price_wei
- ✅ Balance sufficient (confirmed + pending spend)

**On Success**:
1. Add to pool (priority heap)
2. Persist to disk (if enabled)
3. **Trigger P2P broadcast callback** → `TxRelayService.on_mempool_add()`

### 4. P2P Gossip Propagation

**Location**: `/p2p/txrelay.py::TxRelayService`

**Protocol Flow**:

```
Node A (sender)                Node B (receiver)
     │                              │
     ├─── INV(txid) ────────────────>
     │                              │
     │                         [Check: has_tx?]
     │                              │
     <──── GET(txid) ───────────────┤
     │                              │
     ├─── DATA(txid, raw) ──────────>
     │                              │
     │                         [Validate & admit]
     │                              │
     │                         [Rebroadcast INV to peers]
     │                              │
```

**Message Types** (`/p2p/messages_tx.py`):
- **TxInv**: Announce txids to peers
- **TxGet**: Request tx bodies
- **TxData**: Deliver tx bodies
- **TxNotFound**: Signal missing tx
- **TxMempoolReq/Resp**: Bulk mempool sync

**Deduplication**:
- Per-peer `known_txids` LRU cache (50k entries)
- Skip INV if peer already knows txid
- Track inflight GET requests to avoid duplicate fetches

**Reconciliation**:
- On peer connect: sync mempool txids
- Periodic reconciliation every `reconcile_interval_s` (default: 10s)
- Late-joining nodes fetch missing txs via GET

### 5. Block Template Building

**Location**: `/rpc/methods/miner.py::miner_get_block_template()`

**Process**:
1. **Sync peer mempools** (timeout: 1.5s)
   ```python
   synced_peers = _sync_all_peer_mempools(timeout_s=1.5)
   ```

2. **Collect mempool entries**
   ```python
   pending_entries, pending_raw_by_hash, total = _collect_mempool_entries(
       ctx=ctx, adapter=adapter, limit=1000
   )
   ```

3. **Select transactions for block** (`/mempool/select.py::select_for_block`)
   - Sort by effective priority (fee rate + age bonus)
   - Apply nonce/balance checks incrementally
   - Enforce block limits:
     - `MAX_TX_PER_BLOCK` (unlimited by default)
     - `MAX_BLOCK_BYTES` (1GB default)
     - `MAX_BLOCK_GAS` (100B default)
   - Deterministic tiebreak: lexicographic by txid

4. **Build block header**
   - Compute `txs_root` from selected txs (Merkle root)
   - Compute `receipts_root` (initially ZERO32)
   - Compute `state_root` (initially parent state root)

5. **Return template**
   ```json
   {
     "header": {...},
     "transactions": [...],
     "txCount": 15,
     "selectedHashes": ["0x...", ...]
   }
   ```

### 6. Mining

**Location**: Node B (any miner on the network)

**Mining Flow**:
1. Get block template (includes txs from all peers)
2. Compute nonce candidates
3. Check PoIES acceptance (hash-share + external proofs)
4. If accepted:
   - Finalize block (compute receipts, state transitions)
   - Broadcast block to network

**Key Point**: Node B mines transactions that were originally submitted to Node A, demonstrating cross-node mining capability.

### 7. Block Propagation & Confirmation

**Process**:
```
Miner (Node B) → Broadcast Block → All Nodes (A, C, ...) → Validate & Apply
```

**On Block Receipt**:
1. Validate block (header, txs, receipts)
2. Apply state transitions (execute txs sequentially)
3. Remove included txs from mempool via `pool.remove_included(txids)`
4. Update chain head
5. Emit confirmation events

**Confirmation Levels**:
- 1 confirmation: included in head block
- 6 confirmations: considered stable (standard recommendation)
- 12+ confirmations: irreversible (unless deep reorg)

## Observability & Debugging

### RPC Methods for Tracing

**1. `debug.traceTx(txid)` - Complete lifecycle trace**
```bash
animica-cli rpc debug.traceTx 0x1234...
```

Returns:
```json
{
  "txid": "0x1234...",
  "status": "mined",
  "lifecycle": {
    "mempool_status": "in_pool",
    "p2p": {
      "arrival_time": 1234567890.5,
      "source": "local",
      "validation_status": "valid"
    },
    "mined_in_block": {
      "height": 1234,
      "hash": "0xabcd...",
      "index": 2
    },
    "confirmations": 10
  }
}
```

**2. `tx.status(txid)` - Simple status check**
```bash
animica-cli rpc tx.status 0x1234...
```

Returns:
```json
{
  "txid": "0x1234...",
  "status": "pending",
  "in_mempool": true,
  "in_chain": false,
  "block_height": null,
  "confirmations": null
}
```

**3. `tx.explainReject(raw_tx)` - Dry-run validation**
```bash
animica-cli rpc tx.explainReject 0xabcd...
```

Returns:
```json
{
  "valid": false,
  "reason": "nonce_too_low",
  "details": {
    "chain_id": 1,
    "sender": "0x...",
    "nonce": 5,
    "expected_nonce": 6,
    "checks": {
      "chain_id_match": true,
      "sender_valid": true,
      "nonce_valid": false,
      "balance_sufficient": true,
      "gas_price_sufficient": true
    }
  }
}
```

### Log Correlation

**Track transaction flow across nodes**:
```bash
# Node A logs
[INFO] MempoolService.submit: SUCCESS - tx added, tx_hash=0x1234..., pool_size=5
[INFO] [DIAG] P2P broadcast scheduled for tx 0x1234...

# Node B logs
[INFO] TX_INV_RECEIVED: peer=node-a, count=1, txids=[1234...]
[INFO] TX_RELAY_ANNOUNCE_RECV: peer=node-a, count=1
[INFO] TX_RELAY_MEMPOOL_INSERT_OK: hash=0x1234..., source=peer:node-a

# Node B mining logs
[INFO] block template mempool collection: entries=5, total=5
[INFO] Block template includes 5 transactions
[INFO] Block mined: height=1235, hash=0xabcd..., txs=5
```

## Troubleshooting

### Issue: Transaction Not Propagating

**Symptoms**: TX stays in Node A's mempool, never reaches Node B

**Debug Steps**:
1. Check P2P connectivity:
   ```bash
   animica-cli rpc p2p.listPeers
   ```

2. Check mempool callback binding:
   ```bash
   # Look for log:
   [INFO] P2P broadcast callback registered: mempool_id=0x...
   ```

3. Check tx relay metrics:
   ```bash
   animica-cli rpc debug.txRelayMetrics
   ```

4. Trace transaction:
   ```bash
   animica-cli rpc debug.traceTx $TXID
   ```

**Common Causes**:
- P2P service not started
- Mempool callback not bound
- Peer marked as ineligible (recent disconnect)
- TX exceeds peer rate limits

### Issue: Transaction Rejected

**Symptoms**: TX fails admission on Node B after propagation

**Debug Steps**:
1. Explain rejection:
   ```bash
   animica-cli rpc tx.explainReject $RAW_TX
   ```

2. Check mempool rejection history:
   ```bash
   animica-cli rpc debug.mempoolTxTrace $TXID
   ```

**Common Causes**:
- Chain ID mismatch
- Nonce too low (already confirmed)
- Nonce gap (missing previous nonce)
- Insufficient balance (including pending txs)
- Gas price below minimum
- Signature invalid

### Issue: Transaction Not Mined

**Symptoms**: TX in mempool on all nodes, but not included in blocks

**Debug Steps**:
1. Check mempool size:
   ```bash
   animica-cli rpc mempool.getStats
   ```

2. Check block templates:
   ```bash
   animica-cli rpc miner.getBlockTemplate '{\"address\":\"$MINER\",\"includeMempool\":true}'
   ```

3. Check fee market:
   ```bash
   animica-cli rpc mempool.getStats
   # Look for min_fee_wei, avg_fee_wei
   ```

**Common Causes**:
- Fee too low (below watermark)
- Nonce gap (waiting for earlier tx)
- Block gas limit reached
- Miner not including mempool txs (`includeMempool: false`)

## Multi-Node Testing

### Docker Compose Setup

Create `docker-compose.multinode-test.yml`:

```yaml
version: '3.8'

services:
  node-a:
    image: animica-node:latest
    environment:
      - CHAIN_ID=1337
      - P2P_SEEDS=node-b:30303
      - RPC_HOST=0.0.0.0
      - RPC_PORT=8545
    ports:
      - "8545:8545"
      - "30303:30303"
    volumes:
      - ./data-a:/data

  node-b:
    image: animica-node:latest
    environment:
      - CHAIN_ID=1337
      - P2P_SEEDS=node-a:30303
      - RPC_HOST=0.0.0.0
      - RPC_PORT=8545
      - MINING_ENABLED=1
      - MINER_ADDRESS=0x...
    ports:
      - "8546:8545"
      - "30304:30303"
    volumes:
      - ./data-b:/data
```

### Run E2E Test

```bash
# Start nodes
docker-compose -f docker-compose.multinode-test.yml up -d

# Wait for P2P connection
sleep 5

# Submit 20 txs to Node A
for i in {1..20}; do
  animica-cli --rpc http://localhost:8545 tx send \
    --to 0x... --value 1000 --nonce $i
done

# Check Node A mempool
animica-cli --rpc http://localhost:8545 rpc mempool.getStats

# Check Node B mempool (should have propagated)
animica-cli --rpc http://localhost:8546 rpc mempool.getStats

# Build template on Node B
animica-cli --rpc http://localhost:8546 rpc miner.getBlockTemplate \
  '{"address":"0x...","includeMempool":true}'

# Mine on Node B
# (Block should include txs from Node A)

# Verify on Node A
animica-cli --rpc http://localhost:8545 rpc chain.getHead
```

## Production Recommendations

### Mempool Configuration

```yaml
# config/mempool.yaml
limits:
  max_txs: 150000              # Maximum transactions in pool
  max_bytes: 268435456         # 256 MB total
  max_tx_bytes: 1048576        # 1 MB per tx

gas:
  min_price_wei: 1000000000    # 1 gwei minimum
  target_utilization: 0.9      # Eviction starts at 90%

watermark:
  min_floor_wei: 1000000000    # Never admit below 1 gwei
  ema_alpha: 0.1               # Fee market smoothing
```

### P2P Configuration

```yaml
# config/p2p.yaml
tx_relay:
  inv_batch_size: 200          # Max txids per INV message
  inv_flush_interval_s: 0.2    # Batch delay
  max_inflight_total: 2048     # Max concurrent requests
  request_cooldown_s: 3.5      # Retry backoff
  reconcile_interval_s: 10.0   # Mempool sync interval
```

### Mining Configuration

```yaml
# config/mining.yaml
block_limits:
  max_gas: 100000000000        # 100 billion gas
  max_bytes: 1000000000        # 1 GB
  max_txs: 10000               # Unlimited (practical limit)

selection:
  include_mempool: true        # REQUIRED for cross-node mining
  sync_timeout_s: 1.5          # Wait for peer mempool sync
```

## References

- **TX Serialization**: `/python/animica/tx/signing.py`
- **Mempool Core**: `/mempool/pool.py`
- **P2P Relay**: `/p2p/txrelay.py`
- **RPC Methods**: `/rpc/methods/tx.py`, `/rpc/methods/debug.py`
- **Block Template**: `/rpc/methods/miner.py`
- **Selection Logic**: `/mempool/select.py`

## Summary

The Animica transaction pipeline is production-ready and supports the core requirement: **any miner can mine any transaction** from any node on the network. Key features:

✅ Canonical serialization ensures stable txids  
✅ P2P gossip propagates transactions to all nodes  
✅ Mempool reconciliation catches late-joiners  
✅ Block templates include multi-node transactions  
✅ Deterministic selection prevents gaming  
✅ Comprehensive observability for debugging  

The system is designed for decentralization, reliability, and auditability.
