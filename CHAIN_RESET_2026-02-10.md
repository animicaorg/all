# Animica Chain Reset - 2026-02-10

## Overview

This chain reset was performed on **2026-02-10**, introducing new genesis blocks for all networks (mainnet, testnet, and devnet) with version `reset-2026-02-10`. All nodes must reset their chain data and start syncing from block 0.

## Genesis Details

### Mainnet

| Property | Value |
|----------|-------|
| **Chain ID** | `1` |
| **Genesis Hash** | `0x36159f30f1192416ed8c747aef4de5b6fbf5b88f074eab23cd2f79e2b23dde97` |
| **Genesis Time** | `2026-02-10T04:22:00Z` |
| **Genesis Version** | `reset-2026-02-10` |
| **State Root** | `0xdb9a6a1ef950c0a8a9b6f4b0b9ebbd80f0e1c23d7ecb1ebfd01c72f91e4a04a2` |
| **Beacon Seed** | `0x49c7329f0a192ebb5f49aa14dc88bbc5d8facf63e0606ffa00f85baf07f91f04` |
| **Seed Message** | `Animica Mainnet Genesis 2026-02-10` |

### Testnet

| Property | Value |
|----------|-------|
| **Chain ID** | `2` |
| **Genesis Hash** | `0x63f6db3e7677d535f4a93218da2840cd6a64aa47b2825168e8962fc30e10d34e` |
| **Genesis Time** | `2026-02-10T04:22:00Z` |
| **Genesis Version** | `reset-2026-02-10` |
| **State Root** | `0xa8322f6bf020ae533a48d3df61909783805e823b1c9fd0b8945082566babb05c` |
| **Beacon Seed** | `0x1b44ad163b01df0d2fc55ec542c03ffe10e42e9e5ebdf5e3c4cdaae4f0b6f1fb` |
| **Seed Message** | `Animica Testnet Genesis 2026-02-10` |

### Devnet

| Property | Value |
|----------|-------|
| **Chain ID** | `1337` |
| **Genesis Hash** | `0x215e0a0416019380d49c68909942486c00690871470d1d474bb5e609f18ff33b` |
| **Genesis Time** | `2026-02-10T04:22:00Z` |
| **Genesis Version** | `reset-2026-02-10` |
| **State Root** | `0xa8322f6bf020ae533a48d3df61909783805e823b1c9fd0b8945082566babb05c` |
| **Beacon Seed** | `0x26dbe9c7ec0e9f69f45ad5d1e9e08e03deb34d9c18b8ef93bb6e16bc11d77aad` |
| **Seed Message** | `Animica Devnet Genesis 2026-02-10` |

## What Changed

- **New genesis blocks**: All networks have new genesis blocks with different hashes
- **New genesis timestamp**: Updated from `2026-02-08T02:50:03Z` to `2026-02-10T04:22:00Z`
- **New seed messages**: Each network has a unique seed message for beacon randomness
- **New beacon seeds**: Deterministically derived from the new seed messages
- **Same allocations**: Account balances and premine remain unchanged from previous reset

## Impact

- **Breaking change**: All existing nodes must reset their chain data
- **No data migration**: Previous chain data is incompatible and must be discarded
- **Fresh start**: All chains start from block height 0 with the new genesis

## Node Reset Instructions

### 1. Stop the node

```bash
# Stop the running node process
pkill -f animica-node
# or use systemctl if running as a service
systemctl stop animica-node
```

### 2. Backup old data (optional)

```bash
# Archive old chain data for reference
mv ~/.animica/data ~/.animica/data.backup-2026-02-08
```

### 3. Clean chain data

```bash
# Remove old chain database
rm -rf ~/.animica/data
mkdir -p ~/.animica/data
```

### 4. Update node software

```bash
# Pull latest code with new genesis
git pull origin main

# Rebuild if necessary
./build.sh
```

### 5. Start node with new genesis

```bash
# The node will automatically load the new genesis from core/genesis/
animica-node --chain-id 1  # for mainnet
# or
animica-node --chain-id 2  # for testnet
# or
animica-node --chain-id 1337  # for devnet
```

## Verification

To verify your node is running the correct genesis:

```bash
# Check genesis hash via RPC
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"chain_getChainIdentity","params":[],"id":1}' \
  | jq '.result.genesisHash'

# Should return:
# Mainnet: "0x36159f30f1192416ed8c747aef4de5b6fbf5b88f074eab23cd2f79e2b23dde97"
# Testnet: "0x63f6db3e7677d535f4a93218da2840cd6a64aa47b2825168e8962fc30e10d34e"
# Devnet:  "0x215e0a0416019380d49c68909942486c00690871470d1d474bb5e609f18ff33b"
```

## Technical Details

### Genesis Hash Computation

The genesis hash is computed deterministically from the genesis header:

```
genesis_hash = sha3_256(CBOR_canonical(genesis_header))
```

Where the genesis header includes:
- State root (derived from account allocations)
- Beacon seed (sha3_256 of seed message)
- Timestamp
- Chain parameters

### Beacon Seed Generation

Beacon seeds are deterministically generated:

```python
beacon_seed = sha3_256(seed_message.encode('utf-8'))
```

Examples:
- Mainnet: `sha3_256(b"Animica Mainnet Genesis 2026-02-10")`
- Testnet: `sha3_256(b"Animica Testnet Genesis 2026-02-10")`
- Devnet: `sha3_256(b"Animica Devnet Genesis 2026-02-10")`

## Previous Genesis Hashes

For reference, the previous genesis hashes (2026-02-08 reset) were:
- Mainnet: `0x8ec4a0b923005e9039b815e526990359119e6f5492d5038aa898d6f8eee52adc`
- Testnet: `0x6da84a9b99fddadfc4afeed9941e38fc53f24455a747091bcb0e4c67393c8627`
- Devnet: `0x09f94fca928ba59e2a43a0af11d684ee5145a35fced3377606e00cf35ec8db65`

## Support

If you encounter issues during the reset:
1. Check the node logs for errors
2. Verify the genesis hash matches the expected value
3. Ensure all chain data was properly cleared
4. Join the Discord for support: https://discord.gg/animica

## Files Changed

The following files were updated in this reset:
- `core/genesis/mainnet.json` - New mainnet genesis
- `core/genesis/testnet.json` - New testnet genesis
- `core/genesis/devnet.json` - New devnet genesis
- `core/genesis/genesis.json` - Copy of mainnet genesis
- `core/network_params.py` - Updated genesis hash constants
- `p2p/checkpoints/builtin.py` - Updated P2P checkpoints
- `spec/chains.json` - Updated chain metadata
- `chains/animica.mainnet.json` - Updated mainnet config
