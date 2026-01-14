# Automatic Balance Correction Guide

This guide explains how to detect and automatically correct balance inflation caused by state rebuild bugs.

## Background

The balance inflation bug (documented in `BALANCE_INFLATION_FIX_COMPLETE.md`) caused rewards to be re-applied multiple times when state rebuilds occurred. While the root cause has been fixed with state height tracking, affected nodes may still have inflated balances that need correction.

## Detection

### Using the CLI Tool

The simplest way to detect inflation is using the automatic detection tool:

```bash
# Dry run (detection only, no changes)
python tools/correct_balance_inflation.py \
  --db-path ~/.animica/chain-1337/state.db \
  --dry-run
```

This will scan all accounts and report any with detected inflation.

### Using the RPC API

You can also check via RPC:

```bash
curl -X POST http://localhost:8545 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "state.detectBalanceInflation",
    "params": [100],
    "id": 1
  }'
```

Response:
```json
{
  "jsonrpc": "2.0",
  "result": {
    "inflated_accounts": [
      {
        "address": "anim1...",
        "current_balance": "0x...",
        "corrected_balance": "0x...",
        "inflation_factor": 5,
        "explanation": "5x inflated (4 rebuilds), ~18564 blocks"
      }
    ],
    "total_inflated": 1,
    "scan_complete": true
  },
  "id": 1
}
```

## Automatic Correction

### Using the CLI Tool

To apply corrections automatically:

```bash
# Apply corrections (requires confirmation)
python tools/correct_balance_inflation.py \
  --db-path ~/.animica/chain-1337/state.db \
  --apply
```

The tool will:
1. Scan all accounts
2. Detect inflated balances
3. Show a summary of changes
4. Ask for explicit confirmation ("CONFIRM")
5. Apply corrections in a batch
6. Generate an audit trail

**Example output:**
```
================================================================================
⚠️  INFLATION DETECTED!
================================================================================
Accounts with inflation: 2
Total inflated balance: 564100.000000000 ANM
Total corrected balance: 112820.000000000 ANM
Total excess to remove: 451280.000000000 ANM

Sample of affected accounts:
  1. anim1zqquzgffx7r... 464100.000000000 ANM -> 92820.000000000 ANM (5x)
  2. anim1abc123def45... 100000.000000000 ANM -> 20000.000000000 ANM (5x)

================================================================================
⚠️  WARNING: About to modify account balances
================================================================================
This will correct 2 accounts
Audit trail will be saved to: ~/.animica/balance_corrections_20260114_012000.json

Type 'CONFIRM' to proceed with corrections: CONFIRM

Corrected anim1zqquzgffx7r...: 464100.000000000 ANM -> 92820.000000000 ANM
Corrected anim1abc123def45...: 100000.000000000 ANM -> 20000.000000000 ANM

================================================================================
✓ Corrections applied successfully
================================================================================
Corrected: 2/2 accounts
Audit trail: ~/.animica/balance_corrections_20260114_012000.json

NEXT STEPS:
1. Restart your node to ensure changes take effect
2. Verify balances are now correct
3. Review the audit trail for details
```

### Using the RPC API

For programmatic correction (e.g., from a GUI or admin tool):

```bash
# Dry run first (recommended)
curl -X POST http://localhost:8545 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "state.correctBalanceInflation",
    "params": [true, null],
    "id": 1
  }'

# Apply corrections
curl -X POST http://localhost:8545 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "state.correctBalanceInflation",
    "params": [false, null],
    "id": 1
  }'
```

You can also target specific addresses:

```bash
curl -X POST http://localhost:8545 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "state.correctBalanceInflation",
    "params": [false, ["anim1...", "anim1..."]],
    "id": 1
  }'
```

## Detection Logic

The tool uses a threshold-based approach to minimize false positives:

1. **Threshold**: Only balances representing > 10,000 blocks (~50,000 ANM) are checked
2. **Factor Detection**: Checks if the balance is divisible by factors 2-10
3. **First Factor**: Returns the smallest factor that divides cleanly
4. **Clean Multiples**: Only flags balances that are exact multiples of the block reward (5 ANM = 5,000,000,000 nANM)

