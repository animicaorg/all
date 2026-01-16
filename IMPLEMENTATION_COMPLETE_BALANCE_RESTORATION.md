# Implementation Complete: Automatic Balance Restoration After Node Reset

## Summary

Successfully implemented automatic balance restoration after resetting an Animica node. This feature allows users to preserve and restore their balances (mining rewards, etc.) when resetting a node for troubleshooting or testing purposes.

## Problem Solved

Previously, when users ran `animica node reset`, all blockchain data including account balances were permanently lost. While wallet addresses were preserved in `~/.animica/wallets.json`, the balances associated with those addresses were gone. This created a poor user experience, especially during development and testing.

## Solution Implemented

### Core Components

1. **Admin RPC Method (`admin.setBalance`)**
   - New RPC endpoint for direct balance manipulation
   - Only enabled with `ANIMICA_ADMIN_RPC_ENABLED=1` environment variable
   - Security-focused: explicit opt-in required
   - Clear error messages when disabled

2. **Balance Restoration Function**
   - Loads balance backup file created during reset
   - Calls `admin.setBalance` for each non-zero balance
   - Detailed progress reporting
   - Graceful error handling

3. **CLI Commands**
   - `animica balance restore` - Manual restoration command
   - `animica node reset --restore-balances` - Automatic restoration
   - Enhanced `animica balance export` and `show` commands

### Files Created/Modified

#### New Files
- `rpc/methods/admin.py` - Admin RPC method implementation
- `BALANCE_RESTORATION_IMPLEMENTATION.md` - Detailed documentation

#### Modified Files
- `rpc/methods/__init__.py` - Register admin module
- `python/animica/cli/wallet_balances.py` - Implement restore function
- `python/animica/cli/balance.py` - Add restore command
- `python/animica/cli/node.py` - Add --restore-balances flag
- `python/animica/cli/tests/test_wallet_balances.py` - Add comprehensive tests

## Usage

### Automatic Restoration (Recommended)

```bash
# Single command: backup, reset, restart, and restore
animica node reset --up --restore-balances --yes
```

This will:
1. Export current balances to backup file
2. Stop and wipe node data
3. Start node with admin RPC enabled
4. Wait for node to be ready (with health checks)
5. Automatically restore all balances from backup

### Manual Restoration

```bash
# Step 1: Reset node
animica node reset --yes

# Step 2: Start node with admin RPC enabled
ANIMICA_ADMIN_RPC_ENABLED=1 animica node up

# Step 3: Restore balances
animica balance restore
```

### View Backups

```bash
# Show latest backup for active network
animica balance show

# Show specific backup file
animica balance show ~/my-balances.json
```

## Security

### Why Admin RPC is Disabled by Default

The `admin.setBalance` RPC method allows direct state manipulation, bypassing normal consensus rules. This is useful for testing but dangerous in production.

**Protection Mechanisms:**
- Requires `ANIMICA_ADMIN_RPC_ENABLED=1` environment variable
- Clear warnings in all commands about dev/test only usage
- Returns "method not found" when disabled
- Documented as dev/test only

### Best Practices

✅ **DO:**
- Use on private devnets or testnets
- Disable admin RPC after restoration is complete
- Keep balance backups secure

❌ **DON'T:**
- Enable admin RPC on production networks
- Share admin RPC endpoints publicly
- Rely on this for production workflows

## Testing

### Unit Tests Added

- `test_restore_wallet_balances_no_backup` - Error when no backup exists
- `test_restore_wallet_balances_empty_backup` - Handle empty backup
- `test_restore_wallet_balances_success` - Successful restoration
- `test_restore_wallet_balances_partial_failure` - Partial failures
- `test_restore_wallet_balances_admin_rpc_disabled` - Admin RPC disabled error

Run tests:
```bash
pytest python/animica/cli/tests/test_wallet_balances.py -v
```

### Manual Testing Workflow

1. Set up test environment:
   ```bash
   animica network set devnet
   animica node up
   animica wallet new --label test1
   ```

2. Mine blocks to generate balances:
   ```bash
   animica miner mine-blocks --address test1 --count 10
   animica wallet show test1  # Should show balance > 0
   ```

3. Test automatic restoration:
   ```bash
   animica node reset --up --restore-balances --yes
   # Wait for completion
   animica wallet show test1  # Should show original balance
   ```

## Code Quality

### Code Review Feedback Addressed

✅ **Issue**: Admin module not registered in RPC server
   - **Fixed**: Added `rpc.methods.admin` to `_iter_builtin_modules()`

✅ **Issue**: Broad exception handling
   - **Fixed**: Use specific exceptions (ImportError, ModuleNotFoundError)

✅ **Issue**: Brittle state DB access
   - **Fixed**: Clear error messages, removed speculative fallbacks

✅ **Issue**: Fixed sleep duration for node readiness
   - **Fixed**: Proper health check loop with HTTP and RPC verification

### Key Improvements

1. **Robust Health Checking**: Waits up to 60s with 2s interval, checking both `/healthz` and RPC availability
2. **Clear Error Messages**: Helpful guidance when admin RPC is disabled or state DB doesn't support direct updates
3. **Comprehensive Testing**: Full test coverage for success, failure, and edge cases
4. **Documentation**: Detailed implementation guide and usage examples

## Known Limitations

1. **State DB Support Required**: The `admin.setBalance` method requires the state DB to have a direct balance setter. If not available, an informative error message is provided.

2. **Dev/Test Only**: This is intentionally designed for development and testing. Production environments should not use this feature.

3. **No Transaction History**: Restored balances don't include transaction history - only the final balance is restored.

## Future Enhancements

Potential improvements documented in `BALANCE_RESTORATION_IMPLEMENTATION.md`:
- State DB direct access (without RPC)
- Batch restoration for efficiency
- Selective restoration (choose which addresses)
- Balance history tracking
- Automatic backup scheduling

## Conclusion

This implementation provides a complete, secure, and user-friendly solution for balance restoration after node resets. It maintains security through explicit opt-in requirements while providing a smooth experience for development and testing workflows.

The feature is fully integrated with existing CLI commands, follows established code patterns, includes comprehensive error handling, and provides clear user feedback throughout the process.

## Documentation

- Implementation guide: `BALANCE_RESTORATION_IMPLEMENTATION.md`
- Code review addressed: All feedback items fixed
- Tests: Comprehensive unit test coverage
- Security: Clear warnings and explicit opt-in required

## Ready for Review

The implementation is complete and ready for:
1. ✅ Code review (feedback already addressed)
2. ✅ Unit testing (tests included and passing)
3. ⏳ Manual testing (test workflow documented)
4. ⏳ Integration testing (requires full environment)
5. ⏳ Documentation review
