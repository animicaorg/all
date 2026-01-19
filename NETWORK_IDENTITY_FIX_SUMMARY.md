# Network Identity Fix - Mainnet Chain ID 0 Enforcement

## Problem Statement Summary
The issue tracker identified critical failures:
1. Docker node crashes with GenesisError pinned mismatch
2. CLI/status sometimes shows Chain ID 1 when node says 0
3. Sync never progresses ("no_fresh_peer_tips")
4. Mining credits 300 ANM but wallet balance doesn't increase

## Solution Implemented: Single Source of Truth

### Core Module: `core/network_identity.py`

**Purpose**: Centralize all network identity resolution to eliminate inconsistencies.

**Key Functions**:
```python
from core.network_identity import resolve_network_identity

# Get complete network identity
identity = resolve_network_identity(network="mainnet")
# OR
identity = resolve_network_identity(chain_id=0)

# Returns NetworkIdentity with:
# - network: "mainnet"
# - chain_id: 0
# - genesis_path: Path to mainnet.json
# - genesis_identity_hash: 32-byte genesis block hash
# - pinned_expected_hash: Expected hash from network_params
# - db_dir: Path to chain-0 data directory
# - p2p_dir: Path to P2P data directory
```

**Enforced Invariants**:
1. **Mainnet = Chain ID 0**: Non-negotiable, enforced in code
2. **Deterministic**: Same inputs always produce same outputs
3. **Validated**: Genesis hash must match pinned value
4. **Fail-fast**: Clear errors when mismatches occur

### Network Mapping (Immutable)
```python
NETWORK_CHAIN_ID_MAP = {
    "mainnet": 0,      # MUST be 0
    "testnet": 2,      # MUST be 2
    "devnet": 1337,    # MUST be 1337
}
```

### Build-Time Safety Test
`test_mainnet_chain0_pinned_matches_file()` ensures:
- Genesis file for mainnet matches pinned hash in `core/network_params.py`
- Prevents accidental genesis modifications
- Will fail CI if genesis/pins get out of sync
- **This test is REQUIRED to pass in CI - never bypass it**

## Current State of System

### ✅ Already Working
1. **RPC Methods Exist**:
   - `net.getChainId` - returns node's chain_id
   - `net.getGenesisHash` - returns node's genesis hash
   
2. **Status Command Enhanced**:
   - `animica node status` calls both RPC methods
   - Compares RPC values against local config
   - Shows "Network Identity" section with diagnostics
   - Warns loudly on mismatches

3. **Docker Compose Configured**:
   - `ops/docker/docker-compose.mainnet.yml` sets:
     - `ANIMICA_NETWORK: "mainnet"`
     - `ANIMICA_CHAIN_ID: "0"` 
     - `GENESIS_PATH: "/app/core/genesis/mainnet.json"`
     - `ANIMICA_DATA_DIR: "/data/chain-0"`

### 🔧 Needs Integration
The new `network_identity` module needs to be adopted in:

1. **genesis/loader.py** - Use `resolve_network_identity()` instead of ad-hoc resolution
2. **animica/config.py** - Use centralized mapping for network→chain_id
3. **rpc/deps.py** - Use identity module for context building
4. **p2p handshake** - Include network identity in peer validation

## Remaining Work

### High Priority

#### 1. Fix Mining Balance Credit (Critical)
**Problem**: Miner says "credited 300 ANM" but balance doesn't increase

**Root Cause**: Reward might not be applied to state before RPC returns, or wallet queries stale state

**Fix Required**:
- Trace `miner.mine` RPC method execution path
- Ensure reward writes to state DB in same transaction as block acceptance
- Make wallet query HEAD state (not safe head with lag)
- Add postcondition validation in miner CLI

**Implementation**:
```python
# In mining/cli/miner.py after successful mine:
# 1. Get old balance
old_balance = client.request("wallet.getBalance", [address, "latest"])

# 2. Mine block
result = client.request("miner.mine", payload)

# 3. Get new balance (force latest)
new_balance = client.request("wallet.getBalance", [address, "latest"])

# 4. Validate increase
expected_increase = result["totalReward"]
actual_increase = new_balance - old_balance
if actual_increase != expected_increase:
    log.error("BALANCE MISMATCH: Expected increase %d, got %d", 
              expected_increase, actual_increase)
    # Dump diagnostics
    raise ValueError("Mining reward not properly credited")
```

#### 2. Fix Docker GenesisError Crashes
**Problem**: Node crashes on startup with pinned hash mismatch

**Solutions**:

**Option A: Auto-repin (Development Only)**
```python
# In core/genesis/loader.py
if os.getenv("ANIMICA_AUTO_REPIN_GENESIS") == "1":
    if computed_hash != pinned_hash:
        logger.warning("GENESIS HASH MISMATCH - AUTO-REPINNING (DEV ONLY)")
        logger.warning("  Expected: %s", pinned_hash.hex())
        logger.warning("  Computed: %s", computed_hash.hex())
        # Continue with computed hash
        # Write GENESIS_REPINNED marker file
```

**Option B: GENESIS_ID file (Production Safe)**
```python
# On node startup, write /data/chain-0/GENESIS_ID:
{
  "network": "mainnet",
  "chain_id": 0,
  "genesis_hash": "0x6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242",
  "genesis_file_checksum": "sha256:...",
  "created_at": "2026-01-19T14:00:00Z"
}

# On subsequent startups:
# - If GENESIS_ID exists and differs: REFUSE to start
# - Instruct user: "animica node reset" or delete volume
```

#### 3. Fix Sync Not Starting
**Problem**: Sync stalls with "no_fresh_peer_tips" at genesis

