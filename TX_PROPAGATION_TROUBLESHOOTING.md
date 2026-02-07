# Transaction Propagation Troubleshooting Guide

## Quick Diagnostic Commands

```bash
# Check node connectivity
animica-cli rpc p2p.listPeers

# Check mempool status
animica-cli rpc mempool.getStats

# Check transaction status
animica-cli rpc tx.status $TXID

# Trace transaction lifecycle
animica-cli rpc debug.traceTx $TXID

# Check P2P tx relay metrics
animica-cli rpc debug.txRelayMetrics

# Explain why a tx would be rejected
animica-cli rpc tx.explainReject $RAW_TX_HEX
```

## Common Issues & Solutions

### 1. Transaction Not Propagating to Peers

**Symptoms**:
- TX accepted on Node A
- TX never appears on Node B's mempool
- `debug.traceTx` shows no P2P events

**Diagnostic Steps**:

```bash
# 1. Check P2P connectivity
animica-cli rpc p2p.listPeers
# Expected: List of connected peers with status="connected"

# 2. Check if mempool callback is bound
# Look for log line:
grep "P2P broadcast callback registered" /var/log/animica/node.log

# 3. Check tx relay metrics on sender
animica-cli rpc debug.txRelayMetrics
# Look for:
#   inv_sent > 0 (INV messages sent)
#   accepted_count > 0 (local admissions)
```

**Common Causes & Fixes**:

| Cause | Diagnostic | Solution |
|-------|-----------|----------|
| P2P service not started | No peers in `p2p.listPeers` | Start P2P service or check config |
| Mempool callback not bound | Missing log "P2P broadcast callback registered" | Restart node to trigger binding |
| Peer recently disconnected | Peer in "connecting" state | Wait for reconnection or force reconnect |
| Rate limiting | `inv_sent` count not increasing | Check rate limit config, increase if needed |
| Peer marked as ineligible | Peer in `p2p.listPeers` but `eligible=false` | Check peer eligibility criteria |

**Quick Fix**:
```bash
# Restart node to rebind mempool callback
systemctl restart animica-node

# Force peer reconnection
animica-cli rpc p2p.disconnectPeer $PEER_ID
animica-cli rpc p2p.connectPeer $PEER_ADDRESS
```

### 2. Transaction Rejected by Remote Node

