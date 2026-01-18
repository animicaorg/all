# Genesis ChainID Mismatch Fix - Complete

## Problem Summary
Mainnet Docker node was failing to start with the error:
```
core.errors.GenesisError: genesis chainId=0 does not match params.chain_id=1
```

The RPC server would crash during startup when trying to bootstrap genesis, preventing the node from initializing.

## Root Cause Analysis

### The Bug
In `core/genesis/loader.py`, the `_load_chain_params()` function was incorrectly resolving relative paths from the genesis file's `paramsRef.path` field:

1. **Genesis file location**: `core/genesis/mainnet.json`
2. **Genesis specifies**: `"paramsRef": {"path": "spec/params.yaml"}`
3. **Expected resolution**: `spec/params.yaml` (relative to repo root)
4. **Actual resolution**: `core/spec/params.yaml` (relative to `base_dir` which was `core/`)

### The Code Issue
```python
# OLD CODE (BUGGY):
elif base_dir is not None and not params_path.is_absolute():
    params_path = (base_dir / params_path).resolve()
```

When `base_dir` was set to the parent directory of the genesis file (e.g., `core/` for mainnet), the relative path `spec/params.yaml` was resolved to `core/spec/params.yaml`, which doesn't exist.

### Why It Manifested as chain_id=1
When the params file couldn't be found, the code would fall back to `default_params_path()`, but due to the incorrect resolution, it would either:
- Fail to find the file and error out
- Pick up a default or cached params object with incorrect chain_id

## The Fix

### Code Changes
Changed `_load_chain_params()` in `core/genesis/loader.py` to resolve relative paths relative to the **repo root** instead of `base_dir`:

```python
# NEW CODE (FIXED):
elif not params_path.is_absolute():
    # Resolve relative paths relative to repo root, not base_dir
    # The genesis JSON typically uses paths like "spec/params.yaml" which are
    # relative to the repo root, not relative to the genesis file location.
    # core/genesis/loader.py -> parents[0]=core/genesis, parents[1]=core, parents[2]=repo_root
    repo_root = Path(__file__).resolve().parents[2]
    params_path = (repo_root / params_path).resolve()
```

### Why This Works
- `Path(__file__)` = `core/genesis/loader.py`
- `.parents[0]` = `core/genesis/`
- `.parents[1]` = `core/`
- `.parents[2]` = repo root (e.g., `/home/runner/work/all/all/`)
- `repo_root / "spec/params.yaml"` = `/home/runner/work/all/all/spec/params.yaml` ✓

## Testing

### Unit Tests Added
Created `core/genesis/tests/test_params_path_resolution.py` with tests for:
- ✓ Mainnet genesis loads with `chain_id=0`
- ✓ Testnet genesis loads with `chain_id=2`
- ✓ Devnet genesis loads with `chain_id=1337`
- ✓ Relative paths resolve to repo root, not base_dir

All tests pass:
```bash
$ PYTHONPATH=/home/runner/work/all/all python3 core/genesis/tests/test_params_path_resolution.py
✓ test_mainnet_genesis_loads_correct_params passed
✓ test_testnet_genesis_loads_correct_params passed
✓ test_devnet_genesis_loads_correct_params passed
✓ test_params_ref_path_is_relative_to_repo_root passed

✓ All tests passed!
```

### Manual Verification
```python
from core.genesis.loader import load_genesis
params, header = load_genesis('core/genesis/mainnet.json')
assert params.chain_id == 0  # ✓
assert header.chainId == 0   # ✓
```

## Impact

### What Was Fixed
- ✅ Mainnet node can now start without genesis mismatch error
- ✅ Genesis params are loaded with correct chain_id for all networks
- ✅ Relative paths in genesis files work correctly
- ✅ No more "chainId=0 does not match params.chain_id=1" errors

### What Was Not Changed
- Genesis file content (still has `chainId=0` for mainnet, which is correct)
- spec/params.yaml structure (still has network configs under `networks:` key)
- RPC startup logic (only the params loading was fixed)

## Files Modified
1. `core/genesis/loader.py` - Fixed `_load_chain_params()` path resolution
2. `core/genesis/tests/test_params_path_resolution.py` - Added regression tests

## Deployment Notes

### For Docker
The fix is in the Python code, so:
1. Rebuild Docker image with updated code
2. Clear any existing data volumes if they have corrupt genesis state
3. Start node - it should now initialize correctly

### For Development
1. Pull latest changes
2. Clear Python cache: `find . -type d -name "__pycache__" -delete`
3. Run tests to verify: `PYTHONPATH=. python3 core/genesis/tests/test_params_path_resolution.py`

## Prevention

### Design Principle
Genesis file paths should **always** be relative to the repository root, not relative to the genesis file's location. This makes the paths more portable and predictable.

### Code Review Checklist
When reviewing path resolution code:
- [ ] Are relative paths resolved from repo root?
- [ ] Is `Path(__file__).resolve().parents[N]` used correctly?
- [ ] Are there tests that verify path resolution works?
- [ ] Does it work when the genesis file is in a subdirectory?

## Related Issues
This fix resolves the root cause of the "RPC not reachable after 60s" error that was occurring during mainnet node startup. The RPC server was crashing during initialization, so it never bound to the port.
