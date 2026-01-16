# Chain Reset Implementation Summary

## Overview

Successfully implemented a chain reset with 5-minute (300 second) target block time for the Animica blockchain. The new network starts at height 0 with a new genesis block, and old chain data is incompatible.

## Key Changes

### 1. New Genesis (Chain Reset)

**New Genesis Hash:** `0x6a16a931365ca90fe6b5e115d94e26d025771c5d3be269f5105e1cd3de22b517`

**Old Genesis Hash:** `0x27fab3a17fd3a166908cdaa32462511ded2da86724314de45f335b0a59f820d8`

**Fork ID:** `0x4539f8c9` (derived from new genesis hash via CRC32)

**Genesis Timestamp:** `2026-01-16T00:00:00Z` (Unix: 1768521600)

### 2. Block Time Change

- **Old Target:** 120 seconds (2 minutes)
- **New Target:** 300 seconds (5 minutes)
- **Consensus Constant:** `TARGET_BLOCK_TIME_SEC = 300.0`

### 3. Files Modified/Created

#### Consensus Parameters
- `consensus/params.py` - NEW: Central consensus parameters module
- `consensus/difficulty.py` - Updated default target_block_time_s to 300.0
- `spec/params.yaml` - Updated mainnet target_block_interval_ms to 300000

#### Genesis Management
- `consensus/build_genesis.py` - NEW: Deterministic genesis builder script
- `consensus/genesis_output.json` - NEW: Genesis artifact with all hashes
- `core/genesis/mainnet.json` - Updated with new genesis hash and timestamp
- `core/network_params.py` - Updated MAINNET_GENESIS_HASH_HEX

#### Database & Boot
- `core/boot.py` - Added --force-reset-db flag and genesis validation

#### Testing
- `consensus/tests/test_genesis_builder.py` - NEW: Genesis determinism tests
- `consensus/tests/test_retarget_300s.py` - NEW: Difficulty retarget tests

#### Touchpoint Markers
Added `CHAIN_RESET_TOUCHPOINT` and `BLOCKTIME_TOUCHPOINT` comments to:
- `core/network_params.py`
- `core/genesis/loader.py`
- `core/types/header.py`
- `core/chain/identity.py`
- `consensus/difficulty.py`
- `consensus/validator.py`
- `consensus/fork_choice.py`
- `mining/templates.py`
- `p2p/protocol/hello.py`

## How It Works

### Chain Discrimination

The new chain is discriminated from the old chain through multiple mechanisms:

1. **Genesis Hash**: Different genesis block → different chain
2. **Fork ID**: Derived from genesis hash (CRC32) → `0x4539f8c9`
3. **P2P Handshake**: Peers exchange fork_id in HELLO message
4. **Database Validation**: DB stores genesis_hash and chain_id metadata

### Genesis Builder

The deterministic genesis builder (`consensus/build_genesis.py`) creates:

```bash
python consensus/build_genesis.py
```

Output includes:
- Genesis header CBOR bytes
- Genesis block CBOR bytes
- Genesis hash (SHA3-256 of header)
- Genesis state root (Merkle root of allocations)
- Fork ID (CRC32 of genesis hash)
- Consensus parameters snapshot

Verification:
```bash
python consensus/build_genesis.py --verify
```

### Database Reset

When starting a node with a database from the old chain:

```bash
# Without --force-reset-db (will fail with clear error)
python -m core.boot --genesis core/genesis/mainnet.json --db sqlite:///animica.db

# With --force-reset-db (destructive - wipes old data)
python -m core.boot --genesis core/genesis/mainnet.json --db sqlite:///animica.db --force-reset-db
```

The node will:
1. Check stored `db_genesis_hash` against expected genesis
2. If mismatch detected:
   - Without `--force-reset-db`: Exit with error and instructions
   - With `--force-reset-db`: Delete database and reinitialize

### P2P Peer Rejection

Old network peers are automatically rejected:

