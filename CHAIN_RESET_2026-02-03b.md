# Animica Chain Reset - 2026-02-03b

## Overview

This chain reset was performed on **2026-02-03**, introducing new genesis blocks for all networks (mainnet, testnet, and devnet) with version `reset-2026-02-03b`. All nodes must reset their chain data and start syncing from block 0.

## Genesis Details

### Mainnet

| Property | Value |
|----------|-------|
| **Chain ID** | `1` |
| **Genesis Hash** | `0x98451b849722c0da71138d7c648c004d553a067cbac7b45b82eb50c2226f8d3e` |
| **Genesis File Hash** | `0x6d8e91ab9fada5a6640f5995247142927a3e6c0d89daa4aae89a1700c8a1c8d7` |
| **Genesis Time** | `2026-02-03T20:55:38Z` |
| **Genesis Version** | `reset-2026-02-03b` |
| **Fork ID** | `3480286077` |
| **Consensus ID** | `consensus/00fac2edcbee1fe7fcf9c52fe76494bd340d8f377606e538b783d803d9a24a9f` |
| **State Root** | `0xdb9a6a1ef950c0a8a9b6f4b0b9ebbd80f0e1c23d7ecb1ebfd01c72f91e4a04a2` |

### Testnet

| Property | Value |
|----------|-------|
| **Chain ID** | `2` |
| **Genesis Hash** | `0x97cdf1333c13e947208e4e3a47ccbdd0118be192680b256b89c16b2083f6ecd1` |
| **Genesis File Hash** | `0xdc9abeaa9b3367ffccc49add9e7a93225c2607a7b6cb445f04c28c5db7402715` |
| **Genesis Time** | `2026-02-03T20:55:38Z` |
| **Genesis Version** | `reset-2026-02-03b` |
| **Fork ID** | `3563951439` |
| **Consensus ID** | `consensus/1fde72bedd8e95fce31abb20a3eac2e6db109473ec0b344b18562d7858a9b0bd` |
| **State Root** | `0xa8322f6bf020ae533a48d3df61909783805e823b1c9fd0b8945082566babb05c` |

### Devnet

| Property | Value |
|----------|-------|
| **Chain ID** | `1337` |
| **Genesis Hash** | `0x0a489e0d737a8962af5435c0c9e0f9a4fa6bfd9e6a208ea8c46d9cc5bcb9bda8` |
| **Genesis File Hash** | `0x2fe37f658a96819efd6daf90de8de94d4c0c9ad753b0d27d3014ad2a998cb495` |
| **Genesis Time** | `2026-02-03T20:55:38Z` |
| **Genesis Version** | `reset-2026-02-03b` |
| **Fork ID** | `303450816` |
| **Consensus ID** | `consensus/fabdc3a27c9d7a1dc08cb7400abc80b8184cfca1260c59ecac90ec290455a4b0` |
| **State Root** | `0xa8322f6bf020ae533a48d3df61909783805e823b1c9fd0b8945082566babb05c` |

## What Changed

- **New genesis blocks**: All networks have new genesis blocks with different hashes
- **New timestamps**: Genesis time updated to 2026-02-03T20:55:38Z
- **New beacon seeds**: Each network has a unique new beacon seed to ensure different genesis hashes
- **P2P enforcement**: Nodes will refuse connections from peers with mismatched genesis hashes
- **Database isolation**: The datadir guard prevents accidental reuse of old chain data
- **Consensus parameters**: Initial theta and gamma cap remain at `1000000` and `2000000` respectively

### Technical Changes

All genesis files have been updated:
- `core/genesis/mainnet.json` (version 8)
- `core/genesis/testnet.json` (version 6)
- `core/genesis/devnet.json` (version 6)
- `core/genesis/genesis.json` (version 8, reference to mainnet)

All pinned genesis hash references updated in:
- `core/network_params.py`
- `spec/chains.json`
- `chains/animica.mainnet.json`
- `chains/animica.testnet.json`
- `chains/animica.localnet.json`
- `p2p/checkpoints/builtin.py`
- `p2p/checkpoints/tests/test_builtin.py`
- `scripts/tests/test_update_genesis_hash.py`

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

# 2. Back up your data (optional but recommended)
cd ~/.animica
tar -czf backup-$(date +%Y%m%d).tar.gz chain-1/

