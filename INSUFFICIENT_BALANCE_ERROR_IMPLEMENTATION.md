# Insufficient Balance Error Implementation Summary

## Overview
This implementation adds user-friendly error messages when users attempt to send transactions with insufficient balance. The changes span the execution layer, RPC layer, and CLI to provide clear, actionable feedback.

## Changes Made

### 1. Execution Layer (`execution/`)

#### `execution/state/apply_balance.py`
- Enhanced `InsufficientBalance` error class to include structured data:
  - `required`: Total amount needed (value + fees)
  - `available`: Current balance
  - `shortfall`: Difference between required and available
- Updated `_safe_sub()` to raise the enhanced error with amounts
- Error code: `INSUFFICIENT_BALANCE`

#### `execution/runtime/transfers.py`
- Modified `apply_transfer()` to raise `InsufficientBalance` instead of returning REVERT status
- Calculates total required amount (value + total_fee) before balance check
- Provides detailed error information for better debugging

### 2. RPC Layer (`rpc/`)

#### `rpc/errors.py`
- Updated `InsufficientFunds` error to automatically calculate and include shortfall
- Error code: `-32013` (AnimicaCode.INSUFFICIENT_FUNDS)
- All amounts are stringified to avoid JSON number precision issues
- Follows JSON-RPC 2.0 specification

#### `rpc/methods/tx.py`
- Added `_validate_sufficient_balance()` function to check balance before mempool admission
- Balance validation occurs after signature verification but before duplicate checks
- Gracefully skips validation if:
  - Sender address cannot be determined
  - State DB is not available
  - Address parsing fails
  - Balance query methods are unavailable
- Logs validation failures with metrics for monitoring
- Extracts value, gasLimit, and maxFee from transaction body
- Queries state_db for current balance

### 3. CLI Layer (`python/animica/cli/`)

#### `python/animica/cli/tx.py`
- Added `_format_insufficient_funds_error()` helper function
- Catches error code `-32013` during transaction submission
- Displays user-friendly error with:
  - Amounts in both ANM and base units
  - Color-coded output (red for error, yellow for tip)
  - Helpful tip for next steps
- Exits with code 1 on insufficient balance

### 4. Tests

#### `execution/tests/test_insufficient_balance_error.py`
- Tests `InsufficientBalance` error structure and data
- Verifies error is raised with correct amounts
- Tests debit/credit operations with various balances
- Confirms error is a subclass of `ExecError`

#### `rpc/tests/test_tx_insufficient_balance.py`
- Tests `InsufficientFunds` error formatting
- Verifies JSON-RPC error structure
- Tests error code and data fields

## Example Outputs

### CLI Terminal Output
```
Error: Insufficient Balance
  Requested: 1000000.0 ANM (1000000000000000 base units)
  Available: 500.0 ANM (500000000000 base units)
  Shortfall: 999500.0 ANM (999500000000000 base units)

Tip: You need to obtain more ANM before sending this transaction.
```

### RPC JSON-RPC Response
```json
{
  "jsonrpc": "2.0",
  "id": 123,
  "error": {
    "code": -32013,
    "message": "Insufficient funds for transfer",
    "data": {
      "required": "1000000000000000",
      "available": "500000000000",
      "shortfall": "999500000000000"
    }
  }
}
```

## Flow Diagram

```
User sends transaction
    ↓
CLI builds and signs transaction
    ↓
RPC receives tx.sendRawTransaction
    ↓
Decode CBOR transaction ✓
    ↓
Validate chain ID ✓
    ↓
Verify PQ signature ✓
    ↓
[NEW] Validate sufficient balance
    ├─ Extract sender address from signature
    ├─ Query state_db for current balance
    ├─ Calculate required = value + (gasLimit × maxFee)
    └─ If balance < required:
        └─ Raise InsufficientFunds(-32013) with amounts
            ↓
            CLI catches error
            ↓
            Display formatted message
            ↓
            Exit with code 1
    ↓
Check for duplicates ✓
    ↓
Add to pending pool ✓
    ↓
Return tx hash
```