1. Peer sends HELLO message with `fid = 0x...` (old fork_id)
2. Local node compares against expected fork_id (`0x4539f8c9`)
3. Mismatch detected → `ProtocolError: fork_id mismatch`
4. Connection closed, peer marked incompatible

No special configuration needed - automatic via fork_id check.

## Verification Steps

### 1. Test Genesis Builder Determinism

```bash
cd /home/runner/work/all/all
python -m pytest consensus/tests/test_genesis_builder.py -v
```

Expected: All tests pass, confirming:
- Genesis hash is deterministic
- Matches committed constant in consensus.params
- Includes target_block_time_sec = 300
- Fork ID correctly derived

### 2. Test Difficulty Retargeting

```bash
python -m pytest consensus/tests/test_retarget_300s.py -v
```

Expected: All tests pass, confirming:
- Difficulty adjusts correctly with 300s target
- Theta increases when blocks arrive faster
- Theta decreases when blocks arrive slower
- Min/max bounds respected

### 3. Test Database Mismatch Detection

```bash
# Create test database with old genesis (simulation)
# Then try to boot with new genesis
python -m core.boot --genesis core/genesis/mainnet.json --db sqlite:///test_old.db
# Should fail with clear error message

# Try with --force-reset-db
python -m core.boot --genesis core/genesis/mainnet.json --db sqlite:///test_old.db --force-reset-db
# Should succeed after wiping database
```

### 4. Verify RPC Params

```bash
# Start node
python -m animica node up --profile mainnet

# Check parameters via RPC
curl http://localhost:8545 -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"animica_getChainParams","params":[],"id":1}'
```

Expected response includes:
```json
{
  "chain_id": 1,
  "genesis_hash": "0x6a16a931365ca90fe6b5e115d94e26d025771c5d3be269f5105e1cd3de22b517",
  "target_block_time_sec": 300,
  "fork_id": "0x4539f8c9"
}
```

## Integration Test Plan

### Two-Node Convergence Test

1. Start Node A (fresh):
   ```bash
   python -m animica node up --profile mainnet --data-dir /tmp/node-a
   ```

2. Start Node B (fresh):
   ```bash
   python -m animica node up --profile mainnet --data-dir /tmp/node-b --p2p-seeds /ip4/127.0.0.1/tcp/36200
   ```

3. Mine blocks on Node A:
   ```bash
   python -m animica mining mine-blocks --rpc http://localhost:8545 --count 10
   ```

4. Verify Node B syncs:
   ```bash
   curl http://localhost:8546 -X POST -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'
   ```

Expected: Both nodes report same block height (10) and same head hash.

### Old Genesis Peer Rejection Test

1. Create mock peer with old fork_id
2. Attempt P2P handshake
3. Expected: Connection rejected with `fork_id mismatch` error
4. Verify peer not added to peer list

## Migration Guide

### For Node Operators

**IMPORTANT: This is a destructive chain reset. All old blockchain data will be invalid.**

1. **Backup important data** (if needed for historical reference)

2. **Stop your node**:
   ```bash
   # Stop running node
   pkill -f "animica node"
   ```

3. **Update to new code**:
   ```bash
   git pull origin main
   pip install -e ".[dev]"
   ```

4. **Reset database**:
   ```bash
   # Option 1: Delete old database files
   rm -f ~/.animica/mainnet/animica.db
   
   # Option 2: Use --force-reset-db flag (automatic)
   python -m core.boot --genesis core/genesis/mainnet.json \
     --db sqlite:///$HOME/.animica/mainnet/animica.db \
     --force-reset-db
   ```

5. **Start node with new genesis**:
   ```bash
   python -m animica node up --profile mainnet
   ```

6. **Verify chain identity**:
   ```bash
   # Check genesis hash
   curl http://localhost:8545 -X POST -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"eth_getBlockByNumber","params":["0x0",false],"id":1}'
   
   # Should show hash: 0x6a16a931365ca90fe6b5e115d94e26d025771c5d3be269f5105e1cd3de22b517
   ```