**Fix Required**:
- Verify P2P handshake includes `network_identity` fields
- Add aggressive peer tip polling when at genesis height
- Add detailed logging for sync stuck diagnostics
- Implement timeout + retry for handshake

**Example Handshake**:
```python
handshake_msg = {
    "protocol_version": "1.0",
    "network": "mainnet",
    "chain_id": 0,
    "genesis_hash": "0x6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242",
    "head_height": current_height,
    "head_hash": current_head_hash,
}

# On receive:
if peer_handshake["chain_id"] != our_chain_id:
    disconnect_peer(reason="chain_id_mismatch")
if peer_handshake["genesis_hash"] != our_genesis_hash:
    disconnect_peer(reason="genesis_mismatch")
```

### Medium Priority

#### 4. Add `animica doctor fix-genesis` Command
```bash
# Usage:
animica doctor fix-genesis --network mainnet --chain-id 0

# Actions:
# 1. Dump current identity
# 2. Check for mismatches (pinned vs computed)
# 3. Offer to delete/rename incompatible DB
# 4. Reinitialize with correct genesis
# 5. Restart node (if running)
```

#### 5. Refactor All Call Sites
Systematically replace ad-hoc identity resolution with:
```python
from core.network_identity import resolve_network_identity

identity = resolve_network_identity(network=cfg.network)
# Use identity.chain_id, identity.genesis_path, etc.
```

### Low Priority

#### 6. Add Integration Tests
- `test_docker_mainnet_startup()` - verify container stays up
- `test_mining_balance_increase()` - mine 1 block, check balance
- `test_two_node_sync()` - node A mines, node B syncs

## How to Use

### For Developers
```python
# Always use the network_identity module:
from core.network_identity import resolve_network_identity

# Get identity for a network
identity = resolve_network_identity(network="mainnet")
print(f"Chain ID: {identity.chain_id}")        # 0
print(f"DB path: {identity.db_dir}")           # ~/.local/share/animica/chain-0
print(f"Genesis: {identity.genesis_path}")     # /path/to/core/genesis/mainnet.json

# Validate it matches pinned
from core.network_identity import validate_network_identity
validate_network_identity(identity)  # Raises if mismatch
```

### For Operations
```bash
# Check node network identity
animica node status

# Look for "Network Identity" section:
# === Network Identity ===
# Local Config Network: mainnet
# Local Config Chain ID: 0
# RPC Reported Chain ID: 0
# ✓ Network identity verified: RPC and local config match

# If mismatch:
# ⚠️  WARNING: Chain ID mismatch detected!
#   CLI config expects chain_id=0 (mainnet)
#   RPC node reports chain_id=1
#   Fix: Set ANIMICA_NETWORK or ANIMICA_RPC_URL correctly.
```

### For Docker Users
```bash
# Start mainnet node
animica node up

# Should see:
# Container: animica-mainnet-node (running)
# RPC: http://localhost:8545
# Network: mainnet (chain_id=0)

# If crashes with GenesisError:
docker compose down -v  # Delete volumes
docker compose up -d    # Restart fresh
```

## Testing

### Manual Test: Network Identity Resolution
```bash
cd /home/runner/work/all/all

python3 << 'EOF'
from core.network_identity import resolve_network_identity, validate_network_identity

# Test mainnet
print("Testing mainnet...")
identity = resolve_network_identity(network="mainnet")
assert identity.chain_id == 0, "Mainnet MUST be chain_id 0"
assert identity.network == "mainnet"
validate_network_identity(identity)
print("✓ Mainnet: chain_id=0, validated")

# Test chain_id resolution
identity2 = resolve_network_identity(chain_id=0)
assert identity2.network == "mainnet"
print("✓ chain_id=0 resolves to mainnet")

# Test all networks
for network in ["mainnet", "testnet", "devnet"]:
    id = resolve_network_identity(network=network)
    validate_network_identity(id)
    print(f"✓ {network}: chain_id={id.chain_id}, validated")

print("\n✅ All tests passed!")
EOF
```

### Manual Test: Status Command
```bash
# Start local node (if not running)
# animica node up

# Check status
animica node status

# Verify output contains:
# === Network Identity ===
# Local Config Network: mainnet
# Local Config Chain ID: 0
# RPC Reported Chain ID: 0 
# ✓ Network identity verified
```

## Files Changed

### New Files
- `core/network_identity.py` (375 lines) - Single source of truth
- `core/tests/test_network_identity.py` (280 lines) - Comprehensive tests
- `NETWORK_IDENTITY_FIX_SUMMARY.md` (this file)

### Modified Files
- None yet (module is standalone, needs integration)

## Next Steps for Full Completion

1. **Immediate** (2-3 hours):
   - Fix mining balance credit validation
   - Add GENESIS_ID file persistence
   - Test docker startup

2. **Short-term** (3-4 hours):
   - Refactor call sites to use network_identity
   - Fix P2P handshake identity exchange
   - Add doctor command

3. **Medium-term** (4-6 hours):
   - Add comprehensive integration tests
   - Update all documentation
   - CI hardening (enforce build-time test)

## Success Criteria (From Problem Statement)

- [x] Network identity deterministic and consistent
- [x] Genesis identity hash computed deterministically  
- [x] Pinned hash validation in place
- [ ] Docker starts reliably (needs GENESIS_ID persistence)
- [ ] Mining increases balance (needs postcondition validation)
- [ ] Sync snaps to best height (needs P2P handshake fix)

**Status**: 50% complete - foundation solid, integration needed

## Contact & Support

For issues or questions:
1. Check `animica node status` for diagnostics
2. Review logs: Docker logs show genesis validation
3. Verify network config: `echo $ANIMICA_NETWORK` should be "mainnet"
4. Reset if needed: `animica node reset` or `docker compose down -v`
