# Network Identity Integration - Implementation Complete

## Overview

This document summarizes the successful integration of the network identity framework (`network_manifest` and `network_identity` modules) across the Animica codebase. The goal was to eliminate hardcoded network→chain_id mappings and ensure all components use a single source of truth for network identity.

## Problem Statement

The original issue "Now integrate network identity" referred to completing the integration of the network identity framework that was previously created but not fully adopted across the codebase. Components were still using hardcoded mappings like:

```python
# Old pattern (inconsistent)
if network == "mainnet":
    chain_id = 0
elif network == "testnet":
    chain_id = 2
```

This created:
- **Risk of inconsistencies** across different modules
- **Maintenance burden** when adding new networks
- **No single source of truth** for network parameters

## Solution Implemented

### 1. Core Module: `core/network_manifest.py`

**What it provides:**
- `NetworkManifest` dataclass with canonical network definitions
- `MAINNET_MANIFEST`, `TESTNET_MANIFEST`, `DEVNET_MANIFEST` constants
- `get_manifest(network=..., chain_id=...)` - Unified lookup API
- `get_manifest_for_env()` - Environment-based lookup
- `verify_genesis(manifest)` - Genesis hash verification
- Helper functions: `is_mainnet()`, `is_testnet()`, `is_devnet()`

**Network definitions:**
```python
MAINNET_MANIFEST = NetworkManifest(
    network_name="mainnet",
    chain_id=0,
    genesis_path=GENESIS_DIR / "mainnet.json",
    pinned_genesis_hash=bytes.fromhex("6a27e93193020cd0..."),
    p2p_network_id="animica:0"
)
```

### 2. Files Integrated

#### `python/animica/config.py`
**Before:** Hardcoded chain_id values in `get_network_defaults()`
```python
"mainnet": {"chain_id": 0, ...}
"testnet": {"chain_id": 2, ...}
```

**After:** Uses `network_manifest.get_manifest()` to get canonical chain_id
```python
manifest = get_manifest(network=network)
canonical_chain_id = manifest.chain_id
```

**Impact:**
- Network defaults now sourced from manifest
- Validation uses `manifest.chain_id` instead of hardcoded checks
- Consistent error messages reference manifest expectations

#### `core/genesis/loader.py`
**Enhancement:** Improved network name resolution priority
```python
# Priority: 1) genesis.network, 2) ANIMICA_NETWORK env, 3) manifest lookup by chain_id
network_name = genesis.get("network") or os.getenv("ANIMICA_NETWORK")
if not network_name:
    manifest = get_manifest(chain_id=int(genesis["chainId"]))
    if manifest:
        network_name = manifest.network_name
```

**Impact:**
- Better fallback logic for network name resolution
- DB metadata stores correct network name

#### `rpc/deps.py`
**Before:** Hardcoded chain_id→network mapping
```python
if cfg_view.chain_id == 0:
    network = "mainnet"
elif cfg_view.chain_id == 2:
    network = "testnet"
```

**After:** Uses manifest for resolution and validation
```python
manifest = get_manifest(chain_id=cfg_view.chain_id)
if manifest:
    network = manifest.network_name
    
# Validate chain_id matches network
if manifest and cfg_view.chain_id != manifest.chain_id:
    raise ValueError(...)
```

**Impact:**
- Consistent network resolution
- Enhanced validation
- Better error messages with remediation steps

#### `rpc/config.py`
**Enhancement:** Validation uses manifest
```python
manifest = get_manifest(network=network)
if manifest and chain_id != manifest.chain_id:
    raise ValueError(f"Network '{network}' MUST use chain_id={manifest.chain_id}")
```

**Impact:**
- Consistent validation across RPC layer
- All networks validated, not just mainnet

#### `p2p/config.py`
**Before:** Hardcoded `NETWORK_NAME_TO_CHAIN_ID` mapping

**After:** Built from manifest
```python
from core.network_manifest import all_manifests
NETWORK_NAME_TO_CHAIN_ID = {
    m.network_name: m.chain_id for m in all_manifests()
}
```

**Impact:**
- P2P network mapping automatically synced with manifest
- No manual updates needed for new networks

#### `python/animica/cli/network.py`
**Enhancement:** CLI uses manifest for network info display
```python
manifest = get_manifest(network=network)
if manifest:
    chain_id = manifest.chain_id
    # Display chain_id, data_dir info
```

**Impact:**
- CLI shows canonical chain_id from manifest
- Consistent with other components

### 3. Already Integrated (Before This PR)

These components were already properly using `network_manifest`:
- ✅ `p2p/node/service.py` - P2P identity via `_load_network_manifest()`
- ✅ `python/animica/cli/chain.py` - Genesis verification commands
- ✅ `rpc/methods/net.py` - RPC methods
- ✅ `core/db/block_db.py` - DB metadata storage

## Key Benefits

### 1. Single Source of Truth
All network identity information (chain_id, genesis_hash, network_name) comes from `core/network_manifest.py`.

### 2. Consistency Guarantees
```python
# All these now use the same source:
config = load_network_config("mainnet")  # chain_id from manifest
defaults = get_network_defaults("mainnet")  # chain_id from manifest
manifest = get_manifest(network="mainnet")  # chain_id=0
ctx = build_context(cfg)  # validates against manifest
```

