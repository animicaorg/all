# Visual Guide: Block Reward Fix

## The Problem (Before Fix)

```
┌─────────────────────────────────────────────────────────────────┐
│ Block B arrives at Node                                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                  ┌───────────────────────┐
                  │ Check: Already in DB? │
                  └───────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │                   │
                   NO                  YES (Duplicate!)
                    │                   │
                    ▼                   ▼
         ┌──────────────────┐  ┌──────────────────┐
         │ First Import     │  │ Duplicate Import │
         │ • Validate       │  │ • Add to fork    │
         │ • Store block    │  │   choice         │
         │ • Apply state    │  │ • If became_best:│
         │ • Apply reward   │  │   ❌ _apply_reorg│
         │ • Balance +5 ANM │  │   ❌ Re-apply    │
         │                  │  │      state       │
         └──────────────────┘  │   ❌ Re-apply    │
                              │      reward       │
                              │   ❌ Balance +5   │
                              │      AGAIN!       │
                              └──────────────────┘

Result: Balance increases by 10 ANM (5 + 5) instead of 5 ANM!
```

## The Fix (After Fix)

```
┌─────────────────────────────────────────────────────────────────┐
│ Block B arrives at Node                                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                  ┌───────────────────────┐
                  │ Check: Already in DB? │
                  └───────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │                   │
                   NO                  YES (Duplicate!)
                    │                   │
                    ▼                   ▼
         ┌──────────────────┐  ┌──────────────────┐
         │ First Import     │  │ Duplicate Import │
         │ • Validate       │  │ • Add to fork    │
         │ • Store block    │  │   choice         │
         │ • Apply state    │  │ • If became_best:│
         │ • Apply reward   │  │   ✅ Update head │
         │ • Balance +5 ANM │  │      pointer     │
         │                  │  │   ✅ Update      │
         └──────────────────┘  │      canon height│
                              │   ✅ Skip state   │
                              │   ✅ Skip reward  │
                              │   ✅ Balance      │
                              │      unchanged    │
                              └──────────────────┘

Result: Balance increases by 5 ANM (correct!)
```

## Multi-Block Scenario

### Before Fix (WRONG)

```
Time  Block  Import Type  State Change    Balance
────  ─────  ───────────  ──────────────  ──────────
  0   Gen    First        +81M ANM        81,000,000
  1   B1     First        +5 ANM          81,000,005
  2   B1     Duplicate    +5 ANM ❌       81,000,010 ❌
  3   B2     First        +5 ANM          81,000,015
  4   B1     Duplicate    +5 ANM ❌       81,000,020 ❌
  5   B2     Duplicate    +5 ANM ❌       81,000,025 ❌

Final: 81,000,025 ANM (expected: 81,000,010 ANM)
Error: +15 ANM extra! ❌
```

### After Fix (CORRECT)

```
Time  Block  Import Type  State Change    Balance
────  ─────  ───────────  ──────────────  ──────────
  0   Gen    First        +81M ANM        81,000,000
  1   B1     First        +5 ANM          81,000,005
  2   B1     Duplicate    No change ✅    81,000,005
  3   B2     First        +5 ANM          81,000,010
  4   B1     Duplicate    No change ✅    81,000,010
  5   B2     Duplicate    No change ✅    81,000,010

Final: 81,000,010 ANM (expected: 81,000,010 ANM)
Error: 0 ANM ✅
```

## Cross-Node Consistency

### Before Fix (INCONSISTENT)

