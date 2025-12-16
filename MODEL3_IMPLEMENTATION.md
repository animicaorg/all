# Model 3 (Hybrid) Checkpoint Implementation

## Overview

This implementation adds **optional checkpoint mechanism** to Animica's P2P-first sync architecture. Model 3 maintains the existing P2P-first behavior as the default while providing an opt-in safety rail mechanism for validating chain integrity against known-good checkpoints.

## Key Principles

1. **P2P-first remains the default**: No code path requires `rpc.animica.org` to be reachable by default
2. **Checkpoints are optional**: Enabled via explicit configuration
3. **Graceful degradation**: Non-strict mode continues without checkpoints if unavailable
4. **Safety rail, not oracle**: Used for static validation, not live head queries
5. **Zero impact when disabled**: No performance or behavioral changes in default mode

## Architecture

### Components

```
p2p/checkpoints/
├── __init__.py           # Public API exports
├── config.py             # Configuration loader (env vars)
├── loader.py             # Checkpoint fetcher (RPC/file)
├── verifier.py           # Chain verification logic
├── integration.py        # Helper functions for integration
├── README.md             # Module documentation
├── fixtures/
│   └── example_checkpoints.json
├── examples/
│   └── basic_usage.py
└── tests/
    ├── test_config.py
    ├── test_loader.py
    ├── test_verifier.py
    ├── test_integration.py
    └── test_no_http_when_disabled.py
```

### Integration Points

Checkpoints are verified during:

1. **Initial sync**: When DB is empty or far behind
2. **Fork choice**: When adopting a new best chain during reorg
3. **Header sync**: Via optional `checkpoint_verifier` parameter to `HeaderSync`

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANIMICA_CHECKPOINTS_MODE` | `off` | Mode: `off`, `rpc`, or `file` |
| `ANIMICA_CHECKPOINTS_RPC_URL` | `https://rpc.animica.org/rpc` | RPC endpoint for checkpoints |
| `ANIMICA_CHECKPOINTS_FILE` | (none) | Path to local checkpoint file |
| `ANIMICA_CHECKPOINTS_MAX_AGE` | (none) | Maximum age in seconds (optional) |
| `ANIMICA_CHECKPOINTS_STRICT` | `false` | Fail fast if checkpoints unavailable |

### Modes

#### Off (Default)
```bash
export ANIMICA_CHECKPOINTS_MODE=off
```
- No checkpoints used
- Pure P2P consensus
- No external dependencies
- **This is the default behavior**

#### RPC Mode
```bash
export ANIMICA_CHECKPOINTS_MODE=rpc
export ANIMICA_CHECKPOINTS_RPC_URL=https://rpc.animica.org/rpc
```
- Fetches checkpoints from RPC endpoint
- Tries `chain.getCheckpoints` JSON-RPC method
- Falls back to HTTP endpoints (`/checkpoints.json`, `/checkpoints`)
- Non-strict by default: continues without checkpoints if unavailable

#### File Mode
```bash
export ANIMICA_CHECKPOINTS_MODE=file
export ANIMICA_CHECKPOINTS_FILE=~/.animica/checkpoints.json
```
- Loads checkpoints from local JSON file
- No network calls
- Useful for air-gapped or private networks

## Checkpoint Format

### Standard Format
```json
{
  "checkpoints": [
    {"height": 1000, "hash": "0x1234abcd..."},
    {"height": 2000, "hash": "0x5678ef01..."},
    {"height": 3000, "hash": "0x9abc2345..."}
  ],
  "timestamp": 1234567890,
  "network": "mainnet",
  "description": "Optional description"
}
```

### Alternative Format
```json
[
  {"height": 1000, "hash": "0x1234abcd..."},
  {"height": 2000, "hash": "0x5678ef01..."}
]
```

## Usage

### Programmatic

```python
from p2p.checkpoints import initialize_checkpoints

# Initialize from environment
verifier = await initialize_checkpoints()

# Use with header sync
from p2p.sync.headers import HeaderSync

sync = HeaderSync(
    chain=chain_adapter,
    fetcher=header_fetcher,
    consensus=consensus_view,
    checkpoint_verifier=verifier,  # Optional parameter
)
```

### Manual Verification

```python
from p2p.checkpoints import verify_chain_checkpoints

is_valid, errors = await verify_chain_checkpoints(
    verifier=verifier,
    chain_view=chain_adapter,
    max_height=10000,
)

if not is_valid:
    for error in errors:
        print(f"Checkpoint mismatch: {error}")
```

## Testing

### Test Coverage

- **43 unit tests** covering all functionality
- Configuration loading and validation
- Checkpoint parsing (file and RPC)
- Verification and mismatch detection
- Cache behavior and expiry
- HTTP isolation (no calls when disabled)
- Strict vs non-strict mode
- Integration scenarios

### Running Tests

```bash
# Run all checkpoint tests
pytest p2p/checkpoints/tests/ -v

# Run specific test file
pytest p2p/checkpoints/tests/test_verifier.py -v

# Run with coverage
pytest p2p/checkpoints/tests/ --cov=p2p.checkpoints
```