**Symptoms**:
- TX propagated (appears in peer's logs)
- TX not in remote mempool
- Remote node logs show rejection

**Diagnostic Steps**:

```bash
# 1. Check rejection on local node
animica-cli rpc debug.mempoolTxTrace $TXID
# Look for rejection.reason

# 2. Explain rejection with dry-run
animica-cli rpc tx.explainReject $RAW_TX_HEX

# 3. Check chain state differences
# On Node A:
animica-cli rpc state.getBalance $SENDER
animica-cli rpc state.getNonce $SENDER

# On Node B:
animica-cli --rpc http://node-b:8545 rpc state.getBalance $SENDER
animica-cli --rpc http://node-b:8545 rpc state.getNonce $SENDER
```

**Common Rejection Reasons**:

| Reason | Meaning | Solution |
|--------|---------|----------|
| `chain_id_mismatch` | TX for different chain | Recreate TX with correct chain ID |
| `nonce_too_low` | Nonce already used | Get current nonce, submit with next nonce |
| `nonce_gap` | Skipped nonce | Submit missing earlier nonces first |
| `insufficient_funds_pending` | Balance too low (including pending) | Wait for pending txs to confirm or increase balance |
| `fee_too_low` | Gas price below minimum | Increase maxFee/gasPrice |
| `replay` | TX already confirmed | Don't resubmit; TX is confirmed |
| `expired` | TX validity window expired (v2) | Recreate TX with new validity window |

**Quick Fix for Nonce Issues**:
```bash
# Get correct nonce
NONCE=$(animica-cli rpc state.getNonce $SENDER)

# Resubmit with correct nonce
animica-cli tx send --to $RECIPIENT --value $AMOUNT --nonce $NONCE
```

### 3. Transaction Not Mined

**Symptoms**:
- TX in mempool on all nodes
- Multiple blocks mined, but TX not included
- TX status remains "pending"

**Diagnostic Steps**:

```bash
# 1. Check mempool priority
animica-cli rpc mempool.getPending --limit 100
# Look for your TX position in the list

# 2. Check fee market
animica-cli rpc mempool.getStats
# Compare your TX fee to avg_fee_wei, min_fee_wei

# 3. Check block template
animica-cli rpc miner.getBlockTemplate \
  '{"address":"$MINER","includeMempool":true}'
# Check if your TX is in transactions list

# 4. Check for nonce gaps
animica-cli rpc state.getNonce $SENDER
# Compare to TX nonce
```

**Common Causes**:

| Cause | Diagnostic | Solution |
|-------|-----------|----------|
| Fee too low | Your fee < avg_fee_wei | Increase fee or wait for market to clear |
| Nonce gap | TX nonce > confirmed nonce + 1 | Submit missing earlier nonces |
| Mempool eviction | TX no longer in `mempool.getPending` | Resubmit with higher fee |
| Miner not using mempool | Block template empty or small | Check miner config: `includeMempool: true` |
| TX size too large | Size > max_tx_bytes | Reduce transaction size or data payload |

**Quick Fix**:
```bash
# Increase fee and resubmit (replace-by-fee)
animica-cli tx send \
  --to $RECIPIENT \
  --value $AMOUNT \
  --nonce $NONCE \
  --max-fee $(( $OLD_FEE * 2 ))  # Double the fee
```

### 4. Mempool Out of Sync

**Symptoms**:
- Node A has 100 pending txs
- Node B has 10 pending txs
- Peers connected but mempools diverged

**Diagnostic Steps**:

```bash
# 1. Check mempool sync status
animica-cli rpc debug.mempoolSyncStatus

# 2. Check peer eligibility
animica-cli rpc p2p.listPeers
# Look for peers with tx_relay_eligible=true

# 3. Check reconciliation metrics
animica-cli rpc debug.txRelayStats
# Look for mempool_sync_sent, mempool_sync_recv
```

**Common Causes**:

| Cause | Diagnostic | Solution |
|-------|-----------|----------|
| Reconciliation not running | `mempool_sync_sent = 0` | Check P2P service is running |
| Peer missing txs | Remote peer has fewer txs | Trigger manual sync via `p2p.syncMempool` |
| Network partition | Peers in different partitions | Check network connectivity, restart nodes |
| Different admission policies | Nodes reject same TX for different reasons | Align mempool configs (min_fee, limits) |

**Quick Fix**:
```bash
# Force mempool reconciliation
animica-cli rpc p2p.syncMempool

# Or restart P2P service
systemctl restart animica-p2p
```

### 5. Late-Joining Node Missing Transactions

**Symptoms**:
- Node C connects after txs were broadcast
- Node C's mempool is empty
- Peers already have txs but don't rebroadcast

**Diagnostic Steps**:

```bash
# 1. Check if reconciliation happened
animica-cli rpc debug.txRelayStats
# Look for mempool_sync_recv > 0

# 2. Check known_txids on peers
animica-cli rpc debug.txRelayMetrics
# Look for known_txids_count on each peer

# 3. Manually trigger sync
animica-cli rpc p2p.syncMempool
```

**Expected Behavior**:
- On peer connect, Node C sends `TxMempoolReq`
- Peers respond with `TxMempoolResp` (txids)
- Node C requests unknown txids via `TxGet`
- Peers respond with `TxData`

**Quick Fix**:
```bash
# Force immediate reconciliation on join
animica-cli rpc p2p.syncMempool --force

# Or configure shorter reconciliation interval
# In config/p2p.yaml:
#   reconcile_interval_s: 5.0  # Default: 10.0
```

## Diagnostic Logs

### Enable Debug Logging

```bash
# In config/logging.yaml or environment:
export ANIMICA_LOG_LEVEL=DEBUG
export ANIMICA_MEMPOOL_DEBUG=1
export ANIMICA_MINER_DEBUG=1

# Or in code:
import logging
logging.getLogger("animica.p2p.txrelay").setLevel(logging.DEBUG)
logging.getLogger("animica.rpc.mempool").setLevel(logging.DEBUG)
logging.getLogger("animica.rpc.miner").setLevel(logging.DEBUG)
```

### Key Log Lines to Watch

**Successful Propagation**:
```
[INFO] MempoolService.submit: SUCCESS - tx added, tx_hash=0x1234..., pool_size=5
[INFO] [DIAG] P2P broadcast scheduled for tx 0x1234...
[INFO] TX_RELAY_ACCEPT_LOCAL: hash=0x1234..., bytes=256
[INFO] TX_INV_RECEIVED: peer=node-b, count=1, txids=[1234...]
[INFO] TX_RELAY_MEMPOOL_INSERT_OK: hash=0x1234..., source=peer:node-b
```

**Failed Propagation**:
```
[ERROR] [DIAG] P2P broadcast callback is NOT set for tx 0x1234...
[WARNING] TX_INV_SKIP_NOTFOUND_RECENT: peer=node-b, txid=1234...
[ERROR] Unexpected error in admit_tx: ...
```

**Mining Issues**:
```
[INFO] block template mempool collection: entries=0, total=0
[WARNING] miner.getBlockTemplate: FALLBACK - using _PEND/_FALLBACK_PENDING
[WARNING] Block template is empty or very small (txCount=0)
```

## Performance Tuning

### High-Throughput Configuration

For networks with high transaction volume:

```yaml
# config/mempool.yaml
limits:
  max_txs: 500000              # Increase pool capacity
  max_bytes: 1073741824        # 1 GB total

# config/p2p.yaml
tx_relay:
  inv_batch_size: 500          # Larger batches
  inv_flush_interval_s: 0.05   # Faster flushing
  max_inflight_total: 10000    # More concurrent requests
  reconcile_interval_s: 5.0    # More frequent sync
```

### Low-Latency Configuration

For networks prioritizing fast propagation:

```yaml
# config/p2p.yaml
tx_relay:
  inv_flush_interval_s: 0.01   # Immediate flushing
  request_cooldown_s: 1.0      # Faster retry
  reconcile_interval_s: 3.0    # Very frequent sync
```

### Resource-Constrained Configuration

For nodes with limited memory/CPU:

```yaml
# config/mempool.yaml
limits:
  max_txs: 10000               # Smaller pool
  max_bytes: 10485760          # 10 MB total

# config/p2p.yaml
tx_relay:
  inv_batch_size: 50           # Smaller batches
  max_inflight_total: 256      # Fewer concurrent requests
  reconcile_interval_s: 30.0   # Less frequent sync
```

## Monitoring & Alerts

### Key Metrics to Track

```bash
# Mempool size
watch -n 5 'animica-cli rpc mempool.getStats | jq .count'

# P2P connectivity
watch -n 10 'animica-cli rpc p2p.listPeers | jq length'

# TX relay throughput
watch -n 5 'animica-cli rpc debug.txRelayMetrics | jq "{inv_sent, inv_recv, accepted_count}"'

# Mining activity
watch -n 5 'animica-cli rpc miner.getBlockTemplate ... | jq .txCount'
```

### Alert Thresholds

Recommended alerting rules:

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| Mempool size | > 100k txs | > 150k txs | Investigate eviction, increase limits |
| Peer count | < 5 peers | < 2 peers | Check network connectivity |
| TX propagation delay | > 5s | > 10s | Check P2P config, network latency |
| Block template empty | 3 consecutive | 10 consecutive | Check mempool sync, miner config |
| Mempool divergence | > 20% difference | > 50% difference | Force reconciliation |

## Recovery Procedures

### Mempool Corruption

If mempool state becomes inconsistent:

```bash
# 1. Stop node
systemctl stop animica-node

# 2. Clear mempool persistence
rm -rf /data/mempool/pending.jsonl

# 3. Restart node (will sync from peers)
systemctl start animica-node

# 4. Verify sync
animica-cli rpc mempool.getStats
```

### P2P State Reset

If P2P connections are stuck:

```bash
# 1. Disconnect all peers
animica-cli rpc p2p.disconnectAll

# 2. Clear peer store
rm -rf /data/p2p/peers.json

# 3. Reconnect to seed nodes
# (automatic on next P2P tick)

# 4. Verify connectivity
animica-cli rpc p2p.listPeers
```

### Complete TX Pipeline Reset

Nuclear option for severe issues:

```bash
# 1. Stop node
systemctl stop animica-node

# 2. Clear mempool and P2P state
rm -rf /data/mempool/
rm -rf /data/p2p/

# 3. Restart node
systemctl start animica-node

# 4. Resubmit pending transactions
# (users will need to resubmit)
```

## Testing Tools

### Inject Test Transaction

```bash
# Create test tx
animica-cli tx create \
  --from $SENDER \
  --to $RECIPIENT \
  --value 1000 \
  --nonce $NONCE \
  --max-fee 1000000000 \
  --chain-id 1 \
  --output test-tx.hex

# Submit to Node A
animica-cli --rpc http://node-a:8545 tx sendRaw $(cat test-tx.hex)

# Check propagation to Node B
sleep 2
animica-cli --rpc http://node-b:8545 rpc mempool.has $TXID
```

### Simulate Network Partition

```bash
# Block traffic between Node A and Node B
iptables -A INPUT -s $NODE_B_IP -j DROP
iptables -A OUTPUT -d $NODE_B_IP -j DROP

# Wait and verify mempool divergence
sleep 10
diff \
  <(animica-cli --rpc http://node-a:8545 rpc mempool.getPending | jq -r '.[].hash' | sort) \
  <(animica-cli --rpc http://node-b:8545 rpc mempool.getPending | jq -r '.[].hash' | sort)

# Restore connectivity
iptables -D INPUT -s $NODE_B_IP -j DROP
iptables -D OUTPUT -d $NODE_B_IP -j DROP

# Verify reconciliation
sleep 15
# (Mempools should converge)
```

## Support & Contact

For persistent issues:

1. Collect diagnostic bundle:
```bash
./scripts/collect-diagnostics.sh > diagnostics.tar.gz
```

2. Review logs:
```bash
journalctl -u animica-node -n 1000 > node.log
```

3. Open issue on GitHub with:
   - Node version
   - Configuration (redact sensitive info)
   - Diagnostic bundle
   - Steps to reproduce

## Summary

Most transaction propagation issues fall into these categories:

1. **Connectivity**: P2P not connected or peers ineligible
2. **Configuration**: Mempool callback not bound, mining config wrong
3. **Validation**: TX rejected due to nonce/fee/balance
4. **Priority**: TX fee too low to be mined
5. **Sync**: Mempool reconciliation not working

The debug RPC methods (`debug.traceTx`, `tx.status`, `tx.explainReject`) provide comprehensive visibility into each stage of the pipeline, making issues straightforward to diagnose and fix.
