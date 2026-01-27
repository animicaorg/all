# Chain Reset - January 27, 2026 (Second Reset)

## Overview

This document describes the second complete chain reset performed on January 27, 2026 at 22:56:57 UTC. All three networks (devnet, testnet, mainnet) now start from block 0 with new genesis hashes.

This reset supersedes the previous reset from earlier today (21:33:16 UTC) and provides a completely fresh blockchain starting point.

## Genesis Hash Changes

### Previous Genesis Hashes (Reset 1 - 21:33:16 UTC)
- **DEVNET (chainId=1337)**: `0xedad564668eebb7e29e7944091e626517f91987b0cfe99336b87f78f7043d16a`
- **TESTNET (chainId=2)**: `0x8a22f5a3c1ee518d43d269528c9a6316cdd29dfea30bf2ac95b744090307efe1`
- **MAINNET (chainId=1)**: `0x37bbadb1a63d719bf84021c8c12ff60d80bad8d55e788b7da76a46033a3e0e3b`

### New Genesis Hashes (Reset 2 - 22:56:57 UTC)
- **DEVNET (chainId=1337)**: `0x08590b2ec1e636d79103cf28a0c2413ab3978d1f75b2e19cbe77422fe9895799`
- **TESTNET (chainId=2)**: `0xef25935ac17f256fab92e2a93676a6a33f1c557fd654a30275047d6636471253`
- **MAINNET (chainId=1)**: `0xfc3004c4250a724bce0575cd9fc8e7282f75e64482dede19bf334035a4097c2f`

## What Changed

### 1. Genesis Timestamp
All networks updated to: `2026-01-27T22:56:57Z` (Unix: 1769554617)

### 2. Beacon Seed
All networks updated to: `0x3e2c0ecf8dc97b154d51816e643a099b485850f692d7020fae402cdc0c95126d`

This seed was deterministically derived from the reset timestamp using:
```python
seed = sha256(f"animica-genesis-reset-{timestamp}".encode())
```

### 3. Version Updates
- **Mainnet**: genesis version incremented from 3 to 4, tag `reset-2026-01-27b`
- **Testnet**: genesis version incremented from 1 to 2, tag `reset-2026-01-27b`
- **Devnet**: genesis version incremented from 1 to 2, tag `reset-2026-01-27b`

### 4. Premine Allocations
Remain unchanged from previous reset:
- **Devnet**: 581M ANM (includes 500M test account)
- **Testnet**: 581M ANM (includes 500M test account)
- **Mainnet**: 81M ANM (production allocation)

## Files Modified

1. `core/genesis/devnet.json` - Devnet genesis configuration
2. `core/genesis/testnet.json` - Testnet genesis configuration  
3. `core/genesis/mainnet.json` - Mainnet genesis configuration
4. `core/genesis/genesis.json` - Default genesis (mainnet copy)
5. `core/network_params.py` - Pinned genesis hashes for validation

## Transaction System Verification

The chain reset includes verification that transaction functionality works correctly:

✓ **Genesis Identity**: All genesis files load correctly and produce expected hashes  
✓ **Pinned Hashes**: Network parameters match computed genesis hashes  
✓ **Clean State**: Chain starts from block 0 with no previous transactions  
✓ **Transaction Ready**: System is ready to accept and persist transactions permanently

### Transaction Persistence

Transactions sent after this reset will:
1. Be validated against the new genesis chain ID
2. Be included in blocks when mined
3. Update account balances in StateDB
4. Be persisted permanently in the blockchain database
5. Remain accessible through RPC queries indefinitely

Database locations:
- **Mainnet**: `~/.animica/chain-1/animica.db`
- **Testnet**: `~/.animica/chain-2/animica.db`
- **Devnet**: `~/.animica/chain-1337/animica.db`

## How to Reset Your Node

### Option 1: Automatic Reset (Recommended)
The genesis loader will automatically detect the new genesis hash. Simply:

1. Pull the latest code:
   ```bash
   git pull origin main
   ```

2. Restart your node:
   ```bash
   animica node up --network devnet
   ```

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
   animica node up --network devnet
   ```

## Verification

### Verify Genesis Hashes

Run the verification test:
```bash
python3 test_chain_reset_tx.py
```

Expected output:
```
✓ ALL GENESIS FILES VERIFIED
✓ All pinned hashes match computed hashes
✓ Chain is reset to block 0 with new genesis
✓ Transaction system ready for operation
```

### Verify Genesis Identity Manually

```python
from core.genesis.loader import compute_genesis_identity