### 3. Better Error Messages
```
FATAL: Network 'mainnet' MUST use chain_id=0, but got chain_id=2.
This indicates a configuration error. Please check ANIMICA_CHAIN_ID 
environment variable and ensure it matches the network's canonical 
chain_id from network_manifest.
```

### 4. Backward Compatibility
All changes include fallback logic:
```python
try:
    from core.network_manifest import get_manifest
    manifest = get_manifest(network=network)
    chain_id = manifest.chain_id
except ImportError:
    # Fallback for environments without network_manifest
    chain_id = {"mainnet": 0, "testnet": 2}.get(network, 0)
```

### 5. Maintainability
Adding a new network only requires updating `core/network_manifest.py`:
```python
# All other components automatically pick it up
TESTNET2_MANIFEST = NetworkManifest(
    network_name="testnet2",
    chain_id=3,
    genesis_path=GENESIS_DIR / "testnet2.json",
    pinned_genesis_hash=bytes.fromhex("..."),
)
```

## Testing

### Manual Tests Performed

1. **Network manifest module:**
   ```bash
   python3 -c "from core.network_manifest import get_manifest; assert get_manifest(network='mainnet').chain_id == 0"
   ```
   ✅ PASSED

2. **Config integration:**
   ```bash
   ANIMICA_NETWORK=mainnet python3 -c "from python.animica.config import load_network_config; assert load_network_config('mainnet').chain_id == 0"
   ```
   ✅ PASSED

3. **Chain ID validation:**
   ```bash
   ANIMICA_NETWORK=mainnet ANIMICA_CHAIN_ID=2 python3 -c "from python.animica.config import load_network_config; load_network_config('mainnet')"
   ```
   ✅ PASSED - Correctly raised ValueError for mismatch

4. **P2P config:**
   ```bash
   python3 -c "from p2p.config import NETWORK_NAME_TO_CHAIN_ID; assert NETWORK_NAME_TO_CHAIN_ID['mainnet'] == 0"
   ```
   ✅ PASSED

### Comprehensive Integration Test
All components tested together to ensure:
- Network manifest provides correct values
- Config uses manifest for chain_id
- P2P uses manifest for network mapping
- Validation detects mismatches

✅ ALL TESTS PASSED

## What Was NOT Changed

The following files have functional `chain_id == 0` checks that are business logic, not network identity mappings. These are intentionally left as-is:

- `mining/templates.py` - Activation height logic for mainnet
- `consensus/rewards.py` - Premine enforcement for mainnet
- `consensus/validator.py` - Adaptive PoW for mainnet
- `rpc/methods/faucet.py` - Faucet disabled on mainnet
- `p2p/checkpoints/builtin.py` - Network-specific checkpoints
- `core/bootstrap.py` - Bootstrap password for mainnet
- `core/snapshot/policy.py` - Network-specific snapshot policies

These could optionally be updated to use `is_mainnet(chain_id)` helper for better maintainability, but it's not required for network identity integration.

## Migration Guide

### For Developers

When working with network identity, always use `network_manifest`:

```python
from core.network_manifest import get_manifest, is_mainnet

# Get network by name
manifest = get_manifest(network="mainnet")
print(f"Chain ID: {manifest.chain_id}")

# Get network by chain_id
manifest = get_manifest(chain_id=0)
print(f"Network: {manifest.network_name}")

# Check network type
if is_mainnet(chain_id):
    # Mainnet-specific logic
    pass
```

### For Operations

Network identity is now enforced automatically:
- Docker containers use `ANIMICA_NETWORK` env var
- RPC validates chain_id on startup
- Mismatches result in clear error messages with remediation steps

No action needed for existing deployments.

## Verification

You can verify the integration is working:

```bash
# Check manifest is accessible
python3 -c "from core.network_manifest import all_manifests; print([m.network_name for m in all_manifests()])"

# Verify config integration
python3 -c "from python.animica.config import load_network_config; print(load_network_config('mainnet').chain_id)"

# Test validation
ANIMICA_NETWORK=mainnet ANIMICA_CHAIN_ID=2 python3 -m rpc
# Should fail with clear error
```

## Summary

✅ **COMPLETE**: Network identity framework successfully integrated across all key components
✅ **TESTED**: All integration points verified with manual tests
✅ **CONSISTENT**: Single source of truth eliminates hardcoded mappings
✅ **MAINTAINABLE**: Adding new networks only requires updating one file
✅ **BACKWARD COMPATIBLE**: Fallback logic ensures robustness

The codebase now has a unified, consistent approach to network identity that prevents configuration errors and makes the system more maintainable.

## Related Documentation

- `PR_SUMMARY_NETWORK_IDENTITY.md` - Original network identity PR summary
- `NETWORK_IDENTITY_FIX_GUIDE.md` - Implementation guide
- `NETWORK_IDENTITY_FIX_SUMMARY.md` - Fix summary for specific issues
- `core/network_manifest.py` - Network manifest module source code
- `core/network_identity.py` - Alternative network identity module

## Commit History

1. `f345f69a` - Integrate network_manifest in config.py, genesis/loader.py, and rpc/deps.py
2. `8928a9d7` - Integrate network_manifest in CLI, RPC config, and P2P config
3. `e90e86b6` - Add helper functions to network_manifest for network type checks
