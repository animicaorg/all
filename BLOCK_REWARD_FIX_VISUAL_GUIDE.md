# Visual Guide: Block Reward Double-Crediting Fix

## The Problem (Before Fix)

```
┌─────────────────────────────────────────────────────────────┐
│                    Internal Miner Flow                       │
└─────────────────────────────────────────────────────────────┘

1. Miner A creates block with coinbase transactions
   ┌────────────────────┐
   │ Block Height: 100  │
   │ Coinbase TX: 300   │  ← Coinbase transaction included
   │ Other TXs: [...]   │
   └────────────────────┘

2. Block propagates to Node B
   
3. Node B imports block via import_block()
   
4. _apply_block_state() executes:
   
   ┌─────────────────────────────────────┐
   │ Execute Transactions:               │
   │  - Coinbase TX → Credit 300 ANM ✓  │  ← First credit
   │  - Other TXs → Process             │
   └─────────────────────────────────────┘
   
   ┌─────────────────────────────────────┐
   │ Call _apply_block_reward():         │
   │  - Credit 300 ANM ✓                │  ← Second credit (BUG!)
   └─────────────────────────────────────┘

5. Result: 300 + 300 = 600 ANM credited ❌

   ┌────────────────────────────┐
   │ Expected: 300 ANM          │
   │ Actual:   600 ANM          │
   │ Excess:   300 ANM (100%)   │
   └────────────────────────────┘
```

## The Solution (After Fix)

```
┌─────────────────────────────────────────────────────────────┐
│              Internal Miner Flow (Fixed)                     │
└─────────────────────────────────────────────────────────────┘

1. Miner A creates block with coinbase transactions
   ┌────────────────────┐
   │ Block Height: 100  │
   │ Coinbase TX: 300   │  ← Coinbase transaction included
   │ Other TXs: [...]   │
   └────────────────────┘

2. Block propagates to Node B
   
3. Node B imports block via import_block()
   
4. _apply_block_state() executes:
   
   ┌─────────────────────────────────────┐
   │ Check for Coinbase Transactions:    │
   │  - Found TxKind.COINBASE = 3 ✓     │
   └─────────────────────────────────────┘
   
   ┌─────────────────────────────────────┐
   │ Execute Transactions:               │
   │  - Coinbase TX → Credit 300 ANM ✓  │  ← Only credit
   │  - Other TXs → Process             │
   └─────────────────────────────────────┘
   
   ┌─────────────────────────────────────┐
   │ Skip _apply_block_reward()          │
   │  - Has coinbase TX → SKIP ✓        │  ← Fix: Skip double credit
   └─────────────────────────────────────┘

5. Result: 300 ANM credited ✅

   ┌────────────────────────────┐
   │ Expected: 300 ANM          │
   │ Actual:   300 ANM          │
   │ Excess:   0 ANM            │
   └────────────────────────────┘
```

## External Miner Flow (Still Works)

```
┌─────────────────────────────────────────────────────────────┐
│              External Miner Flow (Unchanged)                 │
└─────────────────────────────────────────────────────────────┘

1. External miner gets template (NO coinbase transactions)
   ┌────────────────────┐
   │ Block Height: 100  │
   │ Coinbase TX: NONE  │  ← No coinbase transaction
   │ Other TXs: [...]   │
   └────────────────────┘

2. Miner solves PoW, submits block
   
3. Node imports block via import_block()
   
4. _apply_block_state() executes:
   
   ┌─────────────────────────────────────┐
   │ Check for Coinbase Transactions:    │
   │  - NOT found                        │
   └─────────────────────────────────────┘
   
   ┌─────────────────────────────────────┐
   │ Execute Transactions:               │
   │  - No coinbase TX → No reward      │
   │  - Other TXs → Process             │
   └─────────────────────────────────────┘
   
   ┌─────────────────────────────────────┐
   │ Call _apply_block_reward()          │
   │  - Credit 300 ANM ✓                │  ← Only credit
   └─────────────────────────────────────┘

5. Result: 300 ANM credited ✅

   ┌────────────────────────────┐
   │ Expected: 300 ANM          │
   │ Actual:   300 ANM          │
   │ Excess:   0 ANM            │
   └────────────────────────────┘
```

## Decision Tree

