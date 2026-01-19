# Network Identity & Genesis Fix - Implementation Guide

## Overview
This PR addresses genesis mismatch, network identity confusion, and related sync/balance issues by creating a single source of truth for network identity and enforcing it consistently across all components.

## What Was Fixed

### 1. Network Manifest Module (core/network_manifest.py) ✅
Created canonical network definitions to eliminate hardcoded values across codebase.

**Key Features:**
- NetworkManifest dataclass with chain_id, genesis_path, pinned_genesis_hash
- get_manifest(network=..., chain_id=...) for unified lookup
- verify_genesis() for preflight validation
- network_identity_string for consistent logging
- p2p_network_id for handshake compatibility

**Usage:**
```python
from core.network_manifest import get_manifest, verify_genesis

# Get manifest
manifest = get_manifest(network="mainnet")
# or
manifest = get_manifest(chain_id=0)

# Verify genesis file matches pinned hash
verify_genesis(manifest)  # Raises GenesisError on mismatch

# Use in P2P handshake
network_id = manifest.p2p_network_id  # "animica:0"
genesis_hash = manifest.pinned_genesis_hash_hex
```

### 2. DB Metadata Storage (core/db/block_db.py) ✅
Added network identity to database metadata to prevent cross-network contamination.

**New Methods:**
- `set_network_name(network_name: str)` - Store network identifier
- `get_network_name() -> str` - Retrieve network identifier

**Metadata Keys:**
- META_NETWORK_NAME - Network name (mainnet, testnet, devnet)
- META_CHAIN_ID - Chain ID (already existed)
- META_GENESIS - Genesis block hash (already existed)

### 3. Genesis Init Updates (core/genesis/loader.py) ✅
Stores network metadata when initializing genesis from file.

**Changes:**
- In `load_and_init_genesis()`, added call to `blocks.set_network_name()`
- Network name sourced from genesis JSON or ANIMICA_NETWORK env var
- Ensures DB always has network context for future verification

### 4. RPC Startup Verification (rpc/deps.py) ✅
Added strict validation before RPC server accepts connections.

**Changes in _maybe_bootstrap_genesis():**
1. Check DB metadata (chain_id, genesis_hash, network_name)
2. Verify DB chain_id matches config chain_id
3. Verify DB genesis_hash matches genesis file
4. Raise GenesisMismatchError with remediation steps if mismatch
5. Only bootstrap if DB is empty (no head)

**Error Messages:**
```
Database chain_id mismatch: DB has chain_id=1337, but config expects chain_id=0.
Your database belongs to a different network.
Delete or reset the data directory.
```

```
Database genesis mismatch: DB has genesis=0xaaaa..., but genesis file computes to=0xbbbb...
Your database was initialized with a different genesis.
Delete or reset the data directory.
```

### 5. CLI Commands (python/animica/cli/chain.py) ✅
Added genesis verification and info commands for operational diagnostics.

**Commands:**
```bash
# Verify genesis file matches pinned hash
animica chain genesis verify --network mainnet
# Output: ✓ Genesis verification PASSED

# Show all network info
animica chain genesis info --all
# Output: Network details for mainnet, testnet, devnet

# Verify specific network
animica chain genesis verify --network testnet
ANIMICA_NETWORK=mainnet animica chain genesis verify
```

### 6. Tests (tests/test_genesis_verification.py) ✅
Comprehensive test coverage for network identity enforcement.

**Test Coverage:**
- Manifest retrieval by network name and chain_id
- Genesis verification for each network
- Network identity string formatting
- DB metadata storage and retrieval
- Chain ID mismatch detection and error handling
- Genesis hash mismatch detection and error handling

**Run Tests:**
```bash
pytest tests/test_genesis_verification.py -v
```

## What Still Needs to Be Done

### 1. P2P Handshake Updates (p2p/)
**Files:** p2p/peer/identify.py, p2p/node/service.py

**Current State:**
- IdentifyRequest/Response already have `network_id` and `head_hash` fields
- network_id currently set to `alg_policy_root` (incorrect)

**Required Changes:**
```python
# In p2p/node/service.py (where IdentifyService is created):
from core.network_manifest import get_manifest_for_env

manifest = get_manifest_for_env()
if manifest:
    identify_service = IdentifyService(
        connmgr=...,
        peer_id=...,
        network_id=manifest.p2p_network_id,  # "animica:0"
        genesis_hash=manifest.pinned_genesis_hash_hex,  # "0x6a27..."
        ...
    )
```

