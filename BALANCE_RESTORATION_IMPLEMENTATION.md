# Automatic Balance Restoration After Node Reset - Implementation Guide

## Overview

This document describes the implementation of automatic balance restoration after resetting an Animica node. This feature ensures that users can recover their balances after a node reset, which is useful for development and testing purposes.

## Problem Statement

When resetting a node using `animica node reset`, all blockchain data including account balances are deleted. While wallet addresses are preserved in `~/.animica/wallets.json`, the balances associated with those addresses are lost. This creates a poor user experience, especially during development and testing.

## Solution

The solution consists of three main components:

### 1. Admin RPC Method (`admin.setBalance`)

A new RPC method that allows direct balance manipulation for development and testing purposes.

**Location**: `rpc/methods/admin.py`

**Features**:
- Only enabled when `ANIMICA_ADMIN_RPC_ENABLED=1` environment variable is set
- Validates and normalizes addresses (bech32 `anim1...`, system addresses, hex `0x...`)
- Accepts balance as integer or hex string
- Provides clear error messages when disabled
- Security-focused: explicit opt-in required

**Usage**:
```bash
# Enable admin RPC when starting node
ANIMICA_ADMIN_RPC_ENABLED=1 animica node up

# Set balance via RPC (example using curl)
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "admin.setBalance",
    "params": ["anim1...", 1000000000000],
    "id": 1
  }'
```

### 2. Balance Restoration Function

Enhanced implementation of `restore_wallet_balances` in `wallet_balances.py`.

**Location**: `python/animica/cli/wallet_balances.py`

**Features**:
- Loads balance backup file created during reset
- Filters to only restore non-zero balances
- Uses `admin.setBalance` RPC method for each address
- Provides detailed progress and error reporting
- Handles admin RPC disabled error gracefully

**Key Changes**:
- Removed placeholder/TODO code
- Added proper RPC error handling
- Clear error messages when admin RPC is not enabled
- Progress logging for each restored address

### 3. CLI Commands

#### New Command: `animica balance restore`

Manual balance restoration command.

**Location**: `python/animica/cli/balance.py`

**Features**:
- Shows preview of what will be restored
- Requires confirmation (unless `--yes` flag)
- Provides helpful error messages
- Supports custom backup file path

**Usage**:
```bash
# Restore from automatic backup
animica balance restore

# Restore from custom backup file
animica balance restore --backup-file ~/my-balances.json

# Skip confirmation
animica balance restore --yes
```

#### Enhanced Command: `animica node reset`

Added `--restore-balances` flag for automatic restoration.

**Location**: `python/animica/cli/node.py`

**New Flag**: `--restore-balances/--no-restore-balances`
- Requires `--up` flag (must restart node to restore)
- Automatically enables admin RPC when starting node
- Restores balances after node is up and ready
- Reports success/failure

**Usage**:
```bash
# Reset and automatically restore balances
animica node reset --up --restore-balances --yes

# Reset, restart without restoration (default)
animica node reset --up --yes
```

## Workflow

### Automatic Restoration (Recommended for Dev/Test)

1. Reset node with automatic restoration:
   ```bash
   animica node reset --up --restore-balances --yes
   ```

This single command will:
- Export current balances to backup file
- Stop and wipe node data
- Start node with admin RPC enabled
- Wait for node to be ready
- Automatically restore all balances from backup

### Manual Restoration

1. Export balances (optional, done automatically during reset):
   ```bash
   animica balance export
   ```

2. Reset node:
   ```bash
   animica node reset --yes
   ```

3. Start node with admin RPC enabled:
   ```bash
   ANIMICA_ADMIN_RPC_ENABLED=1 animica node up
   ```

4. Restore balances:
   ```bash
   animica balance restore
   ```

## Security Considerations

### Why Admin RPC is Disabled by Default

The `admin.setBalance` RPC method allows direct state manipulation, bypassing normal consensus rules. This is useful for testing but dangerous in production.