### Examples

| Balance | Blocks | Detection | Factor | Corrected |
|---------|--------|-----------|--------|-----------|
| 50 ANM | 10 | No (below threshold) | - | - |
| 50,000 ANM | 10,000 | No (not divisible) | - | - |
| 100,000 ANM | 20,000 | Yes | 2x | 50,000 ANM |
| 464,100 ANM | 92,820 | Yes | 2x | 232,050 ANM |

*Note: The algorithm returns the first factor, so a 5x inflated balance (92,820 blocks) will be detected as 2x because 92,820 is divisible by 2.*

## Audit Trail

Every correction generates an audit trail JSON file with:

```json
{
  "corrected_at": "2026-01-14T01:20:00.000Z",
  "total_corrections": 2,
  "corrections": [
    {
      "address": "0x...",
      "timestamp": "2026-01-14T01:20:00.000Z",
      "old_balance": 464100000000000,
      "new_balance": 92820000000000,
      "inflation_factor": 5,
      "explanation": "5x inflated (4 rebuilds), ~18564 blocks"
    }
  ]
}
```

## Safety Considerations

1. **Backup First**: Always backup your state database before applying corrections
2. **Dry Run**: Always run with `--dry-run` first to preview changes
3. **Verify**: After correction, verify balances are as expected
4. **Restart**: Restart the node after applying corrections
5. **Audit Trail**: Keep the audit trail for records and potential rollback

## Troubleshooting

### Tool doesn't detect inflation

If you expect inflation but it's not detected:
- **Check threshold**: Balances under 10,000 blocks are not flagged
- **Check divisibility**: Balance must be cleanly divisible by a factor 2-10
- **Manual inspection**: Use the old `check_balance_inflation.py` tool for manual checks

### False positives

If the tool flags a balance incorrectly:
- **Large legitimate balance**: Balances from extensive mining may trigger false positives
- **Use targeted correction**: Specify only the addresses you know are inflated
- **Adjust threshold**: For mature chains, consider modifying the 10,000 block threshold

### Corrections don't take effect

If balances remain inflated after correction:
1. Ensure you restarted the node
2. Check the audit trail was created
3. Verify the state DB was not restored from backup
4. Check logs for errors during batch write

## Recovery Steps

If something goes wrong:

1. **Restore from backup**:
   ```bash
   cp ~/.animica/chain-1337/state.db.backup ~/.animica/chain-1337/state.db
   ```

2. **Re-sync from genesis** (nuclear option):
   ```bash
   rm ~/.animica/chain-1337/state.db
   # Node will rebuild state from blocks
   ```

3. **Manual correction** (for single addresses):
   ```python
   from core.db.kv import open_kv
   from core.db.state_db import StateDB
   
   kv = open_kv("~/.animica/chain-1337/state.db")
   state_db = StateDB(kv)
   
   addr = bytes.fromhex("...")  # 32-byte address
   corrected_balance = 92820000000000  # nANM
   
   state_db.set_balance(addr, corrected_balance)
   state_db.close()
   ```

## Integration with GUI/Tools

For wallet GUI or admin tools, use the RPC methods:

```typescript
// Detect inflation
const result = await rpc.call('state.detectBalanceInflation', [100]);
if (result.total_inflated > 0) {
  // Show warning to user
  showInflationWarning(result.inflated_accounts);
  
  // Offer correction
  if (userConfirms()) {
    await rpc.call('state.correctBalanceInflation', [false, null]);
  }
}
```

## Prevention

The root cause (state rebuild bug) has been fixed in `core/chain/block_import.py` with state height tracking. New installations are not affected. This correction tool is only needed for nodes that experienced the bug before the fix was deployed.

## Support

For issues or questions:
- GitHub Issues: https://github.com/animicaorg/all/issues
- Documentation: `BALANCE_INFLATION_FIX_COMPLETE.md`
- Discord: [Community Support]
