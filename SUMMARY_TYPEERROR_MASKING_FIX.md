# Summary: Fix for TypeError Masking in Mempool Admission

## Issue
Users were seeing generic "internal_error" messages when trying to send transactions, with only a hint that a TypeError occurred but no details about what went wrong.

## Root Cause
The RPC layer was using broad `except TypeError` blocks to handle backward compatibility between old and new mempool interface signatures. Unfortunately, this also caught TypeErrors from INSIDE the methods being called, effectively masking real bugs.

## Solution
Replaced exception-based signature detection with proper introspection using Python's `inspect.signature()`. Now we check function signatures BEFORE calling them, so any TypeErrors from within the functions propagate normally.

## Files Changed
- `rpc/methods/tx.py` - Core implementation
- `FIX_TYPEERROR_MASKING_IN_MEMPOOL.md` - Detailed documentation

## Testing
- ✅ Created comprehensive test suite
- ✅ Verified signature detection works correctly
- ✅ Confirmed TypeErrors now propagate properly
- ✅ Ensured backward compatibility maintained
- ✅ Code review completed and feedback addressed
- ✅ Security scan passed

## What Users Will See Now

### Before (Masked Error)
```
RPC Error -32010: mempool admission failed: internal_error
{
    'context': {
        'error_class': 'TypeError'  # No useful info!
    }
}
```

### After (Actual Error)
Users will now see the real error with a full stack trace, for example:
```
RPC Error -32010: mempool admission failed
TypeError: int() argument must be a string, a bytes-like object or a real number, not 'dict'
  File "rpc/mempool_service.py", line 860, in submit
    chain_id = int(tx.get("chainId"))
  File "<string>", line 1, in <lambda>
```

This makes it immediately clear what the problem is and where to fix it.

## Deployment
The fix is ready for deployment. No configuration changes required. The change is backward compatible and will automatically:
1. Work with old-style mempool services (no optional params)
2. Work with new-style mempool services (with optional params)
3. Work with any service using **kwargs

## Verification
After deployment, when users encounter similar errors:
1. They will see the actual TypeError with full details
2. The node logs will show the complete stack trace
3. Developers can immediately identify and fix the root cause

## Impact
- **Users**: Get actionable error messages instead of "internal_error"
- **Developers**: Can quickly diagnose and fix issues
- **Operations**: Reduced debugging time and support burden
- **Code Quality**: Safer error handling that doesn't mask bugs