**Protection Mechanisms**:
1. **Explicit opt-in**: Requires `ANIMICA_ADMIN_RPC_ENABLED=1` environment variable
2. **Clear warnings**: All commands display warnings about dev/test only usage
3. **Disabled by default**: No accidental exposure
4. **Method not found**: When disabled, returns "method not found" error

### Best Practices

- **Never enable admin RPC on production networks**
- **Only use on private devnets or testnets**
- **Disable admin RPC after restoration is complete**
- **Use strong firewall rules if admin RPC is enabled**

## Testing

### Unit Tests

Location: `python/animica/cli/tests/test_wallet_balances.py`

Added tests for:
- `test_restore_wallet_balances_no_backup`: Verify error when no backup exists
- `test_restore_wallet_balances_empty_backup`: Handle empty backup gracefully
- `test_restore_wallet_balances_success`: Test successful restoration with mocked RPC
- `test_restore_wallet_balances_partial_failure`: Test handling of partial failures
- `test_restore_wallet_balances_admin_rpc_disabled`: Test error when admin RPC disabled

Run tests:
```bash
pytest python/animica/cli/tests/test_wallet_balances.py -v
```

### Manual Testing

1. **Setup Test Environment**:
   ```bash
   animica network set devnet
   animica node up
   animica wallet new --label test1
   ```

2. **Mine Some Blocks** (to generate balances):
   ```bash
   animica miner mine-blocks --address test1 --count 10
   animica wallet show test1  # Verify balance > 0
   ```

3. **Test Automatic Restoration**:
   ```bash
   animica node reset --up --restore-balances --yes
   # Wait for process to complete
   animica wallet show test1  # Should show original balance
   ```

4. **Test Manual Restoration**:
   ```bash
   animica node reset --yes
   ANIMICA_ADMIN_RPC_ENABLED=1 animica node up
   animica balance restore --yes
   animica wallet show test1  # Should show original balance
   ```

5. **Test Without Admin RPC** (should fail gracefully):
   ```bash
   animica node reset --yes
   animica node up  # Without ANIMICA_ADMIN_RPC_ENABLED
   animica balance restore  # Should error with helpful message
   ```

## Files Changed

### New Files
1. `rpc/methods/admin.py` - Admin RPC method implementation

### Modified Files
1. `python/animica/cli/wallet_balances.py` - Implement restore function
2. `python/animica/cli/balance.py` - Add restore command
3. `python/animica/cli/node.py` - Add --restore-balances flag
4. `python/animica/cli/tests/test_wallet_balances.py` - Add tests

## Future Enhancements

1. **State DB Direct Access**: Read/write balances directly from state DB without requiring RPC
2. **Batch Restoration**: Restore multiple balances in a single RPC call for efficiency
3. **Selective Restoration**: Choose which addresses to restore
4. **Balance History**: Track balance changes over time
5. **Automatic Backup Scheduling**: Regular background exports

## Troubleshooting

### "Admin RPC methods are disabled"

**Cause**: Node started without `ANIMICA_ADMIN_RPC_ENABLED=1`

**Solution**: Restart node with admin RPC enabled:
```bash
animica node down
ANIMICA_ADMIN_RPC_ENABLED=1 animica node up
animica balance restore
```

### "Balance backup file not found"

**Cause**: No backup was created before reset

**Solution**: Export balances before next reset:
```bash
animica balance export
```

### Restoration fails for some addresses

**Cause**: RPC timeout, network issues, or invalid addresses

**Solution**: 
1. Check node is running and accessible
2. Verify RPC URL is correct
3. Check backup file format is valid
4. Try restoring individual addresses manually

## Conclusion

This implementation provides a complete solution for balance preservation during node resets. It maintains security by requiring explicit opt-in for admin RPC while providing a smooth user experience for development and testing workflows.

The feature is fully integrated with existing CLI commands and follows the established patterns in the codebase. It includes comprehensive error handling, clear user feedback, and proper security controls.
