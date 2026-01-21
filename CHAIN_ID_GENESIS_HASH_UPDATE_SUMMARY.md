# Chain ID and Genesis Hash Update Summary

## Overview

This update changes the Animica mainnet chain ID from 0 to 1 and generates a new genesis hash, ensuring consistency across the entire codebase.

## Changes Made

### 1. Genesis Files Updated

#### core/genesis/genesis.json
- **chainId**: Changed from 1 → remains 1 ✓
- **genesis_hash**: Updated to `0x753d4c91c89cab828fe1d52e55553b0de74863ce5dfdfe0f81eb6196e88728c2`
- **fork_id**: Updated to `0x753d4c91`
- **genesisTime**: Updated to `2026-01-21T00:00:00Z`
- **genesis_version**: Updated to `reset-2026-01-21`

#### core/genesis/mainnet.json
- **chainId**: Changed from 0 → 1 ✓
- **genesis_hash**: Updated to `0x753d4c91c89cab828fe1d52e55553b0de74863ce5dfdfe0f81eb6196e88728c2`
- **fork_id**: Updated to `0x753d4c91`
- **genesisTime**: Updated to `2026-01-21T00:00:00Z`
- **genesis_version**: Updated to `reset-2026-01-21`

### 2. Network Configuration Files

#### spec/chains.json
- **chainId**: Changed from `animica:0` → `animica:1`
- **genesis.hash**: Updated to match new genesis hash

#### core/genesis/spec/params.yaml
- **chain.id**: Already set to 1 ✓
- **genesis.hash**: Updated to new hash
- **genesis.time**: Updated to `2026-01-21T00:00:00Z`

### 3. Core Network Identity Modules

#### core/network_params.py
- **MAINNET_PARAMS.chain_id**: Changed from 0 → 1
- **MAINNET_GENESIS_HASH_HEX**: Updated to new hash
- **PINNED_GENESIS_BY_NETWORK**: Key changed from `("mainnet", 0)` → `("mainnet", 1)`
- **GENESIS_PATH_BY_NETWORK**: Key changed from `("mainnet", 0)` → `("mainnet", 1)`

#### core/network_manifest.py
- **MAINNET_MANIFEST.chain_id**: Changed from 0 → 1
- **MAINNET_MANIFEST.pinned_genesis_hash**: Updated to new hash
- **_MANIFESTS_BY_CHAIN_ID**: Key changed from 0 → 1
- Updated comments and documentation

#### core/network_identity.py
- **NETWORK_CHAIN_ID_MAP["mainnet"]**: Changed from 0 → 1
- Updated docstring examples to use chain_id=1

### 4. Code References Updated

#### core/genesis/loader.py
- Mainnet premine validation: Changed `if chain_id == 0:` → `if chain_id == 1:`
- Updated comment from "chain_id == 0" to "chain_id == 1"

#### p2p/checkpoints/builtin.py
- **get_builtin_checkpoints()**: Changed `if chain_id == 0:` → `if chain_id == 1:`
- **get_all_builtin_checkpoints()**: Changed result key from 0 → 1
- Updated docstring from "(0=mainnet...)" to "(1=mainnet...)"

#### core/bootstrap.py
- Bootstrap password gate: Changed `is_mainnet = chain_id == 0` → `is_mainnet = chain_id == 1`
- Updated comments from "chain_id == 0" to "chain_id == 1"

#### core/snapshot/policy.py
- Network detection: Changed `if chain_id == 0:` → `if chain_id == 1:`

### 5. Docker and Deployment Configuration

#### ops/docker/docker-compose.mainnet.yml
- **ANIMICA_CHAIN_ID**: Default changed from 0 → 1
- **ANIMICA_DATA_DIR**: Changed from `/data/chain-0` → `/data/chain-1`
- **ANIMICA_P2P_CHAIN_ID**: Default changed from 0 → 1
- **CHAIN_ID** (services): Default changed from 0 → 1
- **VITE_CHAIN_ID** (explorer): Default changed from 0 → 1

## New Genesis Hash Details

**Genesis Hash**: `0x753d4c91c89cab828fe1d52e55553b0de74863ce5dfdfe0f81eb6196e88728c2`

**Fork ID**: `0x753d4c91` (derived from first 4 bytes of genesis hash)

**Generation Method**: Deterministic hash based on:
- chainId: 1
- network: "animica-mainnet"
- genesisTime: "2026-01-21T00:00:00Z"
- reset_version: "reset-2026-01-21"

## Syncing Robustness

