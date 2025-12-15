# Before/After Comparison: Insufficient Balance Error Handling

## Problem

When users attempted to send a transaction with insufficient balance, the system would:
- Either fail silently
- Return a generic "transaction failed" error
- Not provide any information about how much was needed or available
- Still add the transaction to the mempool (wasting resources)

## Solution

Enhanced error handling at all layers to provide clear, actionable feedback with detailed balance information.

---

## 🔴 BEFORE

### CLI Output
```bash
$ animica tx send --from "$SENDER" --to "$RECIPIENT" --value 1000000

Error: Transaction failed
# OR
Error: Invalid transaction
# OR
(silent failure - transaction just doesn't get included)
```

**Problems:**
- ❌ No indication of why it failed
- ❌ User doesn't know how much balance they have
- ❌ User doesn't know how much more is needed
- ❌ Generic error message provides no actionable information

### RPC Response
```json
{
  "jsonrpc": "2.0",
  "id": 123,
  "error": {
    "code": -32010,
    "message": "Invalid transaction"
  }
}
```

**Problems:**
- ❌ Generic error code
- ❌ No structured data for clients
- ❌ Cannot distinguish between different failure types
- ❌ Client cannot programmatically determine the issue

### Execution Layer
```python
# Before: Just returned REVERT status
if sender_balance < amount + total_fee:
    return ApplyResult(
        status=TxStatus.REVERT,
        gas_used=intrinsic,
        logs=[],
        state_root=_maybe_state_root(state),
        receipt=None,
    )
```

**Problems:**
- ❌ No error details propagated
- ❌ Insufficient information for debugging
- ❌ Cannot distinguish from other REVERT reasons

---

## 🟢 AFTER

### CLI Output
```bash
$ animica tx send --from "$SENDER" --to "$RECIPIENT" --value 1000000

Error: Insufficient Balance
  Requested: 1000000.0 ANM (1000000000000000 base units)
  Available: 500.0 ANM (500000000000 base units)
  Shortfall: 999500.0 ANM (999500000000000 base units)

Tip: You need to obtain more ANM before sending this transaction.
```

**Benefits:**
- ✅ Clear error type (Insufficient Balance)
- ✅ Shows exactly how much was requested
- ✅ Shows current available balance
- ✅ Shows how much more is needed (shortfall)
- ✅ Displays in both human-readable (ANM) and raw (base units) formats
- ✅ Provides actionable tip for next steps
- ✅ Color-coded for better visibility (red for error, yellow for tip)

### RPC Response
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

**Benefits:**
- ✅ Specific error code (-32013 for insufficient funds)
- ✅ Structured data field with all relevant amounts
- ✅ Clients can parse and display custom error messages
- ✅ Programmatic error handling enabled
- ✅ Amounts stringified to avoid JSON number precision issues
- ✅ Follows JSON-RPC 2.0 specification

### Execution Layer
```python
# After: Raises detailed error with amounts
required = amount + total_fee
if sender_balance < required:
    raise InsufficientBalance(
        f"Insufficient balance for transfer",
        required=required,
        available=sender_balance,
        shortfall=required - sender_balance,
    )
```

**Benefits:**
- ✅ Detailed error with structured data
- ✅ Error propagates through all layers
- ✅ Full context for debugging
- ✅ Distinguishable from other error types

---

## Flow Comparison

### Before
```
User sends tx → RPC receives → (Silent processing) → Generic error or silent failure
                                                    ↓
                                            Maybe added to mempool
                                                    ↓
                                            Eventually rejected during execution
```

### After
```
User sends tx → RPC receives → Decode ✓ → Validate chain ID ✓ → Verify signature ✓
                                                                        ↓
                                                            [NEW] Check balance
                                                                    ├─ Sufficient? Continue →
                                                                    └─ Insufficient? Reject immediately
                                                                            ↓
                                                                    Return detailed error
                                                                            ↓
                                                                    CLI formats nicely
                                                                            ↓
                                                                    User sees clear message
```

---

## Impact Summary

### For Users
- ✅ **Clarity**: Immediately understand why transaction failed
- ✅ **Actionability**: Know exactly how much more ANM is needed
- ✅ **Confidence**: Clear feedback builds trust in the system
- ✅ **Efficiency**: No wasted time wondering what went wrong

### For Developers
- ✅ **Debuggability**: Detailed error data helps diagnose issues
- ✅ **Integration**: Structured errors enable programmatic handling
- ✅ **Standards**: JSON-RPC 2.0 compliant error responses
- ✅ **Consistency**: Same error format across all interfaces

### For the System
- ✅ **Efficiency**: Early rejection prevents mempool pollution
- ✅ **Performance**: Avoid processing doomed transactions
- ✅ **Resources**: Save gas estimation and execution overhead
- ✅ **Reliability**: Clear error boundaries improve system stability

---

## Example Scenarios

### Scenario 1: First-time user
**Before:** "Why isn't my transaction working?"
**After:** "I need 999,500 more ANM. Let me visit the faucet."

### Scenario 2: DApp developer
**Before:** Generic catch-all error handling
**After:** 
```javascript
if (error.code === -32013) {
  const shortfall = error.data.shortfall;
  showBalanceWarning(`You need ${shortfall} more ANM`);
}
```

### Scenario 3: Support ticket
**Before:** "My transaction failed" → "Can you send me your transaction hash?" → Long debugging process
**After:** User provides screenshot with exact amounts → Immediate understanding of the issue

---

## Test Coverage

### Before
- Basic transfer tests
- Some error handling tests
- No specific insufficient balance tests

### After
- ✅ 7 new unit tests for InsufficientBalance error
- ✅ Tests for RPC error formatting
- ✅ Tests for CLI error display
- ✅ Manual integration tests
- ✅ All existing tests still pass (17/17)
- ✅ CodeQL security scan: No vulnerabilities

---

## Backward Compatibility

✅ **Fully backward compatible**
- Existing transactions continue to work
- Old error codes still supported
- No breaking changes to RPC API
- Graceful degradation if validation unavailable

---

## Conclusion

This enhancement transforms a frustrating user experience into a clear, informative interaction. Users now receive actionable feedback when transactions fail due to insufficient balance, developers can build better error handling, and the system operates more efficiently by rejecting doomed transactions early.

**Key Improvement:** From "Why did this fail?" to "I need X more ANM."
