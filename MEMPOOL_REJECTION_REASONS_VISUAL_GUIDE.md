# Visual Guide: Mempool List Rejection Reasons

## Overview

This document shows visual examples of the enhanced `animica mempool list` command output with specific rejection reasons.

## Scenario 1: Transactions with Rejection Reasons

### Command
```bash
animica mempool list
```

### Output (Enhanced)
```
RPC_TARGET=http://127.0.0.1:8545/rpc NODE_ID=0xc4da0cc227 SOURCE=default
Chain: id=1 genesis=0x98451b8497
Peer: 0xc4da0cc227  Head: 274
Mempool: id=0x772221568090 path=/data/chain-1/mempool/pending.jsonl
Peer-known txids (sample):
  peer=0xc4d211f1c4 conn_id=0xfae7d5a7-f known_txids=0 sample=[n/a]
  peer=0x999ff0572e conn_id=0x2a1ea1c4-8 known_txids=0 sample=[n/a]
  peer=0xbd032d6207 conn_id=0xfa347b01-a known_txids=2 sample=[0x538fd8b570b70c8deed421c184d53514063f5f2b89c02edc098d734fbae44081, 0x7a9b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b]
Auto-imported peer transactions: requested=2, newly_visible=0 (timed out after 2.0s)
  Rejection details:
    0x538fd8b57 state=received_invalid reason=invalid_signature peer=0xbd032d620 attempts=1
    0x7a9b2c3d4 state=dropped_evicted reason=insufficient_balance peer=0xbd032d620 attempts=2
Mempool is empty (no pending transactions)
```

### What's New? ✨
- **Specific rejection reasons** for each transaction
- **Transaction state** (received_invalid, dropped_evicted)
- **Peer information** showing which peer provided the transaction
- **Attempt count** showing how many times the transaction was requested

## Scenario 2: Mix of States

### Output
```
Auto-imported peer transactions: requested=5, newly_visible=0 (timed out after 2.0s)
  Rejection details:
    0x1234567890 state=received_invalid reason=invalid_signature peer=0xpeer111111 attempts=1
    0x2345678901 state=dropped_evicted reason=insufficient_balance peer=0xpeer222222 attempts=2
    0x3456789012 state=dropped_evicted reason=nonce_conflict peer=0xpeer333333 attempts=1
    0x4567890123 state=received_invalid reason=hash_mismatch peer=0xpeer444444 attempts=3
    0x5678901234 state=requested peer=0xpeer555555 attempts=1
Mempool is empty (no pending transactions)
```

### Analysis
- **Transaction 1**: Invalid signature - likely malformed or corrupted transaction
- **Transaction 2**: Insufficient balance - sender doesn't have enough funds
- **Transaction 3**: Nonce conflict - transaction nonce is out of order
- **Transaction 4**: Hash mismatch - transaction data doesn't match its hash (3 attempts!)
- **Transaction 5**: Still being requested - no rejection yet

## Scenario 3: Many Rejected Transactions

### Output
```
Auto-imported peer transactions: requested=25, newly_visible=0 (timed out after 2.0s)
  Rejection details:
    0xabc1234567 state=received_invalid reason=invalid_signature peer=0xpeer111111 attempts=1
    0xbcd2345678 state=dropped_evicted reason=insufficient_balance peer=0xpeer222222 attempts=2
    0xcde3456789 state=received_invalid reason=invalid_signature peer=0xpeer333333 attempts=1
    0xdef4567890 state=dropped_evicted reason=nonce_conflict peer=0xpeer444444 attempts=1
    0xef56789012 state=received_invalid reason=hash_mismatch peer=0xpeer555555 attempts=1
    0xf678901234 state=dropped_evicted reason=insufficient_balance peer=0xpeer666666 attempts=3
    0x0789012345 state=received_invalid reason=invalid_signature peer=0xpeer777777 attempts=1
    0x1890123456 state=dropped_evicted reason=insufficient_balance peer=0xpeer888888 attempts=2
    0x2901234567 state=received_invalid reason=invalid_signature peer=0xpeer999999 attempts=1
    0x3012345678 state=dropped_evicted reason=nonce_conflict peer=0xpeeraaaaaaa attempts=1
    0x4123456789 state=received_invalid reason=hash_mismatch peer=0xpeerbbbbbb attempts=2
    0x5234567890 state=dropped_evicted reason=insufficient_balance peer=0xpeercccccc attempts=1
    0x6345678901 state=received_invalid reason=invalid_signature peer=0xpeerdddddd attempts=1
    0x7456789012 state=dropped_evicted reason=insufficient_balance peer=0xpeereeeeee attempts=2
    0x8567890123 state=received_invalid reason=invalid_signature peer=0xpeerffffff attempts=1
    0x9678901234 state=dropped_evicted reason=nonce_conflict peer=0xpeer101010 attempts=1
    0xa789012345 state=received_invalid reason=hash_mismatch peer=0xpeer202020 attempts=1
    0xb890123456 state=dropped_evicted reason=insufficient_balance peer=0xpeer303030 attempts=3
    0xc901234567 state=received_invalid reason=invalid_signature peer=0xpeer404040 attempts=1
    0xd012345678 state=dropped_evicted reason=nonce_conflict peer=0xpeer505050 attempts=2
    ... and 5 more (see node logs for full details)
Mempool is empty (no pending transactions)
```

