# Bug Investigation: NameError in miner.getBlockTemplate

## Bug Summary

**Issue**: The RPC method `miner.getBlockTemplate` crashes with:
- **Error code**: `-32603` (Internal error)
- **Error data**: `{'reason': 'NameError'}`

**Reproduction**:
```bash
animica miner mine-blocks --address <ADDR> --count 1 --rpc-url http://127.0.0.1:8545/rpc
```

## Root Cause Analysis

### Location
- **File**: `rpc/methods/miner.py`
- **Function**: `miner_get_block_template` (line 3329)
- **Bug lines**: 3404 and 3410

### The Bug

The variable `mempool_service` is referenced but **never defined** in the `miner_get_block_template` function:

**Line 3404**:
```python
log.debug(
    "block template mempool collection",
    extra={
        "entries": len(pending_entries),
        "total": pending_total,
        "source": "service" if mempool_service is not None else "adapter",  # ❌ NameError
    },
)
```

**Line 3410**:
```python
if include_mempool_flag and not pending_entries and mempool_service is None:  # ❌ NameError
```

### Why It Occurred

The `mempool_service` variable is obtained from the context in the `_collect_mempool_entries` helper function (line 1522):

```python
def _collect_mempool_entries(
    *, ctx: Any, adapter: Any, limit: int
) -> tuple[list[PendingTxEntry], dict[str, bytes], int]:
    mempool_service = getattr(ctx, "mempool", None)  # ✅ Correctly defined here
    if mempool_service is not None:
        snapshot = mempool_service.snapshot(limit=limit)
        # ...
```

However, this variable is **local to `_collect_mempool_entries`** and is not returned or accessible in `miner_get_block_template`. The main function directly references `mempool_service` without defining it first.

### Why This Wasn't Caught Earlier

Looking at existing tests in `rpc/tests/test_miner_methods.py`, all tests use `monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")` which likely bypasses certain code paths or the tests execute successfully without triggering the log statement at line 3404.

## Affected Components

**Direct impact**:
- `miner.getBlockTemplate` RPC method (crashes on every call)
- Mining CLI commands that depend on this method

**Related code**:
- `_collect_mempool_entries` function (correctly obtains `mempool_service`)
- Logging statements that reference `mempool_service`
- Fallback logic for mempool entries

## Error Handling Analysis

The RPC dispatcher in `rpc/jsonrpc.py` (lines 497-504) **does log full tracebacks** for internal errors:

```python
if getattr(rpc_err, "code", None) == -32603:
    log.exception(
        "RPC internal error",
        extra={
            "jsonrpc_method": method,
            "params": _redact_params(params),
        },
    )
```

This means the full stack trace **should be available** in the node logs, but the CLI only sees the generic error response.

## Proposed Solution

### Fix

Add the missing variable definition at the beginning of `miner_get_block_template`, after obtaining the `ctx`:

```python
ctx = _ctx()
adapter = _adapter()
mempool_service = getattr(ctx, "mempool", None)  # ✅ Add this line
```

This mirrors the pattern used in `_collect_mempool_entries` and other functions in the same file.

### Alternative Approaches Considered

1. **Return mempool_service from `_collect_mempool_entries`**: Would require changing function signature and all callers
2. **Remove references to mempool_service**: Would lose useful debugging information
3. **Use try/except around the references**: Would mask the underlying issue

**Chosen approach**: Define `mempool_service` directly in the function (simplest and most consistent with the codebase).

### Testing Strategy

1. **Unit test**: Call `miner.getBlockTemplate` with valid address and verify:
   - No NameError is raised
   - Response contains expected fields (`templateId`, `header`, `txs`, etc.)
   
2. **Integration test**: Run the mining CLI command end-to-end:
   - When node is synced → should return valid block template
   - When node is not synced/ready → should return structured error (not NameError)

3. **Regression test**: Add test case that specifically exercises the code path where `mempool_service` is referenced

### Edge Cases to Consider

- Node in different sync states (syncing, synced, not started)
- Mempool service available vs. not available
- Empty mempool vs. populated mempool
- Fallback pending pool scenarios

## Dependencies

- No new dependencies required
- Fix is self-contained within `rpc/methods/miner.py`

---

## Implementation Notes

### Changes Made

**1. Fixed the NameError in `rpc/methods/miner.py`** (line 3382)

Added the missing variable definition:
```python
ctx = _ctx()
adapter = _adapter()
mempool_service = getattr(ctx, "mempool", None)  # ✅ Added this line
```

This fix resolves the NameError at:
- Line 3404: `"source": "service" if mempool_service is not None else "adapter"`
- Line 3410: `if include_mempool_flag and not pending_entries and mempool_service is None:`

**2. Added regression tests in `rpc/tests/test_miner_methods.py`**

- `test_get_block_template_with_mempool_enabled()`: Tests that `miner.getBlockTemplate` with `include_mempool=True` (default) does not raise NameError and returns valid template fields
- `test_get_block_template_with_mempool_disabled()`: Tests that `miner.getBlockTemplate` with `include_mempool=False` works correctly

### Root Cause Summary

The variable `mempool_service` was used on lines 3404 and 3410 but was never defined in the `miner_get_block_template` function. It was defined inside the helper function `_collect_mempool_entries` but as a local variable that was not returned or made accessible to the caller.

### Fix Validation

The fix follows the same pattern used in the `_collect_mempool_entries` helper function and is consistent with other functions in the codebase that need access to the mempool service.

**Expected behavior after fix**:
- `animica miner mine-blocks --count 1 ...` will succeed when node is synced
- No NameError will occur
- Proper logging of mempool source (service vs adapter)
- Fallback logic for pending transactions will work correctly

### Testing Notes

Tests were added to `rpc/tests/test_miner_methods.py`:
- Tests verify no NameError is raised
- Tests verify correct response structure with expected fields
- Tests cover both `include_mempool=True` and `include_mempool=False` cases

The tests require a properly configured Python environment with pytest installed. To run tests:
```bash
pytest -c tests/pytest.ini rpc/tests/test_miner_methods.py::test_get_block_template_with_mempool_enabled -v
```