# Check mainnet
identity = compute_genesis_identity('core/genesis/mainnet.json')
print(f"Genesis Hash: 0x{identity.genesis_block_hash.hex()}")
# Expected: 0xfc3004c4250a724bce0575cd9fc8e7282f75e64482dede19bf334035a4097c2f

# Check testnet
identity = compute_genesis_identity('core/genesis/testnet.json')
print(f"Genesis Hash: 0x{identity.genesis_block_hash.hex()}")
# Expected: 0xef25935ac17f256fab92e2a93676a6a33f1c557fd654a30275047d6636471253

# Check devnet
identity = compute_genesis_identity('core/genesis/devnet.json')
print(f"Genesis Hash: 0x{identity.genesis_block_hash.hex()}")
# Expected: 0x08590b2ec1e636d79103cf28a0c2413ab3978d1f75b2e19cbe77422fe9895799
```

### Test Transaction Flow

1. **Start a devnet node**:
   ```bash
   animica node up --network devnet
   ```

2. **Create a test wallet** (if you don't have one):
   ```bash
   animica wallet create --label test-sender
   ```

3. **Send a test transaction**:
   ```bash
   animica tx send \
     --from test-sender \
     --to anim1... \
     --value 1.0 \
     --network devnet
   ```

4. **Verify transaction was included**:
   ```bash
   # Check transaction status
   animica tx status <tx_hash> --network devnet
   
   # Check balance updated
   animica wallet balance test-sender --network devnet
   ```

5. **Restart node and verify persistence**:
   ```bash
   # Stop and restart node
   animica node down --network devnet
   animica node up --network devnet
   
   # Verify transaction is still there
   animica tx status <tx_hash> --network devnet
   ```

## Testing

All genesis-related tests pass:
```bash
pytest core/genesis/tests/ -v
```

Test results:
- ✓ `test_genesis_identity.py::test_compute_genesis_identity_mainnet_is_stable` - PASSED
- ✓ `test_genesis_pins.py::test_pinned_genesis_hashes_match_files` - PASSED

## Impact

This is a **breaking change**. All nodes must update to continue syncing:

- **All existing blockchain data is invalidated**
- **All nodes start from block 0**
- **Previous transactions and state are lost**
- **Wallets with the old genesis will not connect to the new network**
- **All transactions sent after reset will persist permanently**

## Migration Path

There is no migration path from the old chain to the new chain. This is a complete reset:

1. Any contracts deployed on the old chain must be redeployed
2. Any state on the old chain is lost
3. Account balances reset to genesis allocations
4. **New transactions will be permanent** - the chain will not be reset again without notice

## Technical Details

### Genesis Hash Computation

The genesis hash is computed deterministically from:
1. **Genesis timestamp**: `2026-01-27T22:56:57Z`
2. **Beacon seed**: `0x3e2c0ecf8dc97b154d51816e643a099b485850f692d7020fae402cdc0c95126d`
3. **Initial state allocations** (premine accounts)
4. **Chain parameters** (from `spec/params.yaml`)
5. **Empty roots** for txs, receipts, proofs, DA

The genesis header is serialized as CBOR and hashed with SHA3-256 to produce the genesis block hash.

### Transaction Persistence Architecture

Transactions are persisted through multiple layers:

1. **Mempool** (in-memory): Initial receipt and validation
2. **Block Inclusion**: Selected by miners and included in blocks
3. **Block Database**: Blocks stored with CBOR encoding at KV prefix `0x11`
4. **State Database**: Account balances updated atomically
5. **Receipt Index**: Transaction receipts stored at KV prefix `0x22`
6. **Height Index**: Block height → hash mapping at KV prefix `0x12`

All writes use SQLite (default) or RocksDB with fsync to ensure durability.

## Questions?

For questions or issues with the chain reset:
- Check the logs for genesis mismatch errors
- Run `python3 test_chain_reset_tx.py` to verify your setup
- Verify your genesis file matches the expected hash
- Ensure you've pulled the latest code from the repository

## Determinism Guarantee

Changing any of these values results in a new genesis hash:
- Timestamp
- Beacon seed
- Premine allocations
- Chain ID
- Chain parameters

This ensures all nodes compute the exact same genesis hash and start on the same chain.
