# Animica Mainnet Network Identity

## Canonical Network Parameters

**IMPORTANT:** This document defines the single source of truth for Animica mainnet identity. All components must use these values from `core.network_manifest`.

### Mainnet Identity

- **Network Name:** `mainnet`
- **Chain ID:** `0` (zero)
- **Genesis File:** `core/genesis/mainnet.json`
- **Genesis Hash:** `0x6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242`
- **HRP (Address Prefix):** `anim`
- **Protocol Version:** `1.0.0`
- **P2P Network ID:** `animica:0`

### Why Chain ID 0?

Mainnet uses chain ID 0. This is the canonical value and is NOT an error. Common chain IDs:

- **Mainnet:** 0
- **Testnet:** 2
- **Devnet:** 1337

## Source of Truth

All network identity values are defined in `core/network_manifest.py`:

```python
from core.network_manifest import MAINNET_MANIFEST

# Access canonical values
chain_id = MAINNET_MANIFEST.chain_id  # 0
genesis_hash = MAINNET_MANIFEST.pinned_genesis_hash_hex  # 0x6a27e...
genesis_path = MAINNET_MANIFEST.genesis_path  # core/genesis/mainnet.json
```

**DO NOT:**
- Hardcode chain ID values in application code
- Use fallback defaults for mainnet
- Skip validation of network identity

**DO:**
- Import from `core.network_manifest`
- Validate chain ID matches expected network
- Fail fast on identity mismatches

## Validation

### On Node Startup

The node validates genesis identity on startup:

1. Computes genesis block hash from `core/genesis/mainnet.json`
2. Compares against pinned hash in `MAINNET_MANIFEST.pinned_genesis_hash`
3. **Fails fast** if mismatch detected with clear error message

### In Docker Containers

Docker containers include:
- ✅ `core/network_manifest.py` (bundled in image)
- ✅ `core/genesis/mainnet.json` (bundled in image)
- ✅ Environment variables properly set in `docker-compose.mainnet.yml`:
  ```yaml
  ANIMICA_NETWORK: "mainnet"
  ANIMICA_CHAIN_ID: "0"
  GENESIS_PATH: "/app/core/genesis/mainnet.json"
  ANIMICA_DATA_DIR: "/data/chain-0"
  ```

## Common Issues & Solutions

### Issue: "core.network_manifest not available"

**Cause:** Import failure for network manifest (packaging issue)

**Solution for mainnet (CRITICAL):**
```bash
# Rebuild Docker image to include core/ directory
docker compose -f ops/docker/docker-compose.mainnet.yml build --no-cache
docker compose -f ops/docker/docker-compose.mainnet.yml up -d
```

**Solution for host (Python):**
```bash
# Ensure core/ is in PYTHONPATH
export PYTHONPATH=/path/to/repo:$PYTHONPATH
# Or reinstall in development mode
pip install -e .
```

### Issue: Genesis hash mismatch

**Error:**
```
GenesisError: genesis does not match pinned network genesis
  Expected (pinned): 0x6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242
  Found (computed):  0xd2d2897104110b86bb60ccec251a7e2313f4abb301f8cc532d60162c20d3644f
```

**Cause:** 
- Node database was initialized with a different genesis
- Genesis file was modified without updating pinned hash
- Using wrong genesis file for the network

**Solution:**
```bash
# Option 1: Reset chain data (CAUTION: loses all local chain state)
animica node reset

# Option 2: For Docker
docker compose -f ops/docker/docker-compose.mainnet.yml down -v
docker compose -f ops/docker/docker-compose.mainnet.yml up -d

# Option 3: Manual reset
rm -rf ~/.animica/chain-0  # For host
rm -rf /data/chain-0       # For Docker container
```

### Issue: Chain ID mismatch

**Warning:**
```
⚠️  WARNING: Chain ID mismatch detected!
  CLI config expects chain_id=0 (mainnet)
  RPC node reports chain_id=2
```

**Cause:**
- Querying wrong network (e.g., CLI configured for mainnet but RPC is testnet)
- Environment variable override (`ANIMICA_CHAIN_ID` set incorrectly)

**Solution:**
```bash
# Check current network
animica network get

# Set correct network
animica network set mainnet

# Or set RPC URL directly
export ANIMICA_RPC_URL=http://mainnet-node:8545

# Verify
animica node status
```

### Issue: Genesis hash prints as bound method

**Error:**
```
RPC Reported Genesis Hash: 0x<bound method Header.hash of Header(...)>
```

**Cause:** Bug in RPC response formatting (now fixed)

**Solution:**
This bug has been fixed in this commit. Upgrade to latest version:
```bash
git pull
docker compose -f ops/docker/docker-compose.mainnet.yml build
docker compose -f ops/docker/docker-compose.mainnet.yml up -d
```

## Verifying Network Identity

### Via CLI

```bash
# Show network identity summary
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
```

### Via RPC

```bash
# Get chain ID
curl -X POST http://localhost:8545 -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"net.getChainId","params":[],"id":1}'

# Expected: {"jsonrpc":"2.0","result":0,"id":1}

# Get genesis hash
curl -X POST http://localhost:8545 -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"net.getGenesisHash","params":[],"id":1}'

# Expected: {"jsonrpc":"2.0","result":"0x6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242","id":1}
```

### Programmatic Verification

```python
from core.network_manifest import MAINNET_MANIFEST, verify_genesis

# Verify genesis file matches pinned hash
try:
    verify_genesis(MAINNET_MANIFEST, raise_on_mismatch=True)
    print("✓ Genesis verified successfully")
except GenesisError as e:
    print(f"✗ Genesis verification failed: {e}")
```

## Testing

Run network identity consistency tests:

```bash
# Test canonical chain_id and genesis hash
pytest core/tests/test_network_identity_consistency.py -v

# Test genesis hash format (RPC)
pytest rpc/tests/test_genesis_hash_format.py -v
```

## Changelog

### 2026-01-18: Mainnet Reset
- **Genesis Hash:** `0x6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242`
- **Chain ID:** 0 (unchanged)
- **Genesis File:** Updated with new premine allocations

### 2026-01-16: Previous Genesis (Deprecated)
- **Genesis Hash:** `0x5868b982d22fe2eb4eb15567dd6afdbae453001388bc23a2517639729428cfda`
- **Status:** Deprecated, replaced by 2026-01-18 reset

## References

- **Network Manifest:** `core/network_manifest.py`
- **Network Params:** `core/network_params.py`
- **Genesis File:** `core/genesis/mainnet.json`
- **Docker Compose:** `ops/docker/docker-compose.mainnet.yml`
- **Tests:** `core/tests/test_network_identity_consistency.py`

## Support

If you encounter network identity issues:

1. Check this document for common solutions
2. Verify you're running the latest code: `git pull`
3. Rebuild Docker containers if using Docker
4. Reset chain data if genesis mismatch persists
5. Check environment variables: `env | grep ANIMICA`

For persistent issues, check:
- Node logs: `docker compose logs node` or `~/animica/logs/`
- RPC health: `curl http://localhost:8545/healthz`
- P2P status: `animica node status`
