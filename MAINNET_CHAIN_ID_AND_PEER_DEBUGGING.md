# Mainnet Chain ID Invariants and Peer Eligibility Debugging Guide

## Critical Invariant: Mainnet MUST Use chain_id=0

**Mainnet chain_id=0 is hardcoded at multiple levels to prevent misconfiguration:**

### 1. Genesis File
- **File:** `core/genesis/mainnet.json`
- **Field:** `"chainId": 0`
- **Impact:** All blocks created from this genesis will have chain_id=0

### 2. Configuration Validation
- **File:** `python/animica/config.py`
- **Function:** `load_network_config()`
- **Lines:** 473-482
- **Behavior:** Raises `ValueError` if network="mainnet" and chain_id != 0
- **Error Message:** Clear indication of misconfiguration with remediation steps

### 3. RPC Context Validation
- **File:** `rpc/deps.py`
- **Function:** `build_context()`
- **Behavior:** Defense-in-depth check; raises `ValueError` if mainnet has wrong chain_id
- **Purpose:** Catch misconfigurations that bypass config validation

### 4. Docker Compose
- **File:** `ops/docker/docker-compose.mainnet.yml`
- **Default:** `ANIMICA_CHAIN_ID: "${ANIMICA_CHAIN_ID:-0}"`
- **Lines:** 55, 143, 181

### 5. Entrypoint Script
- **File:** `ops/docker/entrypoints/entrypoint.sh`
- **Default:** `: "${ANIMICA_CHAIN_ID:=0}"`
- **Line:** 7

## P2P Handshake and Identity Validation

### Chain Identity Components

During P2P handshake, nodes exchange and validate these identity components:

1. **chain_id** (int) - Must match exactly
2. **fork_id** (uint32) - Derived from genesis_hash (CRC32)
3. **genesis_hash** (32 bytes) - Genesis block/header hash
4. **consensus_id** (string) - Consensus algorithm identifier
5. **protocol_version** (string) - P2P protocol version
6. **network_magic** (4 bytes) - Network-specific constant

### Handshake Validation Flow

**File:** `p2p/node/p2p_service.py`
**Function:** `_handle_hello()`

Validation order:
1. **Network Magic** (lines 6172-6188) - Rejects immediately if wrong network
2. **Chain ID** (lines 6190-6205) - Rejects if chain_id mismatch
3. **Genesis Hash** (lines 6232-6270) - Rejects if genesis mismatch
4. **Fork ID** (lines 6282-6298) - Rejects if fork mismatch
5. **Consensus ID** (lines 6308-6320+) - Warns if mismatch
6. **Protocol Version** (lines 6415-6421) - Rejects if incompatible

### Handshake Timeout

**Configuration:**
- Environment Variable: `ANIMICA_P2P_HANDSHAKE_TIMEOUT`
- Default: 3.0 seconds
- File: `p2p/node/peer_registry.py`
- Enforcement: `p2p/node/p2p_service.py::_enforce_handshake_timeout()`

**Behavior:**
- Peers that don't complete handshake within timeout are dropped
- Reason logged: `"hello_timeout"`
- Peer state transitions: `dialing` → `handshaking` → (`connected` | `failed`)

### Mismatch Reasons

When handshake fails, the following reasons are logged:

| Reason | Meaning | Points | Ban TTL |
|--------|---------|--------|---------|
| `network_magic_mismatch` | Different network (mainnet/testnet/devnet) | Penalized | No |
| `chain_id_mismatch` | Different chain_id | 0 | No |
| `genesis_mismatch` | Different genesis block | Penalized | No |
| `fork_id_mismatch` | Different fork (post-reset) | Penalized | No |
| `genesis_missing` | Peer didn't provide genesis | Penalized | No |
| `hello_timeout` | Handshake took too long | 0 | No |

## Debugging Peer Eligibility

### Check Peer List

```bash
animica peer list -v
```

Shows peer states:
- `inbound` / `outbound` - Connection direction
- `handshaking` - Still in handshake phase (should complete in <3s)
- `connected` - Handshake complete, peer is eligible
- `unknown` - Peer ID not yet established

### Check Node Status

```bash
animica node status
```

Look for:
- **Chain ID** - MUST be 0 for mainnet
- **Peer counts** - total / inbound / outbound
- **Sync status** - Should show peer tips if peers are eligible

