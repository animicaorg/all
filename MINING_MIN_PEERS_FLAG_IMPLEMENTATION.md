# Mining Without Peers: --min-peers Flag Implementation

## Problem Statement

From user issue on mainnet:
```
(.venv) root@vmi2562287:~/animica# animica miner mine-blocks --address anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz --count 1

Warning: Block template unavailable (insufficient_peers (connected: 0, required: 1). 
Try: 'animica peer bootstrap' to connect to peers. 
Check: 'animica p2p doctor' for diagnostics, or set ANIMICA_MINING_MIN_PEERS=0 for local development.)
Warning: No blocks were mined (may have failed)
```

**Issue**: User on mainnet node couldn't mine because:
1. Node had no peer connections (connected: 0, required: 1)
2. Error message suggested setting `ANIMICA_MINING_MIN_PEERS=0`
3. But this requires setting an environment variable before running the command
4. Not user-friendly for quick local development/testing

## Solution Implemented

Added `--min-peers` CLI flag to the `mine-blocks` command to allow temporary override of the peer requirement without needing to set environment variables.

### What Changed

#### 1. CLI Flag Added (`python/animica/cli/mining.py`)
```python
min_peers: Optional[int] = typer.Option(
    None,
    "--min-peers",
    help="Minimum connected peers required for mining (default: use ANIMICA_MINING_MIN_PEERS or 1). Set to 0 for local development without peers.",
)
```

- Accepts integer value >= 0
- Validates input and converts from string (for stub Typer)
- Passes value to RPC call

#### 2. RPC Method Updated (`rpc/methods/miner.py`)
```python
# In miner_get_block_template():
min_peers_override: int | None = None

# Extract from payload
if "min_peers" in payload or "minPeers" in payload:
    min_peers_val = payload.get("min_peers", payload.get("minPeers"))
    if min_peers_val is not None:
        min_peers_override = int(min_peers_val)
        if min_peers_override < 0:
            raise rpc_errors.InvalidParams("min_peers must be >= 0")

# Pass to _mining_gate
allowed, reason = _mining_gate(
    allow_offline_mining=allow_offline_mining,
    allow_unsynced=allow_unsynced_mining,
    min_peers_override=min_peers_override,
)
```

#### 3. Mining Gate Updated (`rpc/methods/miner.py`)
```python
def _mining_gate(
    *, 
    allow_offline_mining: bool = False, 
    allow_unsynced: bool = False, 
    min_peers_override: int | None = None
) -> tuple[bool, str | None]:
    # Use override if provided, else use environment variable
    env_min_peers = int(os.getenv("ANIMICA_MINING_MIN_PEERS", "1"))
    if min_peers_override is not None:
        min_peers = min_peers_override
        log.info(
            "Using min_peers override from RPC parameter",
            extra={
                "min_peers_override": min_peers,
                "env_min_peers": env_min_peers,
                "source": "rpc_parameter",
            },
        )
    else:
        min_peers = env_min_peers
```

#### 4. Tests Added (`rpc/tests/test_miner_methods.py`)
- `test_get_block_template_accepts_min_peers_override` - Verifies override works
- `test_get_block_template_min_peers_validation` - Validates parameter bounds

#### 5. Documentation Updated (`python/animica/cli/README.md`)
```bash
# Mine without peer connections (for local development/testing)
animica miner mine-blocks --count 1 --min-peers 0

# Alternative: set environment variable
export ANIMICA_MINING_MIN_PEERS=0
animica miner mine-blocks --count 5
```

## Usage Examples

### Before (Required Environment Variable)
```bash
# Had to set environment variable first
export ANIMICA_MINING_MIN_PEERS=0
animica miner mine-blocks --address anim1xxx --count 1
```

### After (Simple Flag)
```bash
# Can use flag directly
animica miner mine-blocks --address anim1xxx --count 1 --min-peers 0
```

### Additional Examples
```bash
# Mine with custom peer requirement
animica miner mine-blocks --address anim1xxx --count 5 --min-peers 3

# Override environment variable for single operation
export ANIMICA_MINING_MIN_PEERS=5
animica miner mine-blocks --address anim1xxx --count 1 --min-peers 0  # Uses 0, not 5
```

## Benefits

1. **User-Friendly**: No need to set environment variables for one-off operations
2. **Flexible**: Can temporarily override environment variable per command
3. **Clear**: Flag makes the peer requirement explicit in the command
4. **Backward Compatible**: Environment variable still works as before
5. **Safe**: Default behavior unchanged (still requires 1 peer)

## Testing

### Unit Tests
```bash
# Run the new tests
pytest rpc/tests/test_miner_methods.py::test_get_block_template_accepts_min_peers_override
pytest rpc/tests/test_miner_methods.py::test_get_block_template_min_peers_validation
```

### Manual Test
```bash
# Start a local node (no peers)
animica node up

# Try to mine (should fail)
animica miner mine-blocks --address anim1xxx --count 1
# Expected: Error about insufficient_peers

# Mine with --min-peers 0 (should succeed)
animica miner mine-blocks --address anim1xxx --count 1 --min-peers 0
# Expected: Block mined successfully
```

## Security Considerations

✅ **No security vulnerabilities introduced**
- Parameter is validated (must be >= 0)
- Only affects local mining operations
- Does not expose sensitive information
- Follows existing patterns for similar parameters
- Cannot be used to bypass legitimate security checks (only peer count)

## Backward Compatibility

✅ **Fully backward compatible**
- Environment variable `ANIMICA_MINING_MIN_PEERS` continues to work
- Default behavior unchanged (requires 1 peer)
- No breaking changes to RPC interface
- CLI flag is optional

## Files Changed

1. `python/animica/cli/mining.py` - Added `--min-peers` flag
2. `rpc/methods/miner.py` - Updated RPC method and mining gate
3. `rpc/tests/test_miner_methods.py` - Added tests
4. `python/animica/cli/README.md` - Updated documentation

## Git Commits

1. `e311e269` - Add --min-peers flag to mine-blocks command
2. `842344bb` - Add tests for min_peers parameter
3. `a0359a83` - Update README with --min-peers flag documentation
4. `5553fddb` - Improve min_peers override logging

## Related Documentation

- Mining README: `mining/README.md`
- CLI README: `python/animica/cli/README.md`
- RPC Methods: `rpc/methods/miner.py`

## Notes

- The CLI uses a stub Typer implementation that doesn't do automatic type conversion, so string-to-int conversion is needed
- The dual key pattern (`min_peers` and `minPeers`) follows existing conventions in the RPC method
- Logging includes both override and environment values for debugging
