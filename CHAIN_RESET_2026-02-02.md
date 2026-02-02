# Animica Chain Reset - 2026-02-02

## Overview

This chain reset was performed on **2026-02-02**, introducing new genesis blocks for all networks (mainnet, testnet, and devnet). All nodes must reset their chain data and start syncing from block 0.

## Genesis Details

### Mainnet

| Property | Value |
|----------|-------|
| **Chain ID** | `1` |
| **Genesis Hash** | `0x811b2dd9f725f2a3d21a3a69f280b8d9c7b01e06f28b8661a0c315314a622d1d` |
| **Genesis File Hash** | `0x5067fa8060540c452d240a2dce6dd5c5f4c1a6b60a5c4b9d7b2fac6ac75d5fba` |
| **Genesis Time** | `2026-02-02T00:30:50Z` |
| **Genesis Version** | `reset-2026-02-02` |
| **Fork ID** | `911452296` |
| **Consensus ID** | `consensus/50df3fde7f97f3ffd7b0d5d535e6016719bffade4f1dfd165bf146b589e4bc6d` |
| **State Root** | `0xdb9a6a1ef950c0a8a9b6f4b0b9ebbd80f0e1c23d7ecb1ebfd01c72f91e4a04a2` |

### Testnet

| Property | Value |
|----------|-------|
| **Chain ID** | `2` |
| **Genesis Hash** | `0xb7f811752e52c61b29b8da5196b27a93684004a2a47309439d9467a0fb8f1c9b` |
| **Genesis File Hash** | `0x1cfc5bfafb91ddafc73a010a97334497780db955304c293d69055bfe5ac812ff` |
| **Genesis Time** | `2026-02-02T00:30:50Z` |
| **Genesis Version** | `reset-2026-02-02` |
| **Fork ID** | `2557686956` |
| **Consensus ID** | `consensus/cf935e60719c3a7f9db692021e54a911035ace8213f78b54f49930c5d4a1c8d0` |
| **State Root** | `0xa8322f6bf020ae533a48d3df61909783805e823b1c9fd0b8945082566babb05c` |

### Devnet

| Property | Value |
|----------|-------|
| **Chain ID** | `1337` |
| **Genesis Hash** | `0xb73c699ef881472743ee6c6ba2ea6c6e81c9a8ffc9dd7a743ef024c2243cae10` |
| **Genesis File Hash** | `0x279f4476a98aa0a43e6dbef6a9792623449712533be838cb5c419395dce4398e` |
| **Genesis Time** | `2026-02-02T00:30:50Z` |
| **Genesis Version** | `reset-2026-02-02` |
| **Fork ID** | `1337480097` |
| **Consensus ID** | `consensus/ee683fc8d68f06e27395ec98a30de4b0181b70c5205ca298161da53d0fea22e4` |
| **State Root** | `0xa8322f6bf020ae533a48d3df61909783805e823b1c9fd0b8945082566babb05c` |

## What Changed

- **New genesis blocks**: All networks have new genesis blocks with different hashes
- **New timestamps**: Genesis time updated to 2026-02-02T00:30:50Z
- **New beacon seeds**: Each network has a unique new beacon seed to ensure different genesis hashes
- **P2P enforcement**: Nodes will refuse connections from peers with mismatched genesis hashes
- **Database isolation**: The datadir guard prevents accidental reuse of old chain data
- **Consensus parameters**: Initial theta and gamma cap remain at `1000000` and `2000000` respectively

### Technical Changes

All genesis files have been updated:
- `core/genesis/mainnet.json` (version 6)
- `core/genesis/testnet.json` (version 4)
- `core/genesis/devnet.json` (version 4)
- `core/genesis/genesis.json` (version 6, symlink to mainnet)

All pinned genesis hash references updated in:
- `core/network_params.py`
- `spec/chains.json`
- `chains/animica.mainnet.json`
- `chains/animica.testnet.json`
- `chains/animica.localnet.json`
- `p2p/checkpoints/builtin.py`
- `p2p/checkpoints/tests/test_builtin.py`

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

# 2. Reset the chain data for your network
animica node reset --network mainnet --yes    # For mainnet
animica node reset --network testnet --yes    # For testnet
animica node reset --network devnet --yes     # For devnet

# 3. Start the node
animica node up
```

### Option 3: Clean Data Directory

For Docker or custom deployments:

```bash
# Stop your node
docker-compose down

# Remove the data directory
# Mainnet uses chain-1, testnet uses chain-2, devnet uses chain-1337
rm -rf ~/.animica/chain-1/     # Mainnet
rm -rf ~/.animica/chain-2/     # Testnet
rm -rf ~/.animica/chain-1337/  # Devnet

# Restart node (will initialize from new genesis)
docker-compose up -d
```

## Verification

After restarting your node, verify it's using the correct genesis:

### Via RPC

```bash
# Check genesis hash for mainnet
animica rpc call chain.getBlock '{"params": [0]}'

# Should show for mainnet:
# {
#   "height": 0,
#   "hash": "0x811b2dd9f725f2a3d21a3a69f280b8d9c7b01e06f28b8661a0c315314a622d1d",
#   ...
# }
```

### Via Logs

Check the logs for the genesis hash on startup:

```bash
# View logs
animica node logs | grep -i genesis

# Should show for mainnet:
# Genesis Hash: 0x811b2dd9f725f2a3d21a3a69f280b8d9c7b01e06f28b8661a0c315314a622d1d
```

### Via Python

```python
from core.genesis.loader import compute_genesis_identity

identity = compute_genesis_identity('core/genesis/mainnet.json')
print(f"Genesis Hash: 0x{identity.genesis_block_hash.hex()}")
print(f"Chain ID: {identity.chain_id}")
print(f"Fork ID: {identity.fork_id}")
```

## Why Reset?

This reset provides a clean starting point for the blockchain, ensuring all nodes start from a common, verified genesis state. The premine allocation and system accounts remain unchanged from the previous genesis configuration.

## Support

If you encounter issues during the reset:

1. Ensure you're using the latest version of the Animica node software
2. Verify your genesis hash matches the values above
3. Check that your data directory was properly cleared
4. Review the logs for any error messages

For additional help, please refer to the documentation or contact support.
