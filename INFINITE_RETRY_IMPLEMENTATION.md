# Infinite Retry Implementation for Miner and Node Status Commands

## Overview

This implementation adds infinite retry logic with configurable delays to the Animica miner and node status commands, addressing issues where RPC operations could fail after a fixed number of retries, impacting resilience during network disruptions.

## Changes Made

### 1. Mining CLI (`mining/cli/miner.py`)

**New Features:**
- Added `--retry-delay` parameter (default: 1.0 seconds)
- Implemented infinite retry loop for RPC operations
- Timestamps logged for each retry attempt
- Distinguishes between retriable (network/connection errors) and non-retriable errors (RpcError from server)
- Validates retry delay > 0
- Supports `ANIMICA_RETRY_DELAY` environment variable

**Behavior:**
- Retries indefinitely on: `RuntimeError`, `ConnectionError`, `OSError`, `TimeoutError`
- Exits immediately on: `RpcError` (server-side application errors)
- Logs each retry with format: `[YYYY-MM-DD HH:MM:SS] Retrying mining operation due to RPC error (attempt N): <error>. Retrying in X.Xs...`

### 2. Node Status CLI (`python/animica/cli/node.py`)

**New Features:**
- Added `--retry-delay` parameter (default: 1.0 seconds)
- Implemented infinite retry loop for status query operations
- Timestamps logged for each retry attempt
- Validates retry delay > 0
- Supports `ANIMICA_RETRY_DELAY` environment variable

**Behavior:**
- Retries indefinitely on any exception during RPC calls
- Logs each retry with format: `[YYYY-MM-DD HH:MM:SS] Retrying node status query due to RPC error (attempt N): <error>. Retrying in X.Xs...`

## Usage Examples

### Mining Command

```bash
# Basic usage (default 1.0 second retry delay)
python -m mining.cli.miner mine-blocks --address anim1test123 --count 5 --threads 4

# Custom retry delay
python -m mining.cli.miner mine-blocks --address anim1test123 --count 5 --retry-delay 2.5

# Using environment variable
export ANIMICA_RETRY_DELAY=2.0
python -m mining.cli.miner mine-blocks --address anim1test123 --count 5
```

### Node Status Command

```bash
# Basic usage (default 1.0 second retry delay)
animica node status

# Custom retry delay
animica node status --retry-delay 1.5

# Using environment variable
export ANIMICA_RETRY_DELAY=2.0
animica node status
```

## Testing

### Test Coverage

**Mining CLI Tests:**
- `test_mine_blocks_retries_on_connection_error` - Validates retry on connection failures
- `test_mine_blocks_accepts_retry_delay_parameter` - Validates custom retry delay parameter
- `test_mine_blocks_rejects_invalid_retry_delay` - Validates rejection of invalid retry delays

**Node Status Tests:**
- `test_status_retries_on_connection_error` - Validates retry on connection failures
- `test_status_accepts_retry_delay_parameter` - Validates custom retry delay parameter
- `test_status_rejects_invalid_retry_delay` - Validates rejection of invalid retry delays

### Running Tests

```bash
# Run mining CLI tests
pytest mining/cli/tests/test_miner_cli.py::TestMineBlocksCommand::test_mine_blocks_retries_on_connection_error -v
pytest mining/cli/tests/test_miner_cli.py::TestMineBlocksCommand::test_mine_blocks_accepts_retry_delay_parameter -v
pytest mining/cli/tests/test_miner_cli.py::TestMineBlocksCommand::test_mine_blocks_rejects_invalid_retry_delay -v

# Run node status tests
pytest python/animica/cli/tests/test_node_cli.py::test_status_retries_on_connection_error -v
pytest python/animica/cli/tests/test_node_cli.py::test_status_accepts_retry_delay_parameter -v
pytest python/animica/cli/tests/test_node_cli.py::test_status_rejects_invalid_retry_delay -v
```

## Documentation Updates

### Updated Files:
1. `mining/cli/miner.py` - Updated CLI docstring with retry behavior
2. `python/animica/cli/README.md` - Added retry behavior section

### Key Documentation Points:
- Default retry delay: 1.0 second
- Configuration via `--retry-delay` flag or `ANIMICA_RETRY_DELAY` environment variable
- Each retry is logged with timestamp and error reason
- Non-retriable errors (e.g., invalid parameters) exit immediately

## Configuration

### Command-Line Flags
- `--retry-delay SECONDS` - Set retry delay in seconds (must be > 0)

### Environment Variables
- `ANIMICA_RETRY_DELAY` - Default retry delay in seconds

### Priority Order
1. Command-line flag `--retry-delay`
2. Environment variable `ANIMICA_RETRY_DELAY`
3. Built-in default (1.0 second)

## Error Handling

### Retriable Errors (Infinite Retry)
- Network connection errors
- Timeout errors
- Transient RPC failures
- HTTP 502, 503, 504 errors

### Non-Retriable Errors (Immediate Exit)
- Invalid parameters (RpcError from server)
- Invalid configuration (retry delay <= 0)
- Missing required arguments

## Validation

All tests pass successfully:
```
✓ test_mine_blocks_retries_on_connection_error
✓ test_mine_blocks_accepts_retry_delay_parameter
✓ test_mine_blocks_rejects_invalid_retry_delay
✓ test_status_retries_on_connection_error
✓ test_status_accepts_retry_delay_parameter
✓ test_status_rejects_invalid_retry_delay
```

## Impact

### Benefits:
- **Improved Resilience**: Operations continue through temporary network disruptions
- **User Control**: Configurable retry delays for different network conditions
- **Better Observability**: Timestamped logs show retry attempts and reasons
- **Backward Compatible**: Existing usage patterns continue to work

### Breaking Changes:
- None. Default behavior is more resilient but commands still work the same way.

## Future Enhancements

Potential improvements for future iterations:
- Exponential backoff with jitter (currently uses fixed delay)
- Maximum retry duration (timeout after N seconds rather than infinite retries)
- Retry statistics (total attempts, total time spent retrying)
- Circuit breaker pattern for repeated failures
