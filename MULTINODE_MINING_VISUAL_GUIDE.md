# Visual Guide: Multi-Node Mining Fix

## The Problem

```
┌─────────────────────────────────────────────────────────┐
│ Before Fix: CRASHES AND SYNC FAILURES                   │
└─────────────────────────────────────────────────────────┘

Node A (Mining to wallet X)          Node B (Mining to wallet X)
        │                                     │
        ▼                                     ▼
   Mine Block H1                         Mine Block H2
   Height: 100                           Height: 100
   Parent: P99                           Parent: P99
   Hash: 0xabc...                        Hash: 0xdef...
   Nonce: 12345                          Nonce: 67890
        │                                     │
        ├─────────────────────────────────────┤
        │         P2P Block Exchange          │
        │                                     │
        ▼                                     ▼
  Receive H2                            Receive H1
  Check: H2.parent != current_head      Check: H1.parent != current_head
        │                                     │
        ▼                                     ▼
  ❌ RAISE EXCEPTION                     ❌ RAISE EXCEPTION
  Mining loop crashes                   Mining loop crashes
        │                                     │
        ▼                                     ▼
  🔥 Node stuck                         🔥 Node stuck
  ❌ Won't sync                          ❌ Won't sync
  ❌ Won't mine                          ❌ Won't mine
```

## The Solution

```
┌─────────────────────────────────────────────────────────┐
│ After Fix: GRACEFUL CONFLICT RESOLUTION                 │
└─────────────────────────────────────────────────────────┘

Node A (Mining to wallet X)          Node B (Mining to wallet X)
        │                                     │
        ▼                                     ▼
   Mine Block H1                         Mine Block H2
   Height: 100                           Height: 100
   Parent: P99                           Parent: P99
   Hash: 0xabc...                        Hash: 0xdef...
   Nonce: 12345                          Nonce: 67890
        │                                     │
        ├─────────────────────────────────────┤
        │         P2P Block Exchange          │
        │                                     │
        ▼                                     ▼
  Receive H2                            Receive H1
  Check: H2.parent != current_head      Check: H1.parent != current_head
        │                                     │
        ▼                                     ▼
  ⚠️  LOG WARNING                        ⚠️  LOG WARNING
  Pass to block import                  Pass to block import
        │                                     │
        ├─────────────────────────────────────┤
        │         Fork Choice Evaluation      │
        │                                     │
        │    Compare H1 vs H2:                │
        │    - Proof of Work                  │
        │    - Weight/Difficulty              │
        │    - Timestamp                      │
        │                                     │
        │    Winner: H1 (better PoW)          │
        │                                     │
        ├─────────────────────────────────────┤
        │                                     │
        ▼                                     ▼
  ✅ H1 is canonical                     ✅ H1 is canonical
  H2 marked as orphan                   H2 marked as orphan
        │                                     │
        ▼                                     ▼
  Continue mining                       Continue mining
  Next block: Height 101                Next block: Height 101
  Parent: H1                            Parent: H1
        │                                     │
        ▼                                     ▼
  ✅ Synced and mining                  ✅ Synced and mining
```

## Key Changes

### 1. Removed Strict Check
```python
# BEFORE (line 4915)
if parent_hash != head_hash:
    raise RpcError("STALE_TEMPLATE")  # ❌ Crash!

# AFTER (line 4987)
if parent_hash != head_hash:
    log.warning("Parent mismatch - possible multi-node mining")
    # ✅ Pass to fork choice - no crash!
```

### 2. Added Warnings
```python
# Template requests tracked (line 145)
_ACTIVE_MINING_ADDRESSES = {}

# Warning when detected (line 4673)
if _track_mining_address(payout_address):
    log.warning("MULTI_NODE_MINING_DETECTED")
```

### 3. Enhanced Diagnostics
```python
# Parent mismatch rejection (line 4955)
if "parent" in reason_lower:
    log.warning("Parent mismatch - possible multi-node mining conflict")
    log.info("Hint: Use different wallet for each node")
```

## Flow Comparison

### Before Fix
```
Template Request → Mine Block → Submit Block
                                    ↓
                              Parent ≠ Head?
                                    ↓
                                   YES
                                    ↓
                          ❌ RAISE EXCEPTION
                                    ↓
                          🔥 CRASH & STUCK
```