**Add Validation:**
```python
# In handshake acceptance logic:
def validate_handshake(local_manifest, peer_response):
    peer_network_id = peer_response.get("network_id")
    peer_genesis = peer_response.get("genesis_hash")
    
    if peer_network_id != local_manifest.p2p_network_id:
        raise HandshakeError(
            f"Network mismatch: local={local_manifest.p2p_network_id}, "
            f"peer={peer_network_id}"
        )
    
    if peer_genesis and peer_genesis != local_manifest.pinned_genesis_hash_hex:
        raise HandshakeError(
            f"Genesis mismatch: local={local_manifest.pinned_genesis_hash_hex}, "
            f"peer={peer_genesis}"
        )
    
    return True
```

### 2. Sync Kickoff and Tip Tracking (p2p/sync/)
**Files:** p2p/sync/service.py, p2p/sync/headers.py

**Issues:**
- Sync not triggering immediately after peer handshake
- Tip freshness based on poll timestamp instead of receive timestamp
- Missing INFO-level logs for debugging

**Required Changes:**
1. **Immediate sync kickoff:**
```python
async def on_peer_connected(self, peer_id):
    """Called after successful handshake."""
    logger.info(f"Peer connected: {peer_id}")
    
    # Immediately request tip
    tip = await self.request_tip(peer_id)
    if tip:
        logger.info(f"Peer tip: height={tip.height} hash={tip.hash}")
        self.update_best_remote_height(tip.height)
        
        # Kickoff sync if we're behind
        if tip.height > self.local_height:
            await self.start_sync_from_peer(peer_id)
```

2. **Fix tip freshness:**
```python
class PeerState:
    last_tip_received_at: float  # Unix timestamp
    last_tip_height: int
    
def is_tip_fresh(peer_state, max_age_seconds=60):
    if peer_state.last_tip_received_at == 0:
        return False
    return (time.time() - peer_state.last_tip_received_at) < max_age_seconds
```

3. **Add INFO logs:**
```python
logger.info(f"Headers accepted: count={len(headers)} from={peer_id}")
logger.info(f"Blocks requested: heights={height_range} from={peer_id}")
logger.info(f"Sync progress: local={local_height} target={target_height}")
```

### 3. Mining Balance Updates (mining/, execution/)
**Files:** mining/mining.py, execution/apply.py

**Issues:**
- Balance not updating after mining
- Coinbase might not be committed properly
- Wallet show might be reading stale data

**Required Changes:**
1. **Ensure miner uses canonical apply path:**
```python
# In mining/mining.py:
async def mine_block(self, template):
    # Create block from template
    block = self.build_block(template)
    
    # MUST apply through same path as network blocks
    receipt = await self.executor.apply_block(block)
    
    # MUST commit before logging "credited"
    await self.block_db.commit()
    
    logger.info(f"Mining reward credited: {reward_anm} ANM")
```

2. **Fix wallet balance query:**
```python
# In wallet show:
async def show_balance(address):
    # Query RPC for latest state, not direct DB
    rpc_url = get_rpc_url()
    head = await rpc_call("chain.getHead", rpc_url)
    balance = await rpc_call("state.getBalance", [address], rpc_url)
    
    logger.info(f"Balance at height {head.height}: {balance}")
    return balance
```

3. **Add test:**
```python
def test_mining_balance_increment():
    # Get premine balance
    premine_addr = "anim1zqqjt3258..."
    initial = get_balance(premine_addr)
    assert initial == 81_000_000_000_000_000  # 81M ANM in base units
    
    # Mine one block
    mine_block(coinbase_addr=premine_addr)
    
    # Verify balance increased
    after_mining = get_balance(premine_addr)
    expected = initial + 300_000_000_000  # +300 ANM
    assert after_mining == expected
    
    # Restart node and verify balance persists
    restart_node()
    final = get_balance(premine_addr)
    assert final == expected
```

### 4. Docker Entrypoint Verification
**File:** ops/docker/node.Dockerfile or docker-entrypoint.sh

**Add verification before starting RPC:**
```bash
#!/bin/bash
set -euo pipefail

# Verify network identity before starting
echo "=== Network Identity Verification ==="
python -c "
from core.network_manifest import get_manifest_for_env, verify_genesis
import sys

manifest = get_manifest_for_env()
if not manifest:
    print('ERROR: No network specified')
    sys.exit(1)

print(f'Network: {manifest.network_name}')
print(f'Chain ID: {manifest.chain_id}')
print(f'Genesis: {manifest.pinned_genesis_hash_hex}')
print(f'Identity: {manifest.network_identity_string}')

# Verify genesis file
try:
    verify_genesis(manifest)
    print('✓ Genesis verification PASSED')
except Exception as e:
    print(f'✗ Genesis verification FAILED: {e}')
    sys.exit(1)
"

# Start RPC server
echo "=== Starting RPC Server ==="
exec python -m rpc
```

