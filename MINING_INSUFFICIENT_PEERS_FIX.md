# Mining "insufficient_peers" Error Fix

## Problem

Users attempting to mine blocks encounter the following error when their node cannot connect to peers:

```
Warning: Block template unavailable (insufficient_peers)
Warning: No blocks were mined (may have failed)
```

This error message doesn't provide any guidance on how to fix the issue, leaving users unable to mine.

## Root Cause

The node requires at least 1 connected peer to mine by default (controlled by `ANIMICA_MINING_MIN_PEERS` environment variable, which defaults to 1). When peer connections fail, mining is blocked.

## Solution

### Improved Error Messages

The error messages now provide actionable guidance:

```
Warning: Block template unavailable (insufficient_peers (connected: 0, required: 1). Try: 'animica peer bootstrap' to connect to peers, or set ANIMICA_MINING_MIN_PEERS=0 for local development.)
```

### Two Ways to Fix

#### Option 1: Allow Mining Without Peers (Local Development)

For local development and testing, you can allow mining without peers:

```bash
export ANIMICA_MINING_MIN_PEERS=0
animica miner mine-blocks --address <your-address> --count 1
```

This skips all peer connection checks and allows mining to proceed.

#### Option 2: Connect to Bootstrap Peers (Production)

For production use, connect to bootstrap peers:

```bash
animica peer bootstrap
# Wait for peers to connect
animica peer list  # Verify peers are connected
animica miner mine-blocks --address <your-address> --count 1
```

## Configuration

The `ANIMICA_MINING_MIN_PEERS` environment variable controls the minimum number of connected peers required for mining:

- **Default**: `1` (requires at least 1 connected peer)
- **For local development**: Set to `0` to allow mining without peers
- **For production**: Keep at `1` or higher to ensure your node is connected to the network

### Examples

```bash
# Allow mining without peers (local development)
export ANIMICA_MINING_MIN_PEERS=0

# Require 3 connected peers (production)
export ANIMICA_MINING_MIN_PEERS=3

# Use default (1 peer required)
unset ANIMICA_MINING_MIN_PEERS
```

## Testing

To verify the fix works:

```bash
# Test 1: Verify error message with no peers
export ANIMICA_MINING_MIN_PEERS=1
animica miner mine-blocks --address <address> --count 1
# Expected: Error message with guidance

# Test 2: Verify mining works with min_peers=0
export ANIMICA_MINING_MIN_PEERS=0
animica miner mine-blocks --address <address> --count 1
# Expected: Mining proceeds successfully
```

## Technical Details

### Changes Made

1. **Error Messages** (`rpc/methods/miner.py`)
   - Enhanced `_mining_gate()` function to return detailed error messages
   - Messages now include current/required peer counts and actionable guidance

2. **Offline Mining Logic**
   - Modified offline mining check to honor `ANIMICA_MINING_MIN_PEERS=0`
   - Both peer checks now respect the min_peers setting

3. **Documentation** (`mining/README.md`)
   - Added `ANIMICA_MINING_MIN_PEERS` to configuration section
   - Documented default value and usage

### Backward Compatibility

- Default behavior unchanged (still requires 1 peer)
- Existing deployments continue to work without changes
- New environment variable only affects behavior when explicitly set

## See Also

- Mining README: `mining/README.md`
- Peer CLI: `python -m animica.cli.peer`
- Mining CLI: `python -m mining.cli.miner`
