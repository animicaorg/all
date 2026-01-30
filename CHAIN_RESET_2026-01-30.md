# Animica Mainnet Chain Reset - 2026-01-30

## Overview

This document describes the mainnet chain reset performed on **2026-01-30**, which introduces a new genesis block and requires all nodes to reset their chain data and start syncing from block 0.

## What Changed

### Genesis Details

| Property | Value |
|----------|-------|
| **Chain ID** | `1` (unchanged) |
| **Genesis Hash** | `0xc1eccb71ac099d12670533ce5d7c01cf76c48544e80647f7d000f8c633313844` |
| **Genesis Time** | `2026-01-30T06:17:43Z` |
| **Genesis Version** | `reset-2026-01-30` |
| **Beacon Seed** | `0xf9c887e345f49b56b5ee9d620bde8f242c2bb3b4a68582c7a70bb7459946076c` |
| **State Root** | `0xdb9a6a1ef950c0a8a9b6f4b0b9ebbd80f0e1c23d7ecb1ebfd01c72f91e4a04a2` |
| **Fork ID** | `2090605621` |

### Technical Changes

- **New genesis block**: All nodes must start from height 0 with the new genesis
- **P2P enforcement**: Nodes will refuse connections from peers with mismatched genesis hashes
- **Database isolation**: The datadir guard prevents accidental reuse of old chain data
- **Consensus parameters**: Initial theta and gamma cap remain at `1000000` and `2000000` respectively

### Why Reset?

This reset provides a clean starting point for the blockchain, ensuring all nodes start from a common, verified genesis state. The premine allocation and system accounts remain unchanged from the previous genesis.

## Node Operator Actions Required

### Option 1: Automatic Reset (Recommended)

The easiest way to handle the genesis reset is to enable automatic reset detection:

```bash
# Stop your node if running
animica node down

# Start with auto-reset enabled
animica node up --auto-reset-genesis-mismatch

# Or set environment variable for persistent auto-reset
export ANIMICA_AUTO_RESET_GENESIS_MISMATCH=1
animica node up
```

This will automatically detect the genesis mismatch and wipe old data before syncing with the new genesis.

### Option 2: Manual Reset

If you prefer manual control:

```bash
# 1. Stop the node
animica node down

# 2. Reset the chain data
animica node reset --network mainnet --yes

# 3. Start the node
animica node up
```

### Option 3: Clean Data Directory

For Docker or custom deployments:

```bash
# Stop your node
docker-compose down

# Remove the data directory (mainnet uses chain-1)
rm -rf ~/.animica/chain-1/

# Restart node (will initialize from new genesis)
docker-compose up -d
```

## Verification

After restarting your node, verify it's using the correct genesis:

```bash
# Check genesis hash
animica rpc call chain.getBlock '{"params": [0]}'

# Should show:
# {
#   "height": 0,
#   "hash": "0xc1eccb71ac099d12670533ce5d7c01cf76c48544e80647f7d000f8c633313844",
#   ...
# }
```

Or check the logs for the genesis hash on startup:

```bash
animica node logs | grep -i "genesis hash"
# Should show: Genesis hash: 0xc1eccb71ac099d12670533ce5d7c01cf76c48544e80647f7d000f8c633313844
```

## For Developers

### Genesis File Changes

The genesis file (`core/genesis/genesis.json`) was updated with:

1. **New genesis time**: `2026-01-30T06:17:43Z`
2. **New genesis version**: `reset-2026-01-30`
3. **New beacon seed**: `0xf9c887e345f49b56b5ee9d620bde8f242c2bb3b4a68582c7a70bb7459946076c`

These changes result in a new genesis hash being computed deterministically. The genesis hash is derived from the genesis header, which includes the genesis time, beacon seed, and other genesis parameters.

### Testing Genesis Changes

You can verify the genesis identity using Python:

```python
from core.genesis.loader import compute_genesis_identity

identity = compute_genesis_identity('core/genesis/genesis.json')
print(f"Genesis Hash: 0x{identity.genesis_block_hash.hex()}")
print(f"Chain ID: {identity.chain_id}")
print(f"Fork ID: {identity.fork_id}")
```

Expected output:
```
Genesis Hash: 0xc1eccb71ac099d12670533ce5d7c01cf76c48544e80647f7d000f8c633313844
Chain ID: 1
Fork ID: 2090605621
```

## Network Compatibility

- **Breaking Change**: This is a breaking change that requires all nodes to reset
- **P2P Isolation**: Nodes with different genesis hashes cannot communicate
- **No Backward Compatibility**: Old chain data cannot be used with the new genesis

## Support

If you encounter issues during the reset:

1. Check that you're running the latest version of the node software
2. Ensure all old chain data is completely removed before restarting
3. Verify your genesis hash matches `0xc1eccb71ac099d12670533ce5d7c01cf76c48544e80647f7d000f8c633313844`
4. Check P2P connectivity with other nodes

For additional help, refer to the main documentation or open an issue on GitHub.
