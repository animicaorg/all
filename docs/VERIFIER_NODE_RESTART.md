# Verifier Node Restart and Sync Recovery Guide

This guide helps verifier node operators handle node restarts, especially after the mainnet genesis reset (2026-01).

## Overview

Verifier nodes (144.126.133.21, 3.12.224.189) are trusted seed nodes that anchor the network's block height validation. They can now restart and resume syncing without manual intervention.

## Genesis Hash Update (Mainnet Reset 2026-01-28b)

The mainnet genesis hash changed in the 2026-01-28b reset:
- **New genesis hash**: `0xec3915d93db8586ea7a11e4deb98ca21317ee3772dd1e4a0fd78cb923aa07ca0`
- **Genesis timestamp**: `2026-01-28T17:18:22Z`
- **Chain ID**: Remains `1` (mainnet)

### Automatic Update (Recommended)

The easiest way to handle the genesis reset is to enable auto-reset when starting your node:

```bash
# For Docker deployments
animica node up --auto-reset-genesis-mismatch

# Or set environment variable
export ANIMICA_AUTO_RESET_GENESIS_MISMATCH=1
animica node up
```

This will automatically detect genesis mismatches and reset the chain data to sync from the new genesis.

### Manual Update (For Existing Nodes)

If you have an existing node with the old genesis and want to preserve some state:

#### Option 1: Use the Update Script

```bash
# From repository root
python scripts/update_genesis_hash.py --network mainnet

# Or specify paths explicitly (use absolute paths or $HOME, not tilde in --db-uri)
python scripts/update_genesis_hash.py \
  --db-uri sqlite:///$HOME/.animica/chain-1/animica.db \
  --genesis-path core/genesis/mainnet.json

# Better: use --data-dir which supports tilde expansion
python scripts/update_genesis_hash.py \
  --data-dir ~/.animica/chain-1 \
  --genesis-path core/genesis/mainnet.json

# Dry-run to check what would change
python scripts/update_genesis_hash.py --network mainnet --dry-run
```

#### Option 2: Reset Node Data

```bash
# Stop the node
animica node down

# Reset all data
animica node reset --network mainnet --yes

# Start with auto-reset enabled
animica node up --auto-reset-genesis-mismatch
```

## Restart Behavior

### Normal Restarts

For nodes already synced with the correct genesis:

1. **Stop the node**:
   ```bash
   animica node down
   ```

2. **Start the node**:
   ```bash
   animica node up
   ```

The node will:
- Load its persisted state from the database
- Resume syncing from the last known height
- Connect to peers and continue validation

### After Genesis Mismatch

If the node detects a genesis mismatch on startup:

1. **Without auto-reset**: Node will refuse to start and show an error:
   ```
   GENESIS_MISMATCH expected=0xe020040d... got=0x<old_hash>
   ```

2. **With auto-reset enabled**: Node will automatically:
   - Detect the mismatch
   - Wipe the chain data
   - Re-initialize with the new genesis
   - Start syncing from height 0

## Verifier Node State Persistence

Verifier nodes persist the following state across restarts:

- **Blockchain data**: Headers, blocks, receipts
- **Genesis hash**: The canonical genesis block hash
- **Chain ID**: Network identifier (1 for mainnet)
- **Sync state**: Last synced height and block hash
- **Peer addresses**: Known peer list for faster reconnection

## Configuration for Verifier Nodes

### Environment Variables

```bash
# Enable auto-reset for genesis mismatches (recommended for upgrades)
export ANIMICA_AUTO_RESET_GENESIS_MISMATCH=1

# P2P configuration (defaults for verifiers)
export ANIMICA_P2P_ENABLE_VERIFIER_SEEDS=true
export ANIMICA_P2P_VERIFIER_SEED_IPS="144.126.133.21,3.12.224.189"

# Network selection
export ANIMICA_NETWORK=mainnet
```

### Docker Compose

For Docker deployments, add to your `docker-compose.yml` or `.env`:

```yaml
environment:
  - ANIMICA_AUTO_RESET_GENESIS_MISMATCH=1
  - ANIMICA_NETWORK=mainnet
  - ANIMICA_P2P_ENABLE_VERIFIER_SEEDS=true
```

## Troubleshooting

### Node won't start after restart

1. Check for genesis mismatch in logs:
   ```bash
   docker logs animica-node 2>&1 | grep GENESIS_MISMATCH
   ```

2. If mismatch detected, use auto-reset:
   ```bash
   animica node down
   animica node up --auto-reset-genesis-mismatch
   ```

### Node stuck at old height

1. Check peer connections:
   ```bash
   animica rpc call p2p.listPeers
   ```

2. Verify verifier seeds are configured:
   ```bash
   animica rpc call p2p.getVerifierSeedStatus
   ```

3. Force sync from genesis:
   ```bash
   animica node down
   animica node reset --yes
   animica node up
   ```

### Database corruption

If you suspect database corruption:

```bash
# Backup first
cp -r ~/.animica/chain-1 ~/.animica/chain-1.backup

# Reset and resync
animica node reset --yes
animica node up
```

## Monitoring Verifier Nodes

### Check sync status

```bash
# RPC call
animica rpc call chain.getHead

# Or use sync status
animica rpc call sync.getStatus
```

### Verify genesis hash

```bash
# Check via RPC
animica rpc call chain.getHead | jq '.result.height'

# Or inspect database directly (note: tilde must be expanded)
python -c "
from pathlib import Path
from core.db.block_db import BlockDB
from core.db.sqlite import SQLiteKV
db_path = Path('~/.animica/chain-1/animica.db').expanduser()
kv = SQLiteKV(str(db_path))
db = BlockDB(kv)
h = db.get_genesis_hash()
print(f'Genesis: 0x{h.hex()}' if h else 'Not set')
kv.close()
"
```

### Monitor height validation

```bash
# Check verifier seed status
animica rpc call p2p.getVerifierSeedStatus

# Should show:
# - enabled: true
# - connected_verifiers: [list of connected verifier IPs]
# - max_verifier_height: <current max height from verifiers>
# - can_mine: true (if local height <= max_verifier_height + 1)
```

## Best Practices

1. **Always enable auto-reset during upgrades**: Set `ANIMICA_AUTO_RESET_GENESIS_MISMATCH=1` when deploying new versions

2. **Monitor genesis hash**: Periodically verify your node's genesis matches the network

3. **Backup before major changes**: Keep backups of `~/.animica/chain-<id>/` before resetting

4. **Use the update script for live nodes**: If you can't afford downtime, use `update_genesis_hash.py` instead of a full reset

5. **Test in devnet first**: For critical verifier nodes, test upgrade procedures in devnet/testnet first

## Support

For issues with verifier nodes:
1. Check logs: `docker logs animica-node` or `~/.animica/logs/`
2. Review this guide and CHAIN_RESET.md
3. Join the Animica Discord or open a GitHub issue
4. Tag @animica/core-team for verifier-specific issues

## See Also

- [CHAIN_RESET.md](./CHAIN_RESET.md) - Details on the mainnet genesis reset
- [scripts/update_genesis_hash.py](../scripts/update_genesis_hash.py) - Genesis hash update utility
- [p2p/tests/test_verifier_seed_*.py](../p2p/tests/) - Verifier node test cases
