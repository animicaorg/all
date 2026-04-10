# Checkpoints Module (Model 3)

Optional checkpoint mechanism for P2P-first sync safety rails.

## Overview

Model 3 (Hybrid) maintains P2P-first sync as the default while adding an optional checkpoint mechanism:

- **Default behavior remains P2P-first** for sync/validation/mining
- **No code path requires an external RPC host to be reachable** by default
- **Built-in checkpoints** provide hardcoded safety checkpoints for mainnet (and other networks)
- **Optional checkpoint mechanism** can consult a configured RPC URL or local file
- **Checkpoints are safety rails** used during initial sync or fork-choice, not live head oracles
- **Graceful degradation**: if checkpoints are unavailable, sync continues via P2P (unless strict mode)

## Built-in Checkpoints

Built-in checkpoints are hardcoded safety rails that don't require external fetching. These are particularly useful for mainnet to provide a baseline security check.

**Mainnet (chain_id=1):**
- Height 55795: `0x0a3205eb3aca078a9c6e8415e5970e198b43c087bff7b71371054bbbc99d8938`

Built-in checkpoints are always available and can be merged with external checkpoints from RPC or file sources. When there's a conflict, built-in checkpoints take precedence.

## Configuration

Checkpoints are configured via environment variables:

```bash
# Mode: off (default), rpc, or file
export ANIMICA_CHECKPOINTS_MODE=off

# RPC endpoint for fetching checkpoints (when mode=rpc)
export ANIMICA_CHECKPOINTS_RPC_URL=http://144.126.133.21:30337/rpc

# Path to local checkpoint file (when mode=file)
export ANIMICA_CHECKPOINTS_FILE=~/.animica/checkpoints.json

# Optional: Maximum age of checkpoints in seconds
export ANIMICA_CHECKPOINTS_MAX_AGE=86400

# Fail fast if checkpoints unavailable (default: false)
export ANIMICA_CHECKPOINTS_STRICT=false
```

## Checkpoint Modes

### Off (Default)

```bash
export ANIMICA_CHECKPOINTS_MODE=off
```

- No checkpoints used
- Pure P2P consensus
- No external dependencies

### RPC Mode

```bash
export ANIMICA_CHECKPOINTS_MODE=rpc
export ANIMICA_CHECKPOINTS_RPC_URL=http://144.126.133.21:30337/rpc
```

- Fetches checkpoints from RPC endpoint
- Tries `chain.getCheckpoints` JSON-RPC method first
- Falls back to HTTP endpoints (`/checkpoints.json`, `/checkpoints`)
- Non-strict by default: continues without checkpoints if unavailable

### File Mode

```bash
export ANIMICA_CHECKPOINTS_MODE=file
export ANIMICA_CHECKPOINTS_FILE=~/.animica/checkpoints.json
```

- Loads checkpoints from local JSON file
- No network calls
- Useful for air-gapped or private networks

## Checkpoint Format

Checkpoints are stored in JSON format:

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

Or as a plain list:

```json
[
  {"height": 1000, "hash": "0x1234abcd..."},
  {"height": 2000, "hash": "0x5678ef01..."}
]
```

See `fixtures/example_checkpoints.json` for a complete example.

## Usage

### CLI Tool

List built-in checkpoints:

```bash
# List all built-in checkpoints
python -m p2p.checkpoints.cli.checkpoints list

# List checkpoints for mainnet only
python -m p2p.checkpoints.cli.checkpoints list --chain-id 1

# Export mainnet checkpoints to file
python -m p2p.checkpoints.cli.checkpoints export --chain-id 1 --output checkpoints.json

# Export all built-in checkpoints
python -m p2p.checkpoints.cli.checkpoints export --output all_checkpoints.json
```

### Programmatic Initialization

```python
from p2p.checkpoints import initialize_checkpoints, CheckpointsConfig

# Load from environment (includes built-in checkpoints for mainnet)
verifier = await initialize_checkpoints(chain_id=1)

# Load without built-in checkpoints
verifier = await initialize_checkpoints(chain_id=1, include_builtin=False)

# Or with explicit config
config = CheckpointsConfig(mode="file", file_path="/path/to/checkpoints.json")
verifier = await initialize_checkpoints(config, chain_id=1)

# Use with header sync
from p2p.sync.headers import HeaderSync

sync = HeaderSync(
    chain=chain_adapter,
    fetcher=header_fetcher,
    consensus=consensus_view,
    checkpoint_verifier=verifier,  # Optional
)
```

### Built-in Checkpoint Access

```python
from p2p.checkpoints import builtin

# Get built-in checkpoints for mainnet
mainnet_cps = builtin.get_builtin_checkpoints(chain_id=1)

# Check if a chain has built-in checkpoints
has_checkpoints = builtin.has_builtin_checkpoints(chain_id=1)

# Get all built-in checkpoints
all_cps = builtin.get_all_builtin_checkpoints()
```

### Verification

```python
from p2p.checkpoints import verify_chain_checkpoints

# Verify chain against checkpoints
is_valid, errors = await verify_chain_checkpoints(
    verifier=verifier,
    chain_view=chain_adapter,
    max_height=10000,  # Optional
)

if not is_valid:
    for error in errors:
        print(f"Checkpoint mismatch: {error}")
```

## Integration Points

Checkpoints are verified during:

1. **Initial sync**: When DB is empty or far behind
2. **Fork choice**: When adopting a new best chain during reorg
3. **Block import**: When processing individual blocks

The verification is automatic when `checkpoint_verifier` is provided to `HeaderSync`.

## Strict Mode

By default, if checkpoints are unavailable, the node continues syncing via P2P with a warning. Enable strict mode to fail fast:

```bash
export ANIMICA_CHECKPOINTS_STRICT=true
```

With strict mode:
- Node will refuse to start if checkpoints cannot be loaded
- Checkpoint mismatches will halt sync immediately
- Useful for production deployments requiring additional validation

## Testing

Run checkpoint tests:

```bash
pytest p2p/checkpoints/tests/ -v
```

Test coverage includes:
- Configuration loading and validation
- Checkpoint parsing from file and RPC
- Checkpoint verification and mismatch detection
- Cache behavior and expiry
- HTTP call isolation (no calls when disabled)
- Strict vs non-strict mode behavior

## Architecture

```
┌──────────────────┐
│  Environment     │
│  Variables       │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Config Loader   │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐      ┌──────────────────┐
│  Checkpoint      │─────▶│  RPC / File      │
│  Loader          │      │  Source          │
└────────┬─────────┘      └──────────────────┘
         │
         ▼
┌──────────────────┐
│  Checkpoint      │
│  Verifier        │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐      ┌──────────────────┐
│  Header Sync     │─────▶│  Fork Choice     │
└──────────────────┘      └──────────────────┘
```

## Security Considerations

- **Checkpoints are NOT consensus rules**: They're safety rails to prevent syncing to minority forks
- **Trust model**: You trust the checkpoint source (RPC endpoint or local file)
- **Fail-safe**: Default non-strict mode allows syncing to continue if checkpoints unavailable
- **P2P-first**: Checkpoints supplement, not replace, P2P validation
- **No live oracle**: Checkpoints are static safety checks, not live head queries

## Further Reading

- [P2P Sync Guide](../../docs/p2p_sync.md) - Complete P2P architecture documentation
- [Fork Choice](../../consensus/fork_choice.py) - Fork selection algorithm
- [Header Sync](../sync/headers.py) - Header synchronization protocol