The syncing mechanism has been reviewed and confirmed to be robust:

### Key Syncing Features
1. **Genesis Handling**: Proper genesis block validation at height 0
2. **Error Recovery**: Exponential backoff with retry mechanisms
3. **Continuous Sync**: Watchdog-based recovery for stuck syncs
4. **Genesis Hash Validation**: Enforced during P2P handshake
5. **Fallback Logic**: Multiple recovery paths for sync stalls

### Syncing Architecture
- **Headers Sync** (`p2p/sync/headers.py`): Batch-based header synchronization with genesis locator
- **Blocks Sync** (`p2p/sync/blocks.py`): Parallel block fetching with timeout and retry
- **Snapshot Sync** (`p2p/sync/snapshot_sync.py`): Continuous discovery and fallback sync
- **Recovery**: Automatic peer rotation and backoff management

## Validation Results

✅ **All validations passed**:
- Mainnet chain_id correctly set to 1 across all modules
- Genesis hash consistent in all configuration files
- Network lookups working correctly (chain_id ↔ network_name)
- Docker configurations updated with correct defaults

## Breaking Changes

### For Node Operators
- **Data directory change**: Mainnet data will now be stored in `chain-1/` instead of `chain-0/`
- **Chain reset**: This is a full chain reset. All existing mainnet data is incompatible.
- **P2P network**: Nodes must use the new genesis hash to connect to peers

### For Developers
- All references to `chain_id == 0` for mainnet detection must be changed to `chain_id == 1`
- Genesis hash has changed; any hardcoded references need updating
- Network identity lookups now use chain_id=1 for mainnet

### For Users
- **Wallets**: Must be reconfigured to use chain_id=1
- **Transactions**: All transactions must be signed with chain_id=1
- **RPC endpoints**: Will return chain_id=1 for mainnet

## Migration Guide

### 1. Stop Existing Mainnet Nodes
```bash
docker-compose -f ops/docker/docker-compose.mainnet.yml down
```

### 2. Clean Old Data (Optional)
```bash
# Backup if needed
mv /data/chain-0 /data/chain-0.backup

# Or remove
rm -rf /data/chain-0
```

### 3. Update Configuration
```bash
# Pull latest changes
git pull origin main

# Verify new genesis
cat core/genesis/mainnet.json | grep chainId
# Should show: "chainId": 1
```

### 4. Start New Mainnet Node
```bash
docker-compose -f ops/docker/docker-compose.mainnet.yml up -d
```

### 5. Verify
```bash
# Check chain ID
curl -X POST http://localhost:8545 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"chain.getChainId","params":[],"id":1}'
# Should return: {"jsonrpc":"2.0","result":1,"id":1}

# Check genesis hash
curl -X POST http://localhost:8545 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"net.getGenesisHash","params":[],"id":1}'
# Should return new genesis hash
```

## Files Changed

### Genesis and Configuration
- `core/genesis/genesis.json`
- `core/genesis/mainnet.json`
- `core/genesis/spec/params.yaml`
- `spec/chains.json`

### Core Network Modules
- `core/network_params.py`
- `core/network_manifest.py`
- `core/network_identity.py`

### Core Logic
- `core/genesis/loader.py`
- `core/bootstrap.py`
- `core/snapshot/policy.py`

### P2P and Sync
- `p2p/checkpoints/builtin.py`

### Deployment
- `ops/docker/docker-compose.mainnet.yml`

## Testing

### Manual Validation
```bash
python3 -c "
from core.network_params import MAINNET_PARAMS
from core.network_manifest import MAINNET_MANIFEST
from core.network_identity import NETWORK_CHAIN_ID_MAP

assert MAINNET_PARAMS.chain_id == 1
assert MAINNET_MANIFEST.chain_id == 1
assert NETWORK_CHAIN_ID_MAP['mainnet'] == 1
print('✅ All checks passed')
"
```

### Expected Output
```
✅ All checks passed
```

## References

- **Previous Genesis Hash**: `0x6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242`
- **New Genesis Hash**: `0x753d4c91c89cab828fe1d52e55553b0de74863ce5dfdfe0f81eb6196e88728c2`
- **Previous Chain ID**: 0
- **New Chain ID**: 1
- **Reset Date**: 2026-01-21

## Notes

- This is a **breaking change** requiring a full chain reset
- All existing mainnet nodes must be restarted with new configuration
- Syncing has been verified to be robust with proper error handling
- Genesis hash validation is enforced at P2P handshake level
- Continuous sync mechanisms ensure nodes stay synchronized
