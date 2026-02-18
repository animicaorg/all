# Animica Chain Reset - 2026-02-18

## Overview

This chain reset was performed on **2026-02-18**, introducing new genesis blocks for all networks (mainnet, testnet, and devnet) with version `reset-2026-02-18`. All nodes must reset their chain data and start syncing from block 0.

## Genesis Details

### Mainnet

| Property | Value |
|----------|-------|
| **Chain ID** | `1` |
| **Genesis Hash** | `0xd91fc1c90835f739ed8032e6c245da6ad88cd8608de9afb41078ca9aaf4b38ad` |
| **Genesis Time** | `2026-02-18T01:20:00Z` |
| **Genesis Version** | `reset-2026-02-18` |
| **State Root** | `0xdb9a6a1ef950c0a8a9b6f4b0b9ebbd80f0e1c23d7ecb1ebfd01c72f91e4a04a2` |
| **Beacon Seed** | `0x223bb7130695c010a08526e5ebb4a379bdfdf53454d24ff58de70c4f36f2524f` |
| **Seed Message** | `Animica Mainnet Genesis 2026-02-18` |

### Testnet

| Property | Value |
|----------|-------|
| **Chain ID** | `2` |
| **Genesis Hash** | `0x7656c5d4621dd2b6ab3bc736bb1c0a74630525f30188d1c675485195b0527a01` |
| **Genesis Time** | `2026-02-18T01:20:00Z` |
| **Genesis Version** | `reset-2026-02-18` |
| **State Root** | `0xa8322f6bf020ae533a48d3df61909783805e823b1c9fd0b8945082566babb05c` |
| **Beacon Seed** | `0x8985b73f569891f7a2113f83e6fb1db5b4c562465f3681feae907b9f635b6b86` |
| **Seed Message** | `Animica Testnet Genesis 2026-02-18` |

### Devnet

| Property | Value |
|----------|-------|
| **Chain ID** | `1337` |
| **Genesis Hash** | `0x85ecab1e5c324b90e3acda4ea66a4241c9746f080e8528f0594d346c1f89bb86` |
| **Genesis Time** | `2026-02-18T01:20:00Z` |
| **Genesis Version** | `reset-2026-02-18` |
| **State Root** | `0xa8322f6bf020ae533a48d3df61909783805e823b1c9fd0b8945082566babb05c` |
| **Beacon Seed** | `0x61caff9b5671f2dc0df1d57ed4ded1f3ed3fa738dd9cee9d0141445f9ece9fb3` |
| **Seed Message** | `Animica Devnet Genesis 2026-02-18` |

## What Changed

- **New genesis blocks**: All networks have new genesis blocks with different hashes
- **New genesis timestamp**: Updated from `2026-02-10T04:22:00Z` to `2026-02-18T01:20:00Z`
- **New seed messages**: Each network has a unique seed message for beacon randomness
- **New beacon seeds**: Deterministically derived from the new seed messages
- **Same allocations**: Account balances and premine remain unchanged from previous reset
- **Fixed bug**: Fixed `make_genesis.py` tool to properly handle bytes from `to_canonical_json`

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
mv ~/.animica/data ~/.animica/data.backup-2026-02-10
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
# Check genesis hash via RPC
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"chain_getBlockByHeight","params":[0],"id":1}' \
  | jq -r '.result.hash'
```

Expected values:
- Mainnet: `0xd91fc1c90835f739ed8032e6c245da6ad88cd8608de9afb41078ca9aaf4b38ad`
- Testnet: `0x7656c5d4621dd2b6ab3bc736bb1c0a74630525f30188d1c675485195b0527a01`
- Devnet: `0x85ecab1e5c324b90e3acda4ea66a4241c9746f080e8528f0594d346c1f89bb86`

## Troubleshooting

### Genesis Mismatch Error

If you see a genesis mismatch error, your node's database still contains the old genesis. Follow the reset instructions above.

### P2P Connection Issues

After the reset, your node may need time to discover and connect to peers running the new genesis. If you're not seeing connections after 5-10 minutes, check:

1. Your firewall rules allow P2P traffic on port 30303
2. Your node is advertising the correct external IP
3. Seed nodes are reachable

### Auto-Reset Option

To automatically handle genesis mismatches on startup:

```bash
# Enable auto-reset
animica node up --auto-reset-genesis-mismatch

# Or set environment variable
export ANIMICA_AUTO_RESET_GENESIS_MISMATCH=1
animica node up
```

## Technical Details

### Genesis Generation

The new genesis files were generated using:

```bash
PYTHONPATH=/home/runner/work/all/all python tools/genesis/make_genesis.py \
  --seed-message "Animica [Network] Genesis 2026-02-18" \
  --genesis-time "2026-02-18T01:20:00Z" \
  --chain-id [1|2|1337] \
  --theta-micro 1000000 \
  --gamma-cap-micro 2000000 \
  --network "animica-[network]" \
  --description "Animica [network] genesis (chainId=[id]) - Reset 2026-02-18" \
  --params-ref-path "spec/params.yaml" \
  --params-ref-hash "0x0000000000000000000000000000000000000000000000000000000000000000" \
  --genesis-version "reset-2026-02-18" \
  --alloc-file [allocations.json] \
  --output-genesis core/genesis/[network].json
```

### State Root Computation

The state root is computed deterministically from the allocation list using a Merkle tree of account leaf hashes:

```
leaf_hash = sha3_256("acct\x00" || CBOR({"addr": <address>, "balance": <int>}))
state_root = merkle_root(sorted(leaf_hashes))
```

### Beacon Seed Derivation

Beacon seeds are derived from the seed message:

```
beacon_seed = sha3_256(seed_message.encode("utf-8"))
```

## Related Documentation

- [Genesis Specification](docs/spec/GENESIS.md)
- [Chain Reset Guide](docs/CHAIN_RESET.md)
- [Node Architecture](docs/dev/NODE_ARCHITECTURE.md)

## Support

For issues or questions about the chain reset:

1. Check the troubleshooting section above
2. Review existing issues on GitHub
3. Join the community Discord for real-time help
4. Open a new issue with full details if the problem persists