### Common Issues

#### Issue: "Chain ID: 1" on mainnet node

**Symptom:**
```
Chain ID: 1
```

**Root Cause:** Old genesis file or database with chain_id=1

**Fix:**
1. Stop the node
2. Remove data directory (e.g., `~/.animica/chain-1` or `/data/chain-1`)
3. Ensure genesis file has `"chainId": 0`
4. Restart node - it will create new DB from correct genesis

#### Issue: Peers stuck "handshaking"

**Symptom:**
```
inbound peer unknown (...) [handshaking]
```

**Root Cause:** Timeout not triggering OR mismatch not being detected

**Debug Steps:**
1. Check logs for `"Peer handshake mismatch"` warnings
2. Check for `"hello_timeout"` messages
3. Verify `ANIMICA_P2P_HANDSHAKE_TIMEOUT` is set (default 3s)
4. Restart P2P service

**Expected Behavior:** Peers should either complete handshake or timeout within 3-8 seconds

#### Issue: "no_fresh_peer_tips"

**Symptom:**
```
Sync status: SYNCING
sync_status_reason: "no_fresh_peer_tips"
```

**Root Cause:** No peers completed handshake successfully

**Debug Steps:**
1. Check peer count: `animica peer list`
2. Check handshake logs for mismatch reasons
3. Verify local chain_id matches network
4. Verify local genesis matches network

**Common Mismatch:**
- Local: chain_id=1, Genesis for chain_id=1
- Peer: chain_id=0, Genesis for chain_id=0
- Result: `chain_id_mismatch` rejection, no eligible peers

#### Issue: Peers connect but immediately disconnect

**Symptom:**
```
Peer handshake mismatch: chain_id_mismatch local=0 remote=1
```

**Root Cause:** Peer is on wrong chain

**Fix:** Either:
1. Update peer to use correct genesis (if you control it)
2. Connect to different peers (if you're on mainnet, use mainnet seeds)

## Verification Commands

### Verify Genesis Chain ID
```bash
jq '.chainId' core/genesis/mainnet.json
# Expected output: 0
```

### Verify Config Chain ID
```bash
python3 -c "from animica.config import load_network_config; print(load_network_config('mainnet').chain_id)"
# Expected output: 0
```

### Verify Running Node Chain ID
```bash
animica node status | grep "Chain ID"
# Expected output: Chain ID: 0
```

### Check P2P Handshake Logs
```bash
# Docker
docker logs animica-mainnet-node 2>&1 | grep -i "handshake"

# Local
journalctl -u animica-node | grep -i "handshake"
```

Look for:
- `"Peer handshake mismatch"` - Indicates identity mismatch
- `"hello_timeout"` - Indicates timeout
- Successful handshakes show no warnings

## Environment Variables

### Critical for Mainnet

```bash
# Network selection (affects defaults)
export ANIMICA_NETWORK=mainnet

# Chain ID (MUST be 0 for mainnet, but defaults are correct)
export ANIMICA_CHAIN_ID=0

# Data directory (isolated by chain_id)
export ANIMICA_DATA_DIR=/data/chain-0

# Genesis file path
export GENESIS_PATH=/app/core/genesis/mainnet.json
```

### P2P Configuration

```bash
# Handshake timeout (default 3.0s)
export ANIMICA_P2P_HANDSHAKE_TIMEOUT=3.0

# P2P listen address
export ANIMICA_P2P_LISTEN_TCP=0.0.0.0:30333

# P2P seeds (optional, auto-selected by chain_id)
export ANIMICA_P2P_SEEDS=""
```

## Testing Chain ID Enforcement

Run the test suite:
```bash
pytest test_mainnet_chain_id_fix.py -v
```

Tests verify:
1. ✓ Mainnet genesis has chain_id=0
2. ✓ Config validates mainnet chain_id=0
3. ✓ RPC deps validates mainnet chain_id=0
4. ✓ Testnet uses chain_id=2
5. ✓ Devnet uses chain_id=1337

## Further Reading

- **P2P Handshake Spec:** `p2p/specs/HANDSHAKE.md`
- **Chain Parameters:** `spec/params.yaml`
- **Genesis Format:** `core/genesis/README.md`
- **Network Configuration:** `python/animica/config.py`