```
                    Block Import
                         │
                         ▼
        ┌────────────────────────────┐
        │ Does block contain         │
        │ TxKind.COINBASE?          │
        └────────────────────────────┘
                    │
         ┌──────────┴──────────┐
         │                     │
        YES                   NO
         │                     │
         ▼                     ▼
┌─────────────────┐   ┌─────────────────┐
│ Execute txs     │   │ Execute txs     │
│ (includes       │   │ (no coinbase)   │
│  coinbase)      │   │                 │
│                 │   │                 │
│ ✓ Reward        │   │ ✗ No reward    │
│   credited      │   │   yet          │
└─────────────────┘   └─────────────────┘
         │                     │
         ▼                     ▼
┌─────────────────┐   ┌─────────────────┐
│ SKIP            │   │ CALL            │
│ _apply_block_   │   │ _apply_block_   │
│ reward()        │   │ reward()        │
│                 │   │                 │
│ (already done)  │   │ ✓ Reward        │
│                 │   │   credited      │
└─────────────────┘   └─────────────────┘
         │                     │
         └──────────┬──────────┘
                    │
                    ▼
            ┌───────────────┐
            │ Total: 300    │
            │ ANM credited  │
            │ (CORRECT)     │
            └───────────────┘
```

## Code Change Summary

```python
# BEFORE (Bug):
def _apply_block_state(self, block: Block) -> bool:
    block_env = make_block_env(block.header, self.params)
    apply_block(block.txs, ...)  # Executes coinbase tx → 300 ANM
    self._apply_block_reward(block)  # Credits reward → 300 ANM again
    # TOTAL: 600 ANM ❌

# AFTER (Fixed):
def _apply_block_state(self, block: Block) -> bool:
    block_env = make_block_env(block.header, self.params)
    apply_block(block.txs, ...)  # Executes coinbase tx → 300 ANM
    
    # NEW: Check for coinbase transactions
    has_coinbase_tx = any(
        getattr(getattr(tx, "unsigned", None), "kind", None) == TxKind.COINBASE
        for tx in block.txs
    )
    
    if has_coinbase_tx:
        # Skip _apply_block_reward - already done
        log.debug("block contains coinbase; skipping separate reward")
    else:
        # Call _apply_block_reward - need to apply
        self._apply_block_reward(block)
    # TOTAL: 300 ANM ✅
```

## Test Coverage

```
┌────────────────────────────────────────┐
│ Test: Block WITH Coinbase TX          │
│                                        │
│ Input:  Block with TxKind.COINBASE    │
│ Check:  has_coinbase_tx = True        │
│ Action: Skip _apply_block_reward()    │
│ Result: Single reward ✅               │
└────────────────────────────────────────┘

┌────────────────────────────────────────┐
│ Test: Block WITHOUT Coinbase TX       │
│                                        │
│ Input:  Block with only TRANSFER txs  │
│ Check:  has_coinbase_tx = False       │
│ Action: Call _apply_block_reward()    │
│ Result: Single reward ✅               │
└────────────────────────────────────────┘

┌────────────────────────────────────────┐
│ Test: Empty Block                     │
│                                        │
│ Input:  Block with no transactions    │
│ Check:  has_coinbase_tx = False       │
│ Action: Call _apply_block_reward()    │
│ Result: Single reward ✅               │
└────────────────────────────────────────┘

┌────────────────────────────────────────┐
│ Test: Multiple Coinbase TXs          │
│                                        │
│ Input:  Block with 3 coinbase txs    │
│ Check:  has_coinbase_tx = True        │
│ Action: Skip _apply_block_reward()    │
│ Result: Single reward ✅               │
└────────────────────────────────────────┘
```

## Impact Analysis

### Before Fix
```
Network State Divergence:
┌───────────┐     ┌───────────┐
│  Node A   │     │  Node B   │
│ (Internal │     │ (Imports  │
│  Miner)   │     │  block)   │
├───────────┤     ├───────────┤
│ Balance:  │     │ Balance:  │
│  100,300  │     │  100,600  │  ← Divergence!
│  ANM      │     │  ANM      │
└───────────┘     └───────────┘
```

### After Fix
```
Network State Consistency:
┌───────────┐     ┌───────────┐
│  Node A   │     │  Node B   │
│ (Internal │     │ (Imports  │
│  Miner)   │     │  block)   │
├───────────┤     ├───────────┤
│ Balance:  │     │ Balance:  │
│  100,300  │     │  100,300  │  ← Consistent!
│  ANM      │     │  ANM      │
└───────────┘     └───────────┘
```

## Summary

**Problem**: Double rewards (600 ANM instead of 300 ANM)
**Cause**: Both transaction execution AND _apply_block_reward crediting
**Fix**: Skip _apply_block_reward when block has coinbase transactions
**Result**: Single reward (300 ANM) regardless of mining method

✅ **Fix Complete and Tested**