```
           Node A                      Node B
          ┌────────┐                  ┌────────┐
Time 0    │Genesis │                  │Genesis │
          │81M ANM │                  │81M ANM │
          └────────┘                  └────────┘
              │                           │
Time 1        ▼                           ▼
          ┌────────┐                  ┌────────┐
          │Block 1 │                  │Block 1 │
          │+5 ANM  │                  │+5 ANM  │
          │81.005M │                  │81.005M │
          └────────┘                  └────────┘
              │                           │
Time 2        ▼                           ▼
          ┌────────┐              ┌──────────────┐
          │Block 2 │              │Block 1 (dup) │
          │+5 ANM  │              │+5 ANM ❌     │
          │81.010M │              │81.010M       │
          └────────┘              └──────────────┘
              │                           │
                                     Time 3
                                          ▼
                                  ┌──────────────┐
                                  │Block 2       │
                                  │+5 ANM        │
                                  │81.015M ❌    │
                                  └──────────────┘

Node A: 81.010M ANM ✅
Node B: 81.015M ANM ❌
Difference: 5M ANM ❌ INCONSISTENT!
```

### After Fix (CONSISTENT)

```
           Node A                      Node B
          ┌────────┐                  ┌────────┐
Time 0    │Genesis │                  │Genesis │
          │81M ANM │                  │81M ANM │
          └────────┘                  └────────┘
              │                           │
Time 1        ▼                           ▼
          ┌────────┐                  ┌────────┐
          │Block 1 │                  │Block 1 │
          │+5 ANM  │                  │+5 ANM  │
          │81.005M │                  │81.005M │
          └────────┘                  └────────┘
              │                           │
Time 2        ▼                           ▼
          ┌────────┐              ┌──────────────┐
          │Block 2 │              │Block 1 (dup) │
          │+5 ANM  │              │No change ✅  │
          │81.010M │              │81.005M       │
          └────────┘              └──────────────┘
              │                           │
                                     Time 3
                                          ▼
                                  ┌──────────────┐
                                  │Block 2       │
                                  │+5 ANM        │
                                  │81.010M ✅    │
                                  └──────────────┘

Node A: 81.010M ANM ✅
Node B: 81.010M ANM ✅
Difference: 0 ✅ CONSISTENT!
```

## Code Flow Comparison

### Before Fix (Buggy)

```
import_block(Block B)
  │
  ├─ Check: header in DB?
  │  └─ YES (duplicate)
  │
  ├─ Add to fork choice
  │  └─ result.became_best?
  │     └─ YES
  │        │
  │        ├─ _apply_reorg(detached, attached, best) ❌
  │        │  │
  │        │  ├─ _apply_state_reorg() ❌
  │        │  │  │
  │        │  │  ├─ For each attached block: ❌
  │        │  │  │  │
  │        │  │  │  ├─ _apply_block_state(block) ❌
  │        │  │  │  │  │
  │        │  │  │  │  ├─ apply_block(txs) ❌
  │        │  │  │  │  │
  │        │  │  │  │  └─ _apply_block_reward(block) ❌
  │        │  │  │  │     └─ credit(address, reward) ❌
  │        │  │  │  │        └─ Balance += reward ❌ WRONG!
  │
  └─ Return DUPLICATE
```

### After Fix (Correct)

```
import_block(Block B)
  │
  ├─ Check: header in DB?
  │  └─ YES (duplicate)
  │
  ├─ Add to fork choice
  │  └─ result.became_best?
  │     └─ YES
  │        │
  │        ├─ set_canonical_head(height, hash) ✅
  │        │  └─ Update pointer only ✅
  │        │
  │        ├─ set_canonical_height(height) ✅
  │        │  └─ Track mining blocks ✅
  │        │
  │        └─ log.info("duplicate became best") ✅
  │           └─ Monitor but no state change ✅
  │
  └─ Return DUPLICATE
     └─ State unchanged ✅
        └─ Balance unchanged ✅ CORRECT!
```

## Summary

### Key Insight
**Duplicate blocks need fork choice tracking (for consensus) but NOT state re-application (already done).**

### The Fix in One Line
**If duplicate becomes best: update pointers, skip state.**

### Impact
✅ Each block rewards exactly once  
✅ Deterministic state across nodes  
✅ Consistent balances everywhere  
✅ No reward inflation  
✅ Better performance