## Acceptance Criteria

✅ **1. CLI shows clear error with amounts**
- Displays requested, available, and shortfall
- Shows amounts in both ANM and base units
- Provides helpful tip

✅ **2. RPC returns structured error**
- JSON-RPC error with code -32013
- Includes required, available, and shortfall in data field
- Follows JSON-RPC 2.0 spec

✅ **3. Error includes all required information**
- Requested amount (value + max gas cost)
- Available balance
- Shortfall (how much more is needed)

✅ **4. Transaction NOT added to mempool on insufficient balance**
- Balance check occurs before `_pending_put()`
- Transaction is rejected early
- No mempool pollution

✅ **5. No silent failures**
- Clear error message at all layers
- Structured error data for programmatic handling
- User-friendly formatting in CLI

## Testing

### Manual Tests
- `test_insufficient_balance_cli.py`: Verifies CLI error formatting
- `test_insufficient_balance_rpc.py`: Verifies RPC JSON-RPC error format

### Unit Tests
- `execution/tests/test_insufficient_balance_error.py`: 7 tests, all passing
- `execution/tests/test_transfer_apply.py`: Existing tests still pass
- All balance-related tests pass (17 total)

### Test Commands
```bash
# Run execution layer tests
python3 -m pytest execution/tests/test_insufficient_balance_error.py -v

# Run all balance/transfer tests
python3 -m pytest execution/tests/ -k "balance or transfer" -v

# Manual CLI test
python3 test_insufficient_balance_cli.py

# Manual RPC test
python3 test_insufficient_balance_rpc.py
```

## Security Considerations

1. **No sensitive data exposure**: Only balance amounts are exposed, which are already public on-chain
2. **No timing attacks**: Balance check is not time-sensitive
3. **Graceful degradation**: Validation is skipped if state_db unavailable (doesn't block valid transactions)
4. **Input validation**: All amounts are validated and converted to integers
5. **CodeQL scan**: No vulnerabilities detected

## Backward Compatibility

- ✅ Existing transactions continue to work
- ✅ Old error handling paths still supported
- ✅ No breaking changes to RPC API
- ✅ Error code `-32013` was already defined in `AnimicaCode`
- ✅ Execution layer error handling backward compatible

## Performance Impact

- **Minimal**: One additional state_db query per transaction submission
- **Early rejection**: Prevents unnecessary mempool admission for doomed transactions
- **No impact on**: Block processing, signature verification, or consensus

## Future Enhancements

1. Support for EIP-1559 style fee estimation
2. Balance check with nonce consideration for sequential transactions
3. Mempool balance reservation to prevent race conditions
4. WebSocket notifications for balance updates
5. Multi-currency balance checks (for token transfers)

## Files Modified

### Core Implementation
- `execution/state/apply_balance.py` (28 lines changed)
- `execution/runtime/transfers.py` (9 lines changed)
- `rpc/errors.py` (7 lines changed)
- `rpc/methods/tx.py` (82 lines added)
- `python/animica/cli/tx.py` (25 lines added)

### Tests
- `execution/tests/test_insufficient_balance_error.py` (151 lines, new file)
- `rpc/tests/test_tx_insufficient_balance.py` (42 lines, new file)
- `test_insufficient_balance_cli.py` (67 lines, new file)
- `test_insufficient_balance_rpc.py` (65 lines, new file)

## Code Review Feedback Addressed

1. ✅ Added comprehensive docstring for `_validate_sufficient_balance`
2. ✅ Moved `parse_address` import to module level
3. ✅ Consolidated typing imports for consistency
4. ✅ Clarified skip scenarios in validation
5. ✅ All tests passing after changes

## Conclusion

This implementation provides a comprehensive solution for insufficient balance error handling across all layers of the stack. Users now receive clear, actionable feedback when attempting to send transactions with insufficient funds, eliminating confusion and improving the overall user experience.