### After Fix
```
Template Request → Mine Block → Submit Block
                                    ↓
                              Parent ≠ Head?
                                    ↓
                                   YES
                                    ↓
                          ⚠️  LOG WARNING
                                    ↓
                          Pass to Block Import
                                    ↓
                            Fork Choice
                          ┌─────┴─────┐
                          │           │
                       Block A     Block B
                          │           │
                    Better PoW    Worse PoW
                          │           │
                      ✅ Accept   📦 Orphan
                          │
                    Update Head
                          │
                   ✅ CONTINUE MINING
```

## Log Messages

### Before Fix
```
ERROR: STALE_TEMPLATE
  reason: stale_template
  expected_head: 0xabc...
  got_parent: 0xdef...
  
🔥 Mining crashed
❌ Node stopped syncing
```

### After Fix
```
WARNING: Block parent mismatch - possible multi-node mining or reorg
  expected_head: 0xabc...
  got_parent: 0xdef...
  block_height: 100
  
INFO: Fork choice evaluating competing blocks

INFO: Block accepted/rejected based on fork choice

✅ Mining continues
✅ Node stays synced
```

## Best Practices

### ✅ Recommended: Unique Wallet Per Node
```
Node A → Wallet A → Mine → No conflicts
Node B → Wallet B → Mine → No conflicts
Node C → Wallet C → Mine → No conflicts

✅ No competing blocks
✅ No orphaned blocks
✅ Maximum mining efficiency
```

### ⚠️ Same Wallet: Expect Conflicts
```
Node A ──┐
Node B ──┼──→ Wallet X → Mine → Competing blocks
Node C ──┘

⚠️  Some blocks orphaned
⚠️  Reduced efficiency
⚠️  Warnings in logs
✅ But no crashes!
✅ Nodes stay synced
```

## Verification

### Test Setup
```bash
# Terminal 1: Node A
animica node start --mining-address=anim1abc...

# Terminal 2: Node B (same wallet!)
animica node start --mining-address=anim1abc...
```

### Expected Output
```
# Node A logs
INFO: Mining started to wallet anim1abc...
WARNING: MULTI_NODE_MINING_DETECTED
  payout_address: anim1abc...
  warning: Mining to same wallet on multiple nodes can cause conflicts
  recommendation: Use different wallet for each node
INFO: Mined block height=100 hash=0x123...
WARNING: Block parent mismatch - possible multi-node mining
INFO: Fork choice selected block 0x123...
INFO: Continue mining height=101

# Node B logs
INFO: Mining started to wallet anim1abc...
WARNING: MULTI_NODE_MINING_DETECTED
INFO: Mined block height=100 hash=0x456...
WARNING: Block parent mismatch - possible multi-node mining
INFO: Fork choice selected block 0x123... (same as Node A)
INFO: Continue mining height=101
```

### Success Criteria
- ✅ Both nodes mine successfully
- ✅ Warnings appear in logs
- ✅ No crashes occur
- ✅ Fork choice resolves conflicts
- ✅ Both nodes converge on same chain
- ✅ Mining continues on both nodes
- ✅ Sync stays healthy

## Summary

| Aspect | Before Fix | After Fix |
|--------|-----------|-----------|
| **Crashes** | ❌ Yes, mining loop crashes | ✅ No crashes |
| **Sync** | ❌ Stops after crash | ✅ Continues normally |
| **Recovery** | ❌ Manual restart needed | ✅ Automatic recovery |
| **Diagnostics** | ❌ Generic error | ✅ Clear warnings + hints |
| **User Guidance** | ❌ None | ✅ Best practices shown |
| **Fork Choice** | ❌ Never reached | ✅ Handles conflicts |

## Conclusion

The fix transforms multi-node mining from a **crash scenario** into a **graceful conflict resolution scenario**:

- **Before:** Crash → Stuck → Manual recovery needed
- **After:** Warning → Fork choice → Automatic resolution

Users mining to the same wallet on multiple nodes will now see **warnings** instead of **crashes**, and nodes will **continue syncing** instead of **getting stuck**.

**Best practice remains:** Use unique wallet per node for maximum efficiency!
