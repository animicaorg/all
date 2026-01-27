# Chain Reset - January 27, 2026

## Overview

This document describes the full chain reset performed on January 27, 2026. All three networks (devnet, testnet, mainnet) now start from block 0 with new genesis hashes.

## Genesis Hash Changes

### Previous Genesis Hashes
- **DEVNET (chainId=1337)**: `0x4eeb4a9127e06215adffbd75acc6715cdccddf12c7cc937ab1d0a1ccecfddfaf`
- **TESTNET (chainId=2)**: `0xcf4489041eb0ae6a4e29a7e9684392eee2b74d2e9ad4bc8c38b82b260a615b34`
- **MAINNET (chainId=1)**: `0xe020040d488c83dd86a1613c5a8017cf60e7ed725952426cef39ab584ac43fab`

### New Genesis Hashes
- **DEVNET (chainId=1337)**: `0xedad564668eebb7e29e7944091e626517f91987b0cfe99336b87f78f7043d16a`
- **TESTNET (chainId=2)**: `0x8a22f5a3c1ee518d43d269528c9a6316cdd29dfea30bf2ac95b744090307efe1`
- **MAINNET (chainId=1)**: `0x37bbadb1a63d719bf84021c8c12ff60d80bad8d55e788b7da76a46033a3e0e3b`

## What Changed

### 1. Genesis Timestamp
All networks updated to: `2026-01-27T21:33:16Z`

### 2. Beacon Seed
All networks updated to: `0x7b6bca1c845aa9b493bc1d72047c4fd0b065ef1c04d10d9cd7302671b957e3ea`

This seed was deterministically derived from the reset timestamp to ensure reproducibility.

### 3. Version Updates
- Mainnet genesis version incremented from 2 to 3
- Genesis version tag updated to `reset-2026-01-27`

### 4. Premine Corrections
- **Devnet**: Updated premineTotal from 81M ANM to 581M ANM to match actual allocations
- **Testnet**: Updated premineTotal from 81M ANM to 581M ANM to match actual allocations

## Files Modified

1. `core/genesis/devnet.json` - Devnet genesis configuration
2. `core/genesis/testnet.json` - Testnet genesis configuration  
3. `core/genesis/mainnet.json` - Mainnet genesis configuration
4. `core/genesis/genesis.json` - Default genesis (mainnet)
5. `core/network_params.py` - Pinned genesis hashes for validation

## How to Reset Your Node

### Option 1: Automatic Reset (Recommended)
The genesis loader will automatically detect the new genesis hash. Simply:

1. Pull the latest code
2. Restart your node

The node will detect the genesis mismatch and automatically reset if `ANIMICA_AUTO_RESET_GENESIS_MISMATCH=1` is set.

### Option 2: Manual Reset

1. **Stop your node**
   ```bash
   # Stop the node process
   pkill -f animica-node
   ```

2. **Clear blockchain data**
   ```bash
   # For mainnet
   rm -rf ~/.animica/chain-1/animica.db
   
   # For testnet
   rm -rf ~/.animica/chain-2/animica.db
   
   # For devnet
   rm -rf ~/.animica/chain-1337/animica.db
   ```

3. **Pull latest code**
   ```bash
   git pull origin main
   ```

4. **Restart your node**
   ```bash
   # The node will initialize with the new genesis
   python -m animica.node --network mainnet
   ```

### Option 3: Update Genesis Hash Only (Advanced)

If you want to preserve some state but update the genesis hash:

```bash
python scripts/update_genesis_hash.py --network mainnet
```

**Note**: This is only recommended for verifier nodes that need to maintain sync state.

## Verification

To verify the new genesis hash is loaded correctly:

```python
from core.genesis.loader import compute_genesis_identity

# Check mainnet
identity = compute_genesis_identity('core/genesis/mainnet.json')
print(f"Genesis Hash: 0x{identity.genesis_block_hash.hex()}")
# Expected: 0x37bbadb1a63d719bf84021c8c12ff60d80bad8d55e788b7da76a46033a3e0e3b
```

## Testing

All genesis-related tests pass:
```bash
pytest core/genesis/tests/ -v
```

## Impact

This is a **breaking change**. All nodes must update to continue syncing:

- **All existing blockchain data is invalidated**
- **All nodes start from block 0**
- **Previous transactions and state are lost**
- **Wallets with the old genesis will not connect to the new network**

## Migration Path

There is no migration path from the old chain to the new chain. This is a complete reset:

1. Any contracts deployed on the old chain must be redeployed
2. Any state on the old chain is lost
3. Account balances reset to genesis allocations

## Questions?

For questions or issues with the chain reset:
- Check the logs for genesis mismatch errors
- Use `python scripts/update_genesis_hash.py --help` for update options
- Verify your genesis file matches the expected hash

## Technical Details

The genesis hash is computed deterministically from:
1. Genesis timestamp
2. Beacon seed  
3. Initial state allocations (premine)
4. Chain parameters

Changing any of these values results in a new genesis hash, effectively creating a new blockchain.
