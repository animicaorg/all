# Mining Rewards Persistence After Node Reset - Implementation

## Problem Statement

When resetting a node using `animica node reset`, all blockchain data is deleted including the state database that contains account balances. While wallet addresses are preserved (stored in `~/.animica/wallets.json`), the mining rewards and other balances associated with those addresses are lost.

**Impact**: Users lose all mining rewards when they reset their node for troubleshooting or testing purposes.

## Root Cause

The directory structure is:
- **Wallet files**: `~/.animica/wallets.json` (preserved - contains addresses)
- **Chain state**: `~/.animica/chain-{chain_id}/animica.db` (deleted - contains balances)

During node reset:
1. The chain data directory (`~/.animica/chain-{chain_id}/`) is removed
2. Wallet files at `~/.animica/wallets.json` are preserved
3. Result: addresses remain but balances are lost

## Solution Implemented

### 1. Balance Export Before Reset

Added automatic balance export functionality to `animica node reset` command:

```bash
animica node reset --backup-balances  # Default: enabled
```

**How it works:**
- Before deleting chain data, the command checks if the node is running
- If running, it queries balances for all addresses in `wallets.json` via RPC
- Balances are exported to a JSON backup file
- Location: `~/.animica/chain-{chain_id}_balances_backup.json`

**Backup file format:**
```json
{
  "version": 1,
  "exported_at": "2024-01-12T18:30:00Z",
  "data_dir": "/home/user/.animica/chain-1337",
  "rpc_url": "http://127.0.0.1:8545/rpc",
  "balances": [
    {
      "label": "miner1",
      "address": "anim1...",
      "hex_address": "0x1234...",
      "balance": 1000000000
    }
  ]
}
```

### 2. Manual Balance Export Command

Added new CLI command for manual balance management:

```bash
# Export balances
animica balance export
animica balance export --network testnet
animica balance export --output ~/my-balances.json

# View backup
animica balance show
animica balance show ~/my-balances.json
animica balance show --network testnet
```

### 3. Enhanced Reset Warning

The `animica node reset` command now:
- Shows clear warnings about data loss
- Lists what will be deleted
- Indicates whether balances will be backed up
- Provides information about the backup file after reset

Example output:
```
⚠ WARNING: This will delete all blockchain data!
The following will be removed: docker volumes, host data at ~/.animica/chain-1337

Consequences:
  • All mining rewards and balances will be lost
  • Transaction history will be deleted
  • The chain will restart from genesis
  • Wallet addresses are preserved (but balances are not)

✓ Node is running - wallet balances will be backed up before reset

Proceed with reset of devnet data? [y/N]:
```

## Files Modified

1. **`python/animica/cli/wallet_balances.py`** (NEW)
   - Core logic for exporting/restoring wallet balances
   - RPC calls to query balances
   - Backup file management

2. **`python/animica/cli/balance.py`** (NEW)
   - CLI commands: `balance export` and `balance show`
   - User-friendly interface for balance management

3. **`python/animica/cli/node.py`** (MODIFIED)
   - Added `--backup-balances` option to `reset` command
   - Automatic balance export before reset
   - Enhanced warnings and messaging

4. **`python/animica/cli/main.py`** (MODIFIED)
   - Registered `balance` subcommand
   - Updated documentation

5. **`python/animica/cli/tests/test_wallet_balances.py`** (NEW)
   - Unit tests for balance export functionality

## Usage Examples

### Reset with Balance Backup (Default)

```bash
# Export balances before reset (default behavior)
animica node reset

# The command will:
# 1. Check if node is running
# 2. Export balances to backup file
# 3. Perform reset
# 4. Show location of backup file
```

### Reset Without Balance Backup

```bash
# Skip balance export (not recommended)
animica node reset --no-backup-balances
```

### Manual Balance Export

```bash
# Export balances manually before reset
animica balance export

# View the exported balances
animica balance show

# Export to custom location
animica balance export --output ~/my-balances.json
```

### After Reset: View What Was Lost

```bash
# After reset, view the backup to see what balances were lost
animica balance show

# Output shows:
# - Exported timestamp
# - Network information
# - List of addresses with their balances
```

## Limitations and Future Work

### Current Limitations

1. **No Automatic Restoration**: Balances are exported but not automatically restored after reset
   - Manual restoration would require an admin RPC method to directly set balances
   - This is intentionally not implemented to avoid security issues

2. **Node Must Be Running**: Balance export only works if the node is accessible
   - If node is not running, no backup is created
   - Users should start the node before reset to enable backup

3. **RPC Access Required**: Balance export requires RPC connectivity
   - Must be able to query `state.getBalance` via RPC
   - Firewall or network issues may prevent export

### Future Enhancements

1. **State Database Direct Export**: Read balances directly from state DB file
   - Would work even if node is not running
   - Requires implementing state DB file reader

2. **Balance Restoration**: Implement secure balance restoration
   - Admin RPC method: `admin.setBalance(address, amount)`
   - Require authentication/authorization
   - Only available in development/test environments

3. **Selective Reset**: Reset without deleting state DB
   - Keep balances but reset blockchain
   - More complex but preserves all balances

4. **Automatic Backup Scheduling**: Regular balance exports
   - Cron job or daemon to periodically export balances
   - Protection against unexpected data loss

## Testing

### Manual Testing Steps

1. **Setup Test Environment**
   ```bash
   animica network set devnet
   animica node up
   animica wallet new --label test1
   ```

2. **Mine Some Blocks**
   ```bash
   animica miner mine-blocks --address test1 --count 10
   animica wallet show test1  # Verify balance > 0
   ```

3. **Export Balances Manually**
   ```bash
   animica balance export
   animica balance show  # Verify export succeeded
   ```

4. **Reset with Backup**
   ```bash
   animica node reset --yes
   # Check that backup file was created
   # Check that balance backup location is shown
   ```

5. **Verify Backup After Reset**
   ```bash
   animica balance show  # Should show pre-reset balances
   animica node up  # Start fresh node
   animica wallet show test1  # Balance should be 0 (expected)
   ```

### Automated Tests

Run the test suite:
```bash
pytest python/animica/cli/tests/test_wallet_balances.py -v
```

## Documentation Updates

Added/updated:
- CLI help text for `node reset` command
- New `balance` command documentation
- Main CLI docstring
- This implementation summary

## Security Considerations

1. **Backup File Location**: Stored in user's home directory
   - Only readable by the user (file permissions)
   - Contains sensitive balance information
   - Should not be shared publicly

2. **No Automatic Restoration**: Intentional security decision
   - Prevents accidental or malicious balance manipulation
   - Users must mine again to earn rewards
   - Maintains blockchain integrity

3. **RPC Authentication**: Balance queries use standard RPC
   - No special permissions required for read operations
   - Existing RPC security applies

## Migration Notes

- **Backward Compatible**: Existing workflows continue to work
- **Opt-Out Available**: Can disable with `--no-backup-balances`
- **No Breaking Changes**: Only additions to CLI

## Summary

This implementation provides:
✅ Automatic balance backup before node reset
✅ Manual balance export/view commands  
✅ Clear warnings about data loss
✅ Preservation of balance history for reference
✅ Foundation for future restoration features

Users can now safely reset their nodes while maintaining a record of their mining rewards and balances for future reference or manual restoration.
