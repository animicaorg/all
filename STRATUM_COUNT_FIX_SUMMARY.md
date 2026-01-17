# Stratum Miner --count Fix: Shares vs Blocks

## Problem Statement

The `animica miner stratum --count N` command was designed to stop after N items, but it was checking **blocks found** instead of **shares accepted**. This caused the miner to spam shares indefinitely in development/testing scenarios where shares are frequently accepted but actual blocks are rarely found.

### Example of the Bug

```bash
$ animica miner stratum --address anim1... --url stratum+tcp://127.0.0.1:3333 --count 2
Target blocks: 2

✓ Share accepted (nonce: 0xe6a8d8b1)
✓ Share accepted (nonce: 0xe6a8d8b2)
✓ Share accepted (nonce: 0xe6a8d8b3)
... [continues indefinitely]
✓ Share accepted (nonce: 0xe6a8d8c2)
✓ Share accepted (nonce: 0xe6a8d8c3)
```

The miner would never stop because `blocks_found` stayed at 0, even though `shares_accepted` kept growing.

## Root Cause

The mining loop condition was:

```python
while mining_active and stats["blocks_found"] < count:
```

And the stopping logic only triggered when a block was found:

```python
if isinstance(result, dict) and result.get("is_block"):
    stats["blocks_found"] += 1
    if stats["blocks_found"] >= count:
        mining_active = False
        break
```

This meant:
- If shares were accepted but no blocks found → miner continues forever
- Only when a block is found → check if we should stop

## Solution

Changed the logic to track **shares accepted** instead of **blocks found**:

### 1. Main Loop Condition

```python
# OLD (broken)
while mining_active and stats["blocks_found"] < count:

# NEW (fixed)
while mining_active and stats["shares_accepted"] < count:
```

### 2. Stopping Logic

```python
# OLD (broken)
if isinstance(result, dict) and result.get("is_block"):
    stats["blocks_found"] += 1
    if stats["blocks_found"] >= count:
        mining_active = False
        break
else:
    typer.echo(f"✓ Share accepted (nonce: {hex(nonce)})")

# NEW (fixed)
stats["shares_accepted"] += 1

if isinstance(result, dict) and result.get("is_block"):
    stats["blocks_found"] += 1
    typer.secho(f"✓ BLOCK FOUND! Share {stats['shares_accepted']}/{count}")
else:
    typer.echo(f"✓ Share accepted (nonce: {hex(nonce)})")

# Stop after reaching share count
if stats["shares_accepted"] >= count:
    mining_active = False
    break
```

### 3. Help Text and Documentation

Updated to clarify the behavior:

**Before:**
```
--count N    Stop after N blocks accepted by node
```

**After:**
```
--count N    Stop after N shares accepted (not blocks found)
```

### 4. Output Messages

**Before:**
```
Target blocks: 2
Shares: 10/15 | Blocks: 0/2
  Blocks found:    0/2
  Shares accepted: 10
```

**After:**
```
Target shares: 2
Shares: 2/2 | Blocks: 0
  Shares accepted: 2/2
  Blocks found:    0
```

## Expected Behavior After Fix

```bash
$ animica miner stratum --address anim1... --url stratum+tcp://127.0.0.1:3333 --count 2
Target shares: 2

✓ Share accepted (nonce: 0xe6a8d8b1)
✓ Share accepted (nonce: 0xe6a8d8b2)

Mining Summary:
  Shares accepted: 2/2
  Blocks found:    0

✓ Mining target reached!
```

The miner now stops after 2 shares are accepted, regardless of whether any blocks were found.

## Use Cases

### Development/Testing
Most common use case: mine a few shares quickly to test the system without waiting for actual blocks.

```bash
# Mine 5 shares for testing
animica miner stratum --address anim1... --url stratum+tcp://127.0.0.1:3333 --count 5
```

### Production Mining
If you want to mine until finding actual blocks, you can set a high share count or use a dedicated mining tool.

```bash
# Mine 1000 shares (will include any blocks found)
animica miner stratum --address anim1... --url stratum+tcp://127.0.0.1:3333 --count 1000
```

## Files Changed

1. `python/animica/cli/mining.py`
   - Updated `miner_stratum` function logic
   - Updated help text and docstring
   - Updated output messages

2. `STRATUM_IMPLEMENTATION_SUMMARY.md`
   - Updated feature descriptions
   - Updated command examples

3. `STRATUM_MINING_GUIDE.md`
   - Updated command reference
   - Updated examples

## Testing

Created a logic verification script (`/tmp/test_stratum_count_fix.py`) that confirms:

✅ **Test 1: Stop after N shares**
- Miner stops after exactly N shares
- Works when no blocks are found

✅ **Test 2: Track blocks correctly**
- Miner still counts blocks found
- Stops after N shares even when blocks are found

✅ **Test 3: Confirm old logic was broken**
- Old logic would continue indefinitely
- Old logic never stopped when no blocks were found

## Backwards Compatibility

This is a **breaking change** in behavior, but it's a **bug fix** that aligns with user expectations:

- **Old behavior**: Spam shares indefinitely if no blocks found (broken)
- **New behavior**: Stop after N shares as expected (fixed)

Users who relied on the old behavior (waiting for blocks) should:
- Use a high share count (e.g., `--count 1000`)
- Or use a dedicated mining tool that runs continuously

## Related Issues

This fix addresses the issue reported in the problem statement where users saw:
- Continuous spam of "✓ Share accepted" messages
- Miner never stopping even though `--count 2` was specified
- Confusion about whether `--count` means shares or blocks
