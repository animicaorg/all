# Visual Guide: Block Reward Double-Prevention Fix

## The Problem (Before Fix)

```
HEIGHT 100: Two Miners Find Valid Blocks
═══════════════════════════════════════════════════════════

Miner A                          Miner B
   │                                │
   │ Mines block                    │ Mines block
   │ nonce = 12345                  │ nonce = 67890
   │ hash = 0xaaa...                │ hash = 0xbbb...
   │ extra = {A's address}          │ extra = {B's address}
   ▼                                ▼
┌─────────────┐                 ┌─────────────┐
│  Block A    │                 │  Block B    │
│  Height: 100│                 │  Height: 100│
│  Hash: 0xaaa│                 │  Hash: 0xbbb│
└─────────────┘                 └─────────────┘
       │                                │
       │ Submit                         │ Submit
       ▼                                ▼
   ┌───────────────────────────────────────┐
   │           Node                        │
   │  ┌──────────────────────────────┐    │
   │  │  import_block(Block A)        │    │
   │  │  ✓ Not duplicate (0xaaa)      │    │
   │  │  ✓ Apply state                │    │
   │  │  ✓ Reward Miner A: 300 ANM    │◄───┼── ❌ BUG: First reward
   │  └──────────────────────────────┘    │
   │                                       │
   │  ┌──────────────────────────────┐    │
   │  │  import_block(Block B)        │    │
   │  │  ✓ Not duplicate (0xbbb)      │    │
   │  │  ✓ Reorg (B wins)             │    │
   │  │  ✓ Apply state                │    │
   │  │  ✓ Reward Miner B: 300 ANM    │◄───┼── ❌ BUG: Second reward!
   │  └──────────────────────────────┘    │
   └───────────────────────────────────────┘

RESULT: 600 ANM minted instead of 300 ANM ❌
```

## The Solution (After Fix)

```
HEIGHT 100: Two Miners Find Valid Blocks
═══════════════════════════════════════════════════════════

Miner A                          Miner B
   │                                │
   │ Mines block                    │ Mines block
   │ nonce = 12345                  │ nonce = 67890
   │ hash = 0xaaa...                │ hash = 0xbbb...
   │ extra = {A's address}          │ extra = {B's address}
   ▼                                ▼
┌─────────────┐                 ┌─────────────┐
│  Block A    │                 │  Block B    │
│  Height: 100│                 │  Height: 100│
│  Hash: 0xaaa│                 │  Hash: 0xbbb│
└─────────────┘                 └─────────────┘
       │                                │
       │ Submit                         │ Submit
       ▼                                ▼
   ┌────────────────────────────────────────────┐
   │           Node (WITH FIX)                  │
   │  ┌───────────────────────────────────┐    │
   │  │  import_block(Block A)             │    │
   │  │  ✓ Not duplicate (0xaaa)           │    │
   │  │  ✓ Check rewarded[100]: None       │    │
   │  │  ✓ Apply state                     │    │
   │  │  ✓ Reward Miner A: 300 ANM         │◄───┼── ✓ First reward
   │  │  ✓ Track: rewarded[100] = 0xaaa    │    │
   │  └───────────────────────────────────┘    │
   │                                            │
   │  ┌───────────────────────────────────┐    │
   │  │  import_block(Block B)             │    │
   │  │  ✓ Not duplicate (0xbbb)           │    │
   │  │  ✓ Fork choice: B wins             │    │
   │  │  ✓ Reorg: Detach A, Attach B       │    │
   │  │  ✓ Clear: del rewarded[100]        │◄───┼── ✓ Clear old tracking
   │  │  ✓ Revert state to height 99       │    │
   │  │  ✓ Check rewarded[100]: None       │    │
   │  │  ✓ Apply state                     │    │
   │  │  ✓ Reward Miner B: 300 ANM         │◄───┼── ✓ New reward (A's reverted)
   │  │  ✓ Track: rewarded[100] = 0xbbb    │    │
   │  └───────────────────────────────────┘    │
   └────────────────────────────────────────────┘

RESULT: 300 ANM minted (correct!) ✅
        Only canonical block's miner rewarded
```

## Key Invariants

```
✓ At most ONE block per height is tracked
✓ Only canonical blocks get rewards
✓ Detached blocks have tracking cleared
✓ State revert removes old rewards
✓ Attached blocks get new rewards
✓ Total supply = genesis + (height × reward_per_block)
```

---

**Status**: ✅ Fixed
**File**: `core/chain/block_import.py`
**Lines Added**: 59
**Backward Compatible**: Yes
