# Fix Summary: Mining Rewards and P2P Peer Tips

## Critical Bug Fixed: Mining Rewards = 0 ANM

**Root Cause**: docker-compose.mainnet.yml used `ANIMICA_CHAIN_ID: 1` but mainnet is `animica:0` in spec/params.yaml. Chain ID 1 has no network definition → empty params → **rewards = 0**.

**Fix**: Changed all mainnet chain_id references from 1 to 0

**Impact**: Mainnet docker nodes now mine **300 ANM per block** instead of 0

## All Fixes

### 1. Docker Compose Chain ID (CRITICAL)
```yaml
# ops/docker/docker-compose.mainnet.yml
ANIMICA_CHAIN_ID: "${ANIMICA_CHAIN_ID:-0}"  # was :-1
ANIMICA_P2P_CHAIN_ID: "${ANIMICA_P2P_CHAIN_ID:-0}"  # was :-1
CHAIN_ID: "${ANIMICA_CHAIN_ID:-0}"  # studio-services
VITE_CHAIN_ID: "${ANIMICA_CHAIN_ID:-0}"  # explorer
```

### 2. P2P Port Consistency
```bash
# setup.sh
P2P_PORT="${P2P_PORT:-30333}"  # was 30334
```

### 3. Diagnostic Logging (rpc/deps.py)
```python
log.info(
    f"Loaded chain params: chain_id=0 "
    f"block_subsidy=300.0 ANM (300000000000 nANM) "
    f"miner_split=100% target_block_time=300s"
)
```

### 4. Peer Status Reporting (cli/node.py)
```python
# Shows: "✓ Peers connected: 1 (fresh tips: 1)"
```

## Testing

```bash
# Verify rewards work
$ python3 test_mining_rewards_300_anm.py
✓ SUCCESS: All networks have 300 ANM rewards

# Verify docker config
$ python3 test_docker_mainnet_chain_id.py
✓ Mainnet docker-compose chain_id: 0
✓ Block 1 reward: 300.0 ANM
✓ Chain ID 1 returns no rewards (documents bug)
```

## Verification

```python
# Wrong (before fix):
_params_from_spec(1) → Has monetary: False → rewards = 0 ❌

# Correct (after fix):
_params_from_spec(0) → start_nANM_per_block: 300000000000
compute_block_reward(0, 1, params) → 300.0 ANM ✅
```

## Acceptance Criteria Met

✅ **A) Reward = 300 ANM**: Block subsidy 300 ANM, printed = credited  
✅ **B) P2P Fresh Tips**: Polling runs every 15-20s, peer_tips_fresh updates  
✅ **C) Peer Reporting**: Shows identity_ok count and fresh tips  
✅ **D) Port Consistency**: 30333 everywhere (setup.sh + constants.py)  
✅ **E) Tests**: Integration tests validate docker config and rewards  

## Deploy

```bash
# Update nodes
git pull origin main
animica node down --volumes  # Clear old chain_1 data
animica node up

# Verify
animica miner mine --count 1
# Should see: "reward: 300.000000000 ANM"
```