## Testing Guide

### Manual Verification

1. **Test genesis verification CLI:**
```bash
cd /home/runner/work/all/all

# Should pass for mainnet
ANIMICA_NETWORK=mainnet python -m animica.cli.chain genesis verify

# Should pass for testnet
ANIMICA_NETWORK=testnet python -m animica.cli.chain genesis verify

# Show all network info
python -m animica.cli.chain genesis info --all
```

2. **Test DB metadata enforcement:**
```bash
# Initialize DB with mainnet
ANIMICA_NETWORK=mainnet ANIMICA_CHAIN_ID=0 python -m rpc &
sleep 5
pkill -f "python -m rpc"

# Try to start with testnet (should fail)
ANIMICA_NETWORK=testnet ANIMICA_CHAIN_ID=2 python -m rpc
# Expected: GenesisError with chain_id mismatch
```

3. **Test docker startup:**
```bash
cd ops/docker

# Build image
docker compose -f docker-compose.mainnet.yml build

# Start node (should print network identity)
docker compose -f docker-compose.mainnet.yml up -d

# Check logs for verification
docker compose -f docker-compose.mainnet.yml logs node | grep -E "Network|Genesis|Identity"

# Should see:
# Network: mainnet
# Chain ID: 0
# Genesis: 0x6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242
# ✓ Genesis verification PASSED
```

### Automated Tests

```bash
# Run genesis verification tests
cd /home/runner/work/all/all
pytest tests/test_genesis_verification.py -v

# Expected output:
# test_get_manifest_by_network PASSED
# test_get_manifest_by_chain_id PASSED
# test_verify_genesis_mainnet PASSED
# test_verify_genesis_testnet PASSED
# test_network_identity_string PASSED
# test_db_metadata_storage PASSED
# test_db_metadata_chain_id_mismatch PASSED
# test_db_metadata_genesis_hash_mismatch PASSED
```

## Migration Guide

### For Existing Nodes

If you have an existing node that was initialized with the old genesis:

1. **Back up important data** (if any)
2. **Check your data directory:**
```bash
# Find your data directory
animica node status | grep "Data"
# or
echo $ANIMICA_DATA_DIR
```

3. **Reset the chain data:**
```bash
# CLI method
animica node reset --force

# Manual method
rm -rf ~/.animica/chain-0
# or for docker
docker compose down -v
```

4. **Start node fresh:**
```bash
# CLI method
animica node up

# Docker method
docker compose up -d
```

5. **Verify genesis:**
```bash
animica chain genesis verify --network mainnet
# Expected: ✓ Genesis verification PASSED
```

### For New Deployments

1. **Pull latest code:**
```bash
git pull origin main
```

2. **Build docker image:**
```bash
docker compose build
```

3. **Start node:**
```bash
docker compose up -d
```

4. **Verify startup logs:**
```bash
docker compose logs node | grep -A 5 "Network Identity"
```

## Troubleshooting

### Error: "Database chain_id mismatch"
**Cause:** Your database was created for a different network.
**Fix:** Reset your data directory or delete the DB file.
```bash
animica node reset --force
# or
rm -rf ~/.animica/chain-0
```

### Error: "Database genesis mismatch"
**Cause:** Genesis file changed or DB was initialized with old genesis.
**Fix:** Same as above - reset data directory.

### Error: "Genesis hash mismatch"
**Cause:** Genesis file content doesn't match pinned hash in code.
**Fix:** 
1. If you pulled latest code: rebuild docker image
2. If genesis intentionally changed: update pinned hash in core/network_manifest.py
3. Reset data directory

### Peers not connecting
**Cause:** Network identity mismatch in P2P handshake.
**Check:**
```bash
animica node status | grep -E "peers|handshake"
```
**Fix:** Ensure all nodes are running same version with same genesis.

## Summary

This PR establishes a robust foundation for network identity enforcement:

1. ✅ Single source of truth (core/network_manifest.py)
2. ✅ DB metadata verification (prevents cross-network contamination)
3. ✅ Genesis verification CLI (operational diagnostic tool)
4. ✅ Clear error messages (actionable remediation steps)
5. ✅ Comprehensive tests (8 test cases covering key scenarios)

Remaining work focuses on P2P handshake validation and sync improvements, which are documented above with specific implementation guidance.
