# PR Summary: Fix Mainnet Startup & Sync Issues

## Problem Statement

Users running mainnet nodes encountered several issues:
1. Chain ID showing as 0 but confusion about whether it should be 1
2. Warning: "core.network_manifest not available, using hardcoded chain_id values"
3. Genesis hash reporting bug: printed as `0x<bound method Header.hash of Header(...)>` instead of proper hex
4. Genesis mismatch errors without clear remediation
5. Sync stuck with "no_fresh_peer_tips" and peer connection timeouts

## Canonical Mainnet Identity (CRITICAL CORRECTION)

**Mainnet chain_id is 0, not 1.** This is correct and intentional.

- **Network Name:** mainnet
- **Chain ID:** 0 (zero)
- **Genesis Hash:** `0x6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242`
- **Genesis File:** `core/genesis/mainnet.json`

See: `MAINNET_NETWORK_IDENTITY.md` for complete documentation.

## Changes Made

### 1. Fixed Genesis Hash Reporting Bug ✅

**File:** `rpc/methods/net.py`

**Issue:** Genesis hash was returned as a bound method object string instead of hex string.

**Fix:**
- Added strict validation in `net_get_genesis_hash()` to ensure proper hex string output
- Added defensive checks for callable objects (methods) and ensure they are invoked
- Validate output format: must be 0x-prefixed, 66 characters, valid hex
- Added clear error messages if format is invalid

**Result:** Genesis hash now always returns proper format like:
```
0x6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242
```

### 2. Improved network_manifest Import Handling ✅

**File:** `python/animica/config.py`

**Issue:** Fallback warnings were noisy and confusing. No distinction between dev/test and mainnet.

**Fix:**
- Made mainnet network_manifest import failures **fatal** (fail fast)
- Added clear error messages for packaging/deployment issues
- Kept fallback for dev/test networks with appropriate warnings
- Removed generic "not available" warnings that confused operators

**Result:** 
- Mainnet will fail fast if network_manifest is missing (indicates deployment issue)
- Dev/test networks degrade gracefully with clear warnings
- No more confusing warnings during normal operation

### 3. Comprehensive Documentation ✅

**File:** `MAINNET_NETWORK_IDENTITY.md`

**Contents:**
- Documents canonical mainnet chain_id=0 (not 1)
- Single source of truth guidance
- Common issues and solutions with remediation commands
- Verification procedures (CLI, RPC, programmatic)
- Docker container asset verification
- Error message explanations

### 4. Added Validation Tests ✅

**Files:**
- `core/tests/test_network_identity_consistency.py`
- `rpc/tests/test_genesis_hash_format.py`
- `test_genesis_hash_format_unit.py`

**Tests verify:**
- Mainnet chain_id is consistently 0 across all components
- Genesis hash matches between manifest, params, and genesis file
- Genesis hash is proper hex string, not bound method
- Network manifest is available and all networks are defined
- Genesis files exist and are valid JSON

### 5. Existing Validation Already in Place ✅

**Discovery:** Genesis validation on startup was already implemented!

**Location:** `rpc/deps.py` (lines 1104-1140)

**What it does:**
- Computes genesis identity from file
- Calls `enforce_pinned_genesis()` to validate against pinned hash
- Logs network identity on startup
- Fails fast on mismatch with clear error

**The error messages from `enforce_pinned_genesis()` have been improved** to include:
- Expected vs found genesis hashes
- Clear explanation of causes
- Specific remediation commands
- DEV-ONLY bypass option (with scary warnings)

## What Was NOT Changed

### 1. P2P Sync Diagnostics

**Status:** Already robust

The P2P layer already has:
- "no_fresh_peer_tips" detection and handling
- Peer connection timeout handling
- Bootstrap diagnostics
- Retry logic with exponential backoff

**Files:** `p2p/node/p2p_service.py`, `p2p/sync/*.py`

### 2. Node Startup Logging

**Status:** Already comprehensive

Node startup already logs:
- Genesis identity (path, hash, file hash)
- Chain identity (chain_id, genesis_hash, fork_id, consensus_id, protocol_version)
- Network configuration

**Location:** `rpc/deps.py` lines 1118-1132

### 3. Docker Container Assets

**Status:** Already correct

