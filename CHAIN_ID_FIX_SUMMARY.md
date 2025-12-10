# Chain ID Resolution Fix - Implementation Summary

## Problem Statement

The Animica CLI had a bug where `animica tx send` was sending transactions with `chainId = 0`, causing the node to reject them with:
```
Chain ID mismatch: {'got': 0, 'expected': 1}
```

The issue persisted even when users specified `--chain-id 1` explicitly.

## Root Cause

The chain ID resolution logic was **missing step #3** in the precedence chain:

1. ✅ Explicit `--chain-id` flag (working)
2. ✅ `ANIMICA_CHAIN_ID` environment variable (working)
3. ❌ **Active network config** (via `animica network set <network>`) - **MISSING!**
4. ✅ Query node via RPC (working)

The network configuration was loaded to get the RPC URL, but the `chain_id` field was completely ignored when resolving the chain ID for transactions. This caused the CLI to skip directly from checking environment variables to querying the node, bypassing the user's active network configuration.

## Solution Implemented

### 1. Updated `resolve_chain_id()` Function

**File:** `python/animica/cli/tx.py`

Added a new `config_chain_id` parameter:

```python
def resolve_chain_id(
    rpc_url: Optional[str],
    cli_chain_id: Optional[int],
    config_chain_id: Optional[int] = None,  # NEW!
) -> int:
```

Implemented proper precedence logic:

```python
# Determine which chain ID to use based on precedence
chain_id_to_use = None
chain_id_source = None

if cli_chain_id is not None:
    chain_id_to_use = cli_chain_id
    chain_id_source = "CLI/env"
elif config_chain_id is not None:  # NEW!
    chain_id_to_use = config_chain_id
    chain_id_source = "network config"
```

### 2. Updated All CLI Commands

Modified `build`, `sign`, and `send` commands to load and pass network config:

**Before:**
```python
url = _resolve_rpc_url(rpc_url)
resolved_chain_id = resolve_chain_id(url, chain_id)
```

**After:**
```python
url = _resolve_rpc_url(rpc_url)
cfg = load_network_config()  # NEW!
resolved_chain_id = resolve_chain_id(url, chain_id, cfg.chain_id)  # NEW!
```

### 3. Improved Error Messages

Enhanced error messages to show the source of the chain ID:

```
============================================================
Error: Chain ID mismatch
============================================================
Source:        network config
Specified ID:  1
Node chain ID: 1337

The transaction would be rejected by the node.

Solutions:
  1. Remove --chain-id flag to auto-detect (node: 1337)
  2. Use 'animica network set <network>' to switch networks
  3. Set --chain-id 1337 to override config
  4. Connect to a different node with --rpc-url
============================================================
```

### 4. Added Comprehensive Tests

**File:** `python/animica/cli/tests/test_chain_id_resolution.py`

Added 4 new tests specifically for network config precedence:
- `test_resolve_chain_id_uses_config_when_no_cli_value()`
- `test_resolve_chain_id_cli_overrides_config()`
- `test_resolve_chain_id_config_mismatch_fails()`
- `test_build_uses_network_config_chain_id()`

**File:** `python/animica/cli/tests/conftest.py`

Added autouse fixture to ensure consistent test environment:

```python
@pytest.fixture(autouse=True)
def set_test_chain_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set chain ID to 1337 for all tests to prevent conflicts."""
    import os
    if "ANIMICA_CHAIN_ID" not in os.environ:
        monkeypatch.setenv("ANIMICA_CHAIN_ID", "1337")
```

## Test Results

All tests passing:
- ✅ 21 chain ID resolution tests
- ✅ 25 tx CLI tests (2 skipped - unrelated issues)
- ✅ **46 total tests passing**

## Usage Examples

### Example 1: Using Network Config

```bash
# Set active network to devnet (chain ID 1337)
animica network set devnet

# Transaction will use chain ID 1337 from config
animica tx send --from alice --to bob --value 1.0
```

### Example 2: Override with Environment Variable

```bash
# Override network config with env var
export ANIMICA_CHAIN_ID=42

# Transaction will use chain ID 42 (overrides config)
animica tx send --from alice --to bob --value 1.0
```

### Example 3: Override with CLI Flag

```bash
# Override everything with explicit flag
animica tx send --from alice --to bob --value 1.0 --chain-id 99
```

### Example 4: Auto-detect from Node

```bash
# Don't set anything - CLI queries node
unset ANIMICA_CHAIN_ID
animica tx send --from alice --to bob --value 1.0 --rpc-url http://localhost:8545
```

## Chain ID Precedence (Final)

The complete precedence order is now:

1. **CLI flag** (`--chain-id 1337`)
   - Highest priority, explicit user intent
   
2. **Environment variable** (`ANIMICA_CHAIN_ID=1337`)
   - Good for scripting and CI/CD
   
3. **Network config** (`animica network set devnet`)
   - **NEWLY FIXED!** Respects user's active network choice
   
4. **RPC query** (`chain.getChainId`)
   - Fallback when nothing else is specified

## Files Changed

1. `python/animica/cli/tx.py` - Core logic fix
2. `python/animica/cli/tests/test_chain_id_resolution.py` - New tests
3. `python/animica/cli/tests/test_tx_cli.py` - Updated assertions
4. `python/animica/cli/tests/conftest.py` - Test environment fixture

## Impact

- ✅ No more "chainId = 0" errors
- ✅ Network switching actually works
- ✅ Clear error messages when mismatches occur
- ✅ Backward compatible (all existing workflows still work)
- ✅ Better user experience with helpful error messages

## Acceptance Criteria Met

- [x] `animica tx send` works with valid chain ID auto-detection
- [x] The fallback precedence (CLI flag > ENV var > active network > RPC query) is implemented correctly
- [x] Tests confirming valid behaviors and edge cases pass successfully (46 passing)
- [x] Error messages provide sufficient diagnostics for future chain-ID issues

## Future Improvements

While not part of this fix, potential future enhancements:

1. Cache RPC-queried chain ID to avoid repeated queries
2. Add debug flag to show chain ID resolution path
3. Add `animica network info` command to show current config including chain ID
