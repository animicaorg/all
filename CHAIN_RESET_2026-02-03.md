# Animica Chain Reset - 2026-02-03

## Overview

This chain reset was performed on **2026-02-03**, introducing new genesis blocks for all networks (mainnet, testnet, and devnet). All nodes must reset their chain data and start syncing from block 0.

## Genesis Details

### Mainnet

| Property | Value |
|----------|-------|
| **Chain ID** | `1` |
| **Genesis Hash** | `0x69cbf43cbfd78aba3de2189156bae4145827f2e54638185216ffef73c7e93b3a` |
| **Genesis File Hash** | `0x87125eeb4b1f76a03f03faca4370ddda738404a6691367d0761bc893abed397b` |
| **Genesis Time** | `2026-02-03T05:09:17Z` |
| **Genesis Version** | `reset-2026-02-03` |
| **Fork ID** | `233506716` |
| **Consensus ID** | `consensus/67bf865f28e51e6d373ac5e39a89a350dbc95c0e64f34aa7082dbe3a3a8023f2` |
| **State Root** | `0xdb9a6a1ef950c0a8a9b6f4b0b9ebbd80f0e1c23d7ecb1ebfd01c72f91e4a04a2` |

### Testnet

| Property | Value |
|----------|-------|
| **Chain ID** | `2` |
| **Genesis Hash** | `0xd5e59e3633cb0eebc4fbc8eb628511fcd87b5835c0d76fa701bb5ae9420ef5ec` |
| **Genesis File Hash** | `0x519d5a1636037c840e643772f9777fa160e9e7c88ebd0707d69d5e7ad56ad45a` |
| **Genesis Time** | `2026-02-03T05:09:17Z` |
| **Genesis Version** | `reset-2026-02-03` |
| **Fork ID** | `1158224053` |
| **Consensus ID** | `consensus/cc00a9d9c5a96b80e367ec3978ec02de0d8c37eabf56ec4ef36c103d684377ec` |
| **State Root** | `0xa8322f6bf020ae533a48d3df61909783805e823b1c9fd0b8945082566babb05c` |

### Devnet

| Property | Value |
|----------|-------|
| **Chain ID** | `1337` |
| **Genesis Hash** | `0x1b9f7afab6542e843dbc2c87a9a49d57e5068c10fc02698548d65d08e9c5437c` |
| **Genesis File Hash** | `0x5dce708742b841d8b94c8af2c159db4f01d6c3406198051f0578054f5d3cfa43` |
| **Genesis Time** | `2026-02-03T05:09:17Z` |
| **Genesis Version** | `reset-2026-02-03` |
| **Fork ID** | `1127859294` |
| **Consensus ID** | `consensus/9f0a31f91908b2b759c1719a14ed9cfff38930da24c859faf13a0083a079b294` |
| **State Root** | `0xa8322f6bf020ae533a48d3df61909783805e823b1c9fd0b8945082566babb05c` |

## What Changed

- **New genesis blocks**: All networks have new genesis blocks with different hashes
- **New timestamps**: Genesis time updated to 2026-02-03T05:09:17Z
- **New beacon seeds**: Each network has a unique new beacon seed to ensure different genesis hashes
- **P2P enforcement**: Nodes will refuse connections from peers with mismatched genesis hashes
- **Database isolation**: The datadir guard prevents accidental reuse of old chain data
- **Consensus parameters**: Initial theta and gamma cap remain at `1000000` and `2000000` respectively

### Technical Changes

All genesis files have been updated:
- `core/genesis/mainnet.json` (version 7)
- `core/genesis/testnet.json` (version 5)
- `core/genesis/devnet.json` (version 5)
- `core/genesis/genesis.json` (version 7, symlink to mainnet)

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
# 0x69cbf43cbfd78aba3de2189156bae4145827f2e54638185216ffef73c7e93b3a

# For testnet, the hash should be:
# 0xd5e59e3633cb0eebc4fbc8eb628511fcd87b5835c0d76fa701bb5ae9420ef5ec

# For devnet, the hash should be:
# 0x1b9f7afab6542e843dbc2c87a9a49d57e5068c10fc02698548d65d08e9c5437c
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
3. The old genesis hash was: `0x811b2dd9f725f2a3d21a3a69f280b8d9c7b01e06f28b8661a0c315314a622d1d` (mainnet)

## Support

If you encounter issues:

1. Check the [troubleshooting guide](docs/TROUBLESHOOTING.md)
2. Join the [Discord community](https://discord.gg/animica)
3. File an issue on [GitHub](https://github.com/animicaorg/all/issues)

## Timeline

- **2026-02-03 05:09:17 UTC**: New genesis time
- **2026-02-03**: Chain reset performed
- **2026-02-03+**: All nodes must upgrade to continue participating in the network

## Technical Details

### Beacon Seeds

New unique beacon seeds were generated for each network:

- **Mainnet**: `0xd01a3f693aa515f4af5d2e57130304b6f2eae8ef140f47f9e0bfdfdd7e4ed865`
- **Testnet**: `0x29ab8da5cc2a41c22ea3058f6d9a388b0ce1c182145487af0c10a4917865bc96`
- **Devnet**: `0x3fed32785aba883e2c69aac08da056e6eeafe287bc685dec7477ec105cebbc38`

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