# 3. Remove old chain data
rm -rf ~/.animica/chain-1/*

# 4. Start the node (it will initialize with the new genesis)
animica node up
```

### Option 3: Update Genesis Hash Only (For Verifiers)

If you're a verifier node that hasn't synced yet, you can update just the genesis hash:

```bash
# For mainnet
python scripts/update_genesis_hash.py --network mainnet

# For testnet
python scripts/update_genesis_hash.py --network testnet

# For devnet
python scripts/update_genesis_hash.py --network devnet
```

## Verification

After restarting your node, verify it's using the correct genesis:

```bash
# Check genesis hash via RPC
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"chain_getBlockByHeight","params":[0],"id":1}'

# For mainnet, the hash should be:
# 0x98451b849722c0da71138d7c648c004d553a067cbac7b45b82eb50c2226f8d3e

# For testnet, the hash should be:
# 0x97cdf1333c13e947208e4e3a47ccbdd0118be192680b256b89c16b2083f6ecd1

# For devnet, the hash should be:
# 0x0a489e0d737a8962af5435c0c9e0f9a4fa6bfd9e6a208ea8c46d9cc5bcb9bda8
```

## P2P Network Impact

- Nodes with the old genesis will not be able to connect to nodes with the new genesis
- The P2P network will naturally split, with old nodes forming one network and new nodes forming another
- All official seed nodes and bootnodes have been updated to use the new genesis
- Old nodes will eventually become isolated as more nodes upgrade

## Developer Actions

If you're developing on Animica:

1. **Pull latest code**: `git pull origin main`
2. **Clear local chain data**: `rm -rf ~/.animica/chain-*/`
3. **Restart your development environment**: Your node will initialize with the new genesis automatically

## Smart Contract Impact

- **All deployed contracts are reset**: Contracts will need to be redeployed
- **All transaction history is reset**: The chain starts fresh from block 0
- **All account balances are reset**: Only genesis allocations exist initially
- **Contract addresses will be the same**: If you redeploy with the same nonce, contracts will have the same address

## Rollback Procedure

There is no rollback for this genesis reset. The chain has permanently moved to the new genesis. If you need access to old chain data:

1. Keep a backup of your old data directory
2. Use an archive node or block explorer that captured the old chain
3. The old genesis hash was: `0x69cbf43cbfd78aba3de2189156bae4145827f2e54638185216ffef73c7e93b3a` (mainnet, reset-2026-02-03)

## Support

If you encounter issues:

1. Check the [troubleshooting guide](docs/TROUBLESHOOTING.md)
2. Join the [Discord community](https://discord.gg/animica)
3. File an issue on [GitHub](https://github.com/animicaorg/all/issues)

## Timeline

- **2026-02-03 20:55:38 UTC**: New genesis time
- **2026-02-03**: Chain reset performed
- **2026-02-03+**: All nodes must upgrade to continue participating in the network

## Technical Details

### Beacon Seeds

New unique beacon seeds were generated for each network:

- **Mainnet**: `0x8418c7029bd5df9c7dbaf53e7b1cf7b09494fca46643836ef50ff189b4dded7f`
- **Testnet**: `0xf0abbcc73729798c966163dcb27f60751b64764632686e8473bdd29e3dcd72e0`
- **Devnet**: `0xcac80f5bbd78459476bef5fb2898d3f1d31c843bc74045212f3dff8db1777823`

Seed messages used:
- Mainnet: "Animica Mainnet Genesis Reset 2026-02-03T20:55:38Z"
- Testnet: "Animica Testnet Genesis Reset 2026-02-03T20:55:38Z"
- Devnet: "Animica Devnet Genesis Reset 2026-02-03T20:55:38Z"

### State Root Computation

State roots are computed deterministically from the genesis allocations using:
- Canonical CBOR encoding
- SHA3-256 hashing
- Merkle tree construction

The same allocations will always produce the same state root, ensuring all nodes agree on the genesis state.

### Fork ID Calculation

Fork IDs are derived from:
- Chain ID
- Genesis hash
- Protocol version
- Consensus parameters

This ensures network isolation and prevents accidental cross-network connections.

## Summary

This is a clean reset of all networks to start fresh from block 0. The genesis hash has changed, requiring all nodes to wipe their old data and sync from the new genesis. The allocations remain the same (mainnet premine, testnet/devnet test allocations), but the timing and seeds are new.
