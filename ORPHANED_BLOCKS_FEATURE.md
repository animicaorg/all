# Orphaned Block Detection and Visualization

## Overview

This feature makes orphaned blocks highly visible throughout the Animica blockchain system, including the explorer UI, miner dashboard, RPC responses, and logs.

## What is an Orphaned Block?

An **orphaned block** is a valid block that was mined but is not part of the canonical (main) blockchain. This happens when:

1. Two or more miners find valid blocks at roughly the same height
2. The network accepts one block as canonical (the "winner")
3. The other blocks become "orphaned" - they exist in the database but aren't part of the main chain
4. **Crucially: Miners receive NO REWARDS for orphaned blocks** because only canonical blocks credit rewards

## Detection Logic

The system detects orphaned blocks by comparing:
- The block's hash
- The canonical block hash at that height

If they don't match, the block is orphaned.

### Implementation: `rpc/methods/block.py`

```python
def _is_block_orphaned(block_hash: str | None, height: int | None) -> bool:
    """
    Check if a block is orphaned (not part of the canonical chain).
    
    Returns True if the block exists but is not canonical at its height.
    """
    # Query canonical hash at this height
    canonical_hash = deps.get_canonical_hash(height)
    
    # Compare with block's hash
    return canonical_hash != block_hash
```

## User-Facing Changes

### 1. Explorer UI (explorer2 & explorer-web)

**Block Detail Pages:**
- 🔴 **Red "ORPHANED" badge** next to block height
- ⚠️ **Warning banner** explaining the block was orphaned and no rewards were paid
- Visual styling (red border/background) to make orphaned blocks obvious

**Block Lists:**
- ⚠️ **Warning icon** next to orphaned blocks in tables
- Red highlighting for orphaned rows
- Strikethrough on reward amounts (showing they were NOT received)

### 2. Miner Dashboard

**Recent Blocks Table:**
- 🔴 **Red highlighting** for orphaned block rows
- ⚠️ **"ORPHANED - No Reward" label** in mobile view
- Strikethrough on reward amounts
- Clear visual distinction from successful blocks

### 3. RPC API

**Added `orphaned` field to block responses:**

```json
{
  "number": 12345,
  "hash": "0x...",
  "orphaned": true,
  ...
}
```

This field is included in:
- `chain.getBlockByHash()`
- `chain.getBlockByNumber()`
- `eth_getBlockByHash()`
- `eth_getBlockByNumber()`

### 4. Logging

**Enhanced mining logs with orphaned warnings:**

#### Fork Race Detection:
```
⚠️ ORPHANED: Discarding mined block that would have been orphaned 
due to head update during mining (fork race). Another block was accepted 
at this height while we were mining, so this block would not receive rewards.
```

#### Reward Check:
```
⚠️ ORPHANED?: Block reward not credited! 
This may indicate the block was orphaned (not part of canonical chain) 
and lost a fork race. Check if another block exists at this height.
```

#### Successful Mining:
```
✓ ACCEPTED: Block mined and reward credited | height=12345 | reward=5000000000 nANM
```

## TypeScript Types

Updated type definitions in `explorer2/shared/src/types.ts`:

```typescript
export interface BlockSummary {
  height: number
  hash: Hash
  orphaned?: boolean  // NEW: Indicates if block is orphaned
  ...
}

export interface BlockDetail {
  height: number
  hash: Hash
  orphaned?: boolean  // NEW: Indicates if block is orphaned
  ...
}
```

## Testing

Run the test suite to verify orphaned block detection:

```bash
python test_orphaned_block_detection.py
```

Tests cover:
1. Detection logic correctness
2. Block view includes orphaned flag
3. TypeScript types are updated

## Benefits

1. **Transparency**: Miners immediately know if their block was orphaned
2. **Clarity**: Clear explanation that no rewards were received
3. **Debugging**: Easier to diagnose fork races and network issues
4. **User Experience**: No confusion about missing rewards

## Related Files

- `rpc/methods/block.py` - Detection logic and RPC integration
- `rpc/methods/miner.py` - Enhanced mining logs
- `explorer2/web/src/pages/BlockDetailPage.tsx` - Explorer2 UI
- `explorer-web/src/pages/Blocks/BlockDetailPage.tsx` - Explorer UI
- `explorer-web/src/components/tables/BlocksTable.tsx` - Block table component
- `apps/miner-dashboard/src/components/Tables/BlocksTable.tsx` - Miner dashboard
- `explorer2/shared/src/types.ts` - TypeScript type definitions
- `test_orphaned_block_detection.py` - Test suite

## Future Enhancements

Potential improvements:
1. Add orphaned block statistics to dashboard
2. Track orphan rate per miner/pool
3. Alert users when orphan rate exceeds threshold
4. Historical orphaned block analysis
5. Network-wide orphan rate monitoring
