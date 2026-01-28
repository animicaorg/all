# Animica Mainnet Chain Reset - 2026-01-28b

## Overview

This document describes the mainnet chain reset performed on **2026-01-28**, which introduces a new genesis block and requires all nodes to reset their chain data and start syncing from block 0.

## What Changed

### Genesis Details

| Property | Value |
|----------|-------|
| **Chain ID** | `1` (unchanged) |
| **Genesis Hash** | `0xec3915d93db8586ea7a11e4deb98ca21317ee3772dd1e4a0fd78cb923aa07ca0` |
| **Genesis Time** | `2026-01-28T17:18:22Z` |
| **Genesis Version** | `reset-2026-01-28b` |
| **Beacon Seed** | `0x01e8e5daac8677a364752309a0595721a7079f3685fccbba2bb16293405a225c` |
| **State Root** | `0xdb9a6a1ef950c0a8a9b6f4b0b9ebbd80f0e1c23d7ecb1ebfd01c72f91e4a04a2` |

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
#   "hash": "0xec3915d93db8586ea7a11e4deb98ca21317ee3772dd1e4a0fd78cb923aa07ca0",
#   ...
# }
```

Or check the logs for the genesis hash on startup:

```bash
animica node logs | grep -i "genesis hash"
# Should show: genesis hash=0xec3915d93db8586ea7a11e4deb98ca21317ee3772dd1e4a0fd78cb923aa07ca0
```

## Verifier Nodes

Verifier nodes (seed nodes) may need special handling. See [docs/VERIFIER_NODE_RESTART.md](docs/VERIFIER_NODE_RESTART.md) for detailed instructions specific to verifier node operators.

### Quick Verifier Reset

```bash
# Use the update script to update genesis without full wipe
python scripts/update_genesis_hash.py --network mainnet

# Then restart
animica node down
animica node up
```

## Troubleshooting

### Genesis Mismatch Error

If you see an error like:

```
GenesisError: genesis does not match pinned network genesis
  expected: 0xec3915d93db8586ea7a11e4deb98ca21317ee3772dd1e4a0fd78cb923aa07ca0
  found: 0xfe6d08aa60f15bbfe0abedbe915437d004c12ebd8144ff6f388a7340d5a43e5a
```

**Solution**: Your node has the old genesis. Follow Option 1 or Option 2 above to reset.

### Peer Connection Issues

If your node can't connect to peers after the reset:

1. **Check genesis hash**: Ensure you're running the latest code with the new genesis
2. **Clear peer cache**: `rm ~/.animica/chain-1/peers.db`
3. **Verify P2P port**: Check that port 30333 is accessible
4. **Check seed nodes**: Ensure seed nodes are updated

### Node Won't Start

If the node fails to start:

1. **Check logs**: `animica node logs`
2. **Verify data directory**: Ensure old data is removed
3. **Check permissions**: Ensure write access to `~/.animica/`
4. **Try fresh install**: Remove and recreate the data directory

## FAQ

### Q: Will I lose my wallet?

**A**: No. Your wallet keys are stored separately in `~/.animica/wallets.json` and are not affected by the chain reset. However, any on-chain balances and transactions from the old chain are gone (except the premine allocation which is restored in the new genesis).

### Q: Do I need to update my software?

**A**: Yes. Make sure you pull the latest code from the repository that includes the new genesis files. Run:

```bash
git pull origin main
# If you have a venv:
source .venv/bin/activate
pip install -e ".[dev]"
```

### Q: Can I sync from the old genesis?

**A**: No. The old genesis is no longer valid. All nodes must use the new genesis hash `0xec3915d93db8586ea7a11e4deb98ca21317ee3772dd1e4a0fd78cb923aa07ca0`.

### Q: What happens to my mined blocks?

**A**: Any blocks from the old chain are no longer part of the canonical chain. The new chain starts fresh from block 0. Continue mining on the new chain to earn rewards.

### Q: How long does the reset take?

**A**: The reset itself is instant (just deleting old data). Syncing from genesis depends on network conditions and block height. With snapshots enabled, sync should complete in minutes to hours.

## Support

If you encounter issues not covered in this guide:

1. Check the [docs/CHAIN_RESET.md](docs/CHAIN_RESET.md) for additional information
2. Review [docs/VERIFIER_NODE_RESTART.md](docs/VERIFIER_NODE_RESTART.md) for verifier-specific guidance
3. Join the community channels for support
4. File an issue on GitHub if you believe you've found a bug

## Technical Details

### Files Updated

The following files were updated to reflect the new genesis:

- `core/genesis/mainnet.json` - Mainnet genesis definition
- `core/genesis/genesis.json` - Canonical genesis reference
- `core/network_params.py` - Network parameters and genesis hash
- `spec/chains.json` - Chain metadata
- `chains/animica.mainnet.json` - Mainnet chain config
- `p2p/checkpoints/builtin.py` - P2P checkpoint at genesis
- Documentation files (CHAIN_RESET.md, VERIFIER_NODE_RESTART.md, etc.)

### Genesis Computation

The genesis hash is deterministically computed from:

1. Genesis JSON contents (timestamp, allocations, consensus params)
2. State root derived from premine allocations
3. Header fields (stateRoot, timestamp, mixSeed, etc.)
4. SHA3-256 hash of the serialized header

This ensures that all nodes independently arrive at the same genesis hash when given the same genesis file.

### Premine Allocation

The premine allocation remains unchanged:

| Account | Amount (ANM) | Purpose |
|---------|--------------|---------|
| Treasury | 39,600,000 | Network development and operations |
| AICF | 20,250,000 | AI Capability Framework rewards |
| Foundation | 8,100,000 | Foundation operations |
| Faucet | 3,240,000 | Testnet and devnet faucet |
| Dev Reserve | 9,810,000 | Developer incentives |
| **Total** | **81,000,000** | Total premine |

All allocated to address: `anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz`

## Timeline

- **2026-01-28 17:18:22 UTC**: New genesis timestamp
- **2026-01-28**: Code updated with new genesis
- **Ongoing**: Nodes reset and sync from new genesis

## References

- [Genesis Specification](docs/spec/GENESIS.md)
- [Chain Reset Documentation](docs/CHAIN_RESET.md)
- [Verifier Node Guide](docs/VERIFIER_NODE_RESTART.md)
- [Network Parameters](core/network_params.py)
- [Genesis Loader](core/genesis/loader.py)