### For Developers

Update any code that:
1. **Hardcodes genesis hash** - Use `consensus.params.GENESIS_HASH_HEX`
2. **Hardcodes block time** - Use `consensus.params.TARGET_BLOCK_TIME_SEC`
3. **Assumes 2-minute blocks** - Update for 5-minute intervals
4. **Connects to peers** - Ensure fork_id is validated

## Consensus Parameters Reference

```python
from consensus import params

# Chain identity
params.CHAIN_ID = 1
params.GENESIS_HASH_HEX = "0x6a16a931365ca90fe6b5e115d94e26d025771c5d3be269f5105e1cd3de22b517"

# Block time
params.TARGET_BLOCK_TIME_SEC = 300.0  # 5 minutes
params.TARGET_BLOCK_TIME_MS = 300000

# Difficulty retarget
params.RETARGET_HALF_LIFE_BLOCKS = 24.0
params.RETARGET_GAIN_BETA = 0.75
params.RETARGET_STEP_CLAMP_MICRO = 400_000
params.RETARGET_THETA_MIN_MICRO = 500_000

# Genesis
params.GENESIS_TIMESTAMP_UTC = "2026-01-16T00:00:00Z"
params.GENESIS_THETA_MICRO = 1_000_000  # 1.0 nats
params.GENESIS_PREMINE_TOTAL = 81_000_000_000_000_000  # 81M ANM

# Timestamp validation
params.TIMESTAMP_FUTURE_DRIFT_SEC = 60  # 1 minute
params.MTP_WINDOW_SIZE = 11
params.MIN_TIMESTAMP_INCREMENT_SEC = 1
```

## Known Limitations

1. **Bootstrap/Seed Nodes**: Must be updated externally to use new genesis
2. **Existing Wallets**: Remain compatible (same chain_id=1, different genesis)
3. **Historical Data**: Old chain data cannot be imported into new chain
4. **Cross-Chain**: No bridge or migration path between old and new chain

## Security Considerations

1. **Genesis Hash Verification**: Always verify genesis hash matches expected
2. **Fork ID Check**: P2P handshake automatically rejects incompatible peers
3. **Database Isolation**: Old and new chain data cannot coexist in same DB
4. **Deterministic Genesis**: Genesis builder is deterministic and reproducible

## Support & Troubleshooting

### Common Issues

**Issue**: "Genesis mismatch" error on startup
**Solution**: Use `--force-reset-db` to reset database, or delete old DB files

**Issue**: Node doesn't sync with peers
**Solution**: Verify both nodes have same genesis hash and fork_id

**Issue**: Mining produces invalid blocks
**Solution**: Ensure difficulty params are correctly initialized from genesis

### Debug Commands

```bash
# Check genesis hash
python consensus/build_genesis.py --verify

# Check database metadata
sqlite3 ~/.animica/mainnet/animica.db "SELECT hex(value) FROM kv WHERE key = x'1f67656e657369735f68617368';"

# Check chain identity
python -c "from core.chain.identity import ChainIdentity, derive_fork_id; \
  from consensus.params import GENESIS_HASH, CHAIN_ID; \
  print(f'Chain ID: {CHAIN_ID}'); \
  print(f'Genesis Hash: 0x{GENESIS_HASH.hex()}'); \
  print(f'Fork ID: 0x{derive_fork_id(GENESIS_HASH):08x}')"
```

## Next Steps

1. Deploy updated seed/bootstrap nodes with new genesis
2. Update public documentation with new chain parameters
3. Announce reset schedule to community
4. Monitor network convergence after reset
5. Validate mining and sync performance with 5-minute blocks

## References

- Genesis Builder: `consensus/build_genesis.py`
- Consensus Params: `consensus/params.py`
- Spec File: `spec/params.yaml`
- Core Boot: `core/boot.py`
- P2P Hello: `p2p/protocol/hello.py`