The Docker build already includes:
- `core/network_manifest.py` (entire repo copied at line 107)
- `core/genesis/*.json` files
- Proper environment variables in `docker-compose.mainnet.yml`

## Testing

All tests pass:
```bash
# Network identity consistency
pytest core/tests/test_network_identity_consistency.py -v
# Output: 5 passed in 0.22s

# Genesis hash format unit tests
python3 test_genesis_hash_format_unit.py
# Output: ✅ All genesis hash format tests passed!
```

## Migration / Operator Actions

### If Seeing "core.network_manifest not available" on Mainnet

**This is now a FATAL error.** It indicates a critical packaging/deployment issue.

**Fix:**
```bash
# For Docker
docker compose -f ops/docker/docker-compose.mainnet.yml build --no-cache
docker compose -f ops/docker/docker-compose.mainnet.yml up -d

# For host Python
pip install -e .  # Reinstall in development mode
```

### If Seeing Genesis Hash Mismatch

**Error example:**
```
GenesisError: genesis does not match pinned network genesis
  Expected (pinned): 0x6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242
  Found (computed):  0xd2d2897104110b86bb60ccec251a7e2313f4abb301f8cc532d60162c20d3644f
```

**Cause:** Node database was initialized with different genesis.

**Fix:**
```bash
# Option 1: CLI reset
animica node reset

# Option 2: Docker reset
docker compose -f ops/docker/docker-compose.mainnet.yml down -v
docker compose -f ops/docker/docker-compose.mainnet.yml up -d

# Option 3: Manual
rm -rf ~/.animica/chain-0  # or /data/chain-0 in Docker
```

### If Seeing Chain ID Mismatch in Status

**Warning example:**
```
⚠️  WARNING: Chain ID mismatch detected!
  CLI config expects chain_id=0 (mainnet)
  RPC node reports chain_id=2
```

**Cause:** Querying wrong network or environment variable override.

**Fix:**
```bash
# Check and set correct network
animica network set mainnet

# Or set RPC URL directly
export ANIMICA_RPC_URL=http://mainnet-node:8545

# Verify
animica node status
```

## Summary of Improvements

✅ **Genesis hash bug fixed** - No more bound method strings
✅ **Network identity documented** - Clear canonical values (chain_id=0)
✅ **Import handling improved** - Fatal errors on mainnet, graceful degradation on dev/test
✅ **Comprehensive tests added** - Prevent regressions
✅ **Operator guidance improved** - Clear error messages and remediation steps
✅ **Discovered existing safeguards** - Genesis validation already in place, just improved messages

## What This Fixes

From the original problem statement:

1. ✅ **"Chain ID: 0 should be 1"** - Clarified that 0 is correct
2. ✅ **"network_manifest not available"** - Now fatal on mainnet, clear on dev/test
3. ✅ **Genesis hash prints as bound method** - Fixed in RPC method
4. ✅ **Genesis mismatch confusion** - Improved error messages with remediation
5. ⚠️  **"Sync stuck with no_fresh_peer_tips"** - Already has robust handling; separate issue

## Files Changed

- `rpc/methods/net.py` - Fixed genesis hash formatting
- `python/animica/config.py` - Improved network_manifest import handling
- `core/tests/test_network_identity_consistency.py` - Added consistency tests
- `rpc/tests/test_genesis_hash_format.py` - Added RPC format tests
- `test_genesis_hash_format_unit.py` - Added unit tests
- `MAINNET_NETWORK_IDENTITY.md` - Added comprehensive documentation
- `core/network_params.py` - Existing validation improved (error messages)

## Verification

To verify the fixes:

```bash
# 1. Check network identity
animica node status

# Expected output:
# === Network Identity ===
# Local Config Network: mainnet
# Local Config Chain ID: 0
# Local Config Genesis Path: /path/to/core/genesis/mainnet.json
# Local Pinned Genesis Hash: 0x6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242
#
# RPC Reported Chain ID: 0
# RPC Reported Genesis Hash: 0x6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242

# 2. Run tests
pytest core/tests/test_network_identity_consistency.py -v
python3 test_genesis_hash_format_unit.py

# 3. Check RPC directly
curl -X POST http://localhost:8545 -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"net.getGenesisHash","params":[],"id":1}'
# Should return: {"jsonrpc":"2.0","result":"0x6a27e...","id":1}
```
