# Animica Chain Reset - 2026-02-08

## Overview

This chain reset was performed on **2026-02-08**, introducing new genesis blocks for all networks (mainnet, testnet, and devnet) with version `reset-2026-02-08`. All nodes must reset their chain data and start syncing from block 0.

## Genesis Details

### Mainnet

| Property | Value |
|----------|-------|
| **Chain ID** | `1` |
| **Genesis Hash** | `0x8ec4a0b923005e9039b815e526990359119e6f5492d5038aa898d6f8eee52adc` |
| **Genesis Time** | `2026-02-08T02:50:03Z` |
| **Genesis Version** | `reset-2026-02-08` |
| **State Root** | `0xdb9a6a1ef950c0a8a9b6f4b0b9ebbd80f0e1c23d7ecb1ebfd01c72f91e4a04a2` |
| **Beacon Seed** | `0x95e3d94fa051a1ce5a60e16eda758a658e411315da2f6cee7880e4baa5f08b88` |
| **Seed Message** | `Animica Mainnet Genesis 2026-02-08` |

### Testnet

| Property | Value |
|----------|-------|
| **Chain ID** | `2` |
| **Genesis Hash** | `0x6da84a9b99fddadfc4afeed9941e38fc53f24455a747091bcb0e4c67393c8627` |
| **Genesis Time** | `2026-02-08T02:50:03Z` |
| **Genesis Version** | `reset-2026-02-08` |
| **State Root** | `0xa8322f6bf020ae533a48d3df61909783805e823b1c9fd0b8945082566babb05c` |
| **Beacon Seed** | `0xc2d2614fb9d112e54f5e98b7bec8a1febc0c798d8a99b0c308bccb9d418540c4` |
| **Seed Message** | `Animica Testnet Genesis 2026-02-08` |

### Devnet

| Property | Value |
|----------|-------|
| **Chain ID** | `1337` |
| **Genesis Hash** | `0x09f94fca928ba59e2a43a0af11d684ee5145a35fced3377606e00cf35ec8db65` |
| **Genesis Time** | `2026-02-08T02:50:03Z` |
| **Genesis Version** | `reset-2026-02-08` |
| **State Root** | `0xa8322f6bf020ae533a48d3df61909783805e823b1c9fd0b8945082566babb05c` |
| **Beacon Seed** | `0x045405b11adba46b0049a4a98221de38c31c42fbd8d09c4ef6cdc3360e4e9859` |
| **Seed Message** | `Animica Devnet Genesis 2026-02-08` |

## What Changed

- **New genesis blocks**: All networks have new genesis blocks with different hashes
- **New genesis timestamp**: Updated from `2026-02-03T20:55:38Z` to `2026-02-08T02:50:03Z`
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
mv ~/.animica/data ~/.animica/data.backup-2026-02-03
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

To verify your node is using the correct genesis:

```bash
# Check genesis hash
animica-cli get-block 0 | grep hash

# Expected output for mainnet:
# "hash": "0x8ec4a0b923005e9039b815e526990359119e6f5492d5038aa898d6f8eee52adc"
```

## Files Modified

The following files were updated with new genesis hashes:

- `core/genesis/mainnet.json` - New mainnet genesis
- `core/genesis/testnet.json` - New testnet genesis
- `core/genesis/devnet.json` - New devnet genesis
- `core/genesis/genesis.json` - Copy of mainnet genesis
- `core/network_params.py` - Updated pinned genesis hashes
- `spec/chains.json` - Updated chain configurations
- `chains/animica.mainnet.json` - Updated mainnet metadata
- `chains/animica.testnet.json` - Updated testnet metadata
- `chains/animica.localnet.json` - Updated devnet metadata
- `p2p/checkpoints/builtin.py` - Updated mainnet checkpoint
- Tests updated to match new hashes

## Technical Details

### Genesis Hash Computation

The genesis hash is computed as:

```
genesis_hash = sha3_256(CBOR(genesis_header))
```

Where the genesis header includes:
- Chain ID
- Height (always 0 for genesis)
- Timestamp
- State root (derived from account allocations)
- Empty roots for txs, receipts, proofs, DA
- Beacon seed (sha3_256 of seed message)
- Policy roots
- Initial theta (acceptance threshold)
- Other consensus parameters

### Determinism

The genesis hash computation is fully deterministic:
- Same genesis JSON → same genesis hash
- CBOR encoding is canonical
- State root is deterministically computed from allocations
- No randomness or clock dependencies in computation

### Backward Compatibility

**This is a breaking change.** Nodes running the old genesis cannot sync with nodes running the new genesis. The P2P handshake includes genesis hash verification to prevent incompatible peers from connecting.

## Support

For issues or questions:
- GitHub Issues: https://github.com/animicaorg/all/issues
- Documentation: https://docs.animica.dev