### Test Results
```
43 passed in 1.29s
```

## Implementation Details

### Modified Files

1. **p2p/sync/headers.py**
   - Added optional `checkpoint_verifier` parameter to `HeaderSync.__init__`
   - Added `enable_checkpoints` flag to `HeaderSyncConfig`
   - Integrated checkpoint verification during fork choice
   - Added `_verify_checkpoint_if_enabled` helper method

2. **docs/p2p_sync.md**
   - Added Model 3 overview section
   - Documented checkpoint modes and configuration
   - Added checkpoint format specification
   - Updated comparison table

### New Files

- `p2p/checkpoints/__init__.py` - Public API
- `p2p/checkpoints/config.py` - Configuration
- `p2p/checkpoints/loader.py` - Checkpoint loading
- `p2p/checkpoints/verifier.py` - Verification logic
- `p2p/checkpoints/integration.py` - Integration helpers
- `p2p/checkpoints/README.md` - Module docs
- `p2p/checkpoints/fixtures/example_checkpoints.json` - Example
- `p2p/checkpoints/examples/basic_usage.py` - Usage example
- 5 test files with 43 tests

## Security Considerations

### Trust Model

Checkpoints require trusting the checkpoint source:
- **RPC mode**: Trust the RPC endpoint operator
- **File mode**: Trust the checkpoint file provider
- **Default off**: No trust required

### Fail-Safe Design

- **Non-strict default**: Continues without checkpoints if unavailable
- **No consensus rules**: Checkpoints supplement, not replace, P2P validation
- **Static checks**: Not live oracles, prevents checkpoint-based attacks
- **Graceful degradation**: System functions fully without checkpoints

### Attack Vectors

| Attack | Mitigation |
|--------|------------|
| Checkpoint poisoning | User must verify checkpoint source; file mode recommended for critical deployments |
| RPC unavailability | Non-strict mode continues with P2P only |
| Checkpoint staleness | Optional `max_age` parameter enforces freshness |
| Deep reorg to bad chain | Checkpoints prevent syncing to minority forks |

## Performance Impact

### Default Mode (Off)
- **Zero overhead**: No code execution when disabled
- **No HTTP calls**: Verified by tests
- **No storage**: No checkpoint data stored

### Enabled Modes
- **Initial cost**: One-time fetch/load of checkpoints
- **Cache**: Checkpoints cached in memory (respects `max_age`)
- **Verification**: O(1) lookup per checkpoint height
- **Minimal impact**: Only checks blocks at checkpoint heights

## Migration Path

### For Existing Nodes

No changes required:
- Default mode remains `off`
- Existing behavior unchanged
- No migration needed

### Enabling Checkpoints

```bash
# Add to environment or config file
export ANIMICA_CHECKPOINTS_MODE=file
export ANIMICA_CHECKPOINTS_FILE=/path/to/checkpoints.json

# Restart node
systemctl restart animica-node
```

### For New Deployments

Recommended for production:
```bash
export ANIMICA_CHECKPOINTS_MODE=rpc
export ANIMICA_CHECKPOINTS_RPC_URL=https://rpc.animica.org/rpc
export ANIMICA_CHECKPOINTS_STRICT=true
```

## Future Enhancements

Potential improvements:
1. **Automatic checkpoint updates**: Periodic refresh from trusted sources
2. **Checkpoint signing**: Cryptographic verification of checkpoint authenticity
3. **Multi-source verification**: Consensus across multiple checkpoint providers
4. **Dynamic checkpoints**: Community-driven checkpoint proposals
5. **RPC method**: `chain.getCheckpoints` implementation on public RPC

## Documentation

- **Module README**: `p2p/checkpoints/README.md`
- **P2P Sync Guide**: `docs/p2p_sync.md` (updated)
- **Example**: `p2p/checkpoints/examples/basic_usage.py`
- **Test Coverage**: `p2p/checkpoints/tests/`

## Compliance

### PR Requirements

✅ Configuration with env vars  
✅ JSON checkpoint format defined  
✅ RPC and file modes implemented  
✅ Verification during sync and fork choice  
✅ No HTTP calls when disabled (tested)  
✅ Unit tests for parsing and validation  
✅ Unit test for checkpoint mismatch  
✅ Documentation updated  
✅ Step 0 removal maintained (proxy explicit opt-in)  

### Model 3 Principles

✅ Default behavior remains P2P-first  
✅ No code path requires `rpc.animica.org` by default  
✅ Optional checkpoint mechanism  
✅ Safety rail during sync/fork-choice, not live oracle  
✅ Graceful degradation (non-strict mode)  

## Conclusion

Model 3 (hybrid) implementation successfully adds optional checkpoint safety rails while maintaining P2P-first architecture as the default. The implementation is:

- **Minimal**: Zero impact when disabled
- **Flexible**: Multiple modes (off/rpc/file)
- **Robust**: Comprehensive test coverage
- **Safe**: Fail-safe design with graceful degradation
- **Documented**: Complete documentation and examples

The checkpoint mechanism provides an additional layer of safety for initial sync and fork choice without compromising the decentralized P2P-first consensus model.