### What's New? ✨
- Shows up to **20 transactions** to avoid overwhelming output
- Displays **"... and X more"** message when there are additional rejections
- Users can check node logs for complete details

## Scenario 4: Fallback to Generic Note

When no transaction state information is available:

### Output
```
Auto-imported peer transactions: requested=2, newly_visible=0 (timed out after 2.0s)
  Note: Transactions may have been:
    • Rejected during validation (hash mismatch, invalid signature)
    • Failed mempool admission (insufficient balance, nonce conflict, low fee)
    • Not available on peers (responded with TX_NOTFOUND)
  Check node logs for: TX_DATA_ADMIT_RESULT, TX_REJECTED, TX_NOTFOUND
Mempool is empty (no pending transactions)
```

This fallback ensures backwards compatibility and handles cases where:
- Transaction state tracking is not available
- TxRelayService doesn't provide state information
- System is in degraded mode

## Scenario 5: Successful Import

When transactions successfully arrive:

### Output
```
Auto-imported peer transactions: requested=3, newly_visible=3
Pending transactions (3):
    1. 0x538fd8b570b70c8deed421c184d53514063f5f2b89c02edc098d734fbae44081 nonce=5 status=pending from=0x1234...5678 fee=21000 size=120
    2. 0x7a9b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b nonce=6 status=pending from=0x2345...6789 fee=21000 size=125
    3. 0x9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d nonce=7 status=pending from=0x3456...7890 fee=21000 size=130
```

No rejection details shown - all transactions successfully imported! ✅

## Common Rejection Reasons Reference

| Reason | Description | Suggested Action |
|--------|-------------|------------------|
| `invalid_signature` | Transaction signature verification failed | Check transaction signing, verify keys |
| `insufficient_balance` | Sender doesn't have enough balance | Add funds to sender account |
| `nonce_conflict` | Nonce is already used or out of order | Check account nonce, wait for pending txs |
| `hash_mismatch` | Transaction hash doesn't match content | Verify transaction data integrity |
| `in_chain` | Transaction already in blockchain | No action needed - already processed |
| `low_fee` | Transaction fee below minimum | Increase transaction fee |

## Transaction States Reference

| State | Description |
|-------|-------------|
| `requested` | Transaction requested from peer, awaiting response |
| `announced_only` | Transaction announced but not yet requested |
| `received_valid_pending` | Transaction received and valid, pending admission |
| `received_invalid` | Transaction received but failed validation |
| `dropped_evicted` | Transaction was dropped from mempool |
| `accepted_in_mempool` | Transaction successfully added to mempool |

## Benefits

### 🎯 Immediate Debugging
No need to grep through logs - rejection reasons are right in front of you!

### 📊 Pattern Recognition
Spot patterns quickly:
- Multiple `invalid_signature` errors? → Check peer connectivity/data integrity
- Many `insufficient_balance` errors? → Network might be processing many transactions
- Repeated `nonce_conflict` errors? → Check transaction ordering

### 👥 Peer Tracking
See which peers are providing problematic transactions:
- Identifies peers sending invalid data
- Helps diagnose network-wide issues
- Useful for debugging P2P connectivity

### 🔄 Retry Insights
Track how many times each transaction was attempted:
- High attempt count? → Persistent network issue
- Single attempt failures? → Likely validation/balance problems

---

**Note**: This enhanced output maintains backwards compatibility. If transaction state information is unavailable, the command falls back to the generic note format.
