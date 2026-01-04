# Mining Troubleshooting Guide

This guide covers common issues when mining with Animica and how to resolve them.

⚠️ **Important**: As of the P2P-first update, mining uses local P2P validation by default (no proxy). See [P2P Sync Guide](p2p_sync.md) for P2P troubleshooting.

## Table of Contents

1. [RPC Parameter Errors](#rpc-parameter-errors)
2. [Device Selection Issues](#device-selection-issues)
3. [Mining Rewards and Balance Accrual](#mining-rewards-and-balance-accrual)
4. [Theta Adjustment and Difficulty](#theta-adjustment-and-difficulty)
5. [Network Connectivity](#network-connectivity)
6. [Performance Issues](#performance-issues)

---

## RPC Parameter Errors

### Error: "got an unexpected keyword argument 'device'" (Code -32602)

**Problem**: The CLI was sending a `device` parameter to the RPC node, which doesn't accept it.

**Status**: ✅ **FIXED** in recent update

**Explanation**: The `device` parameter is a CLI-only feature for future local device selection. It should never be sent to the RPC node.

**What was changed**:
- Removed `device` from all RPC calls (`miner.mine` method)
- Device selection now happens locally in the CLI
- Fallback handlers also updated to not send device parameter

**If you still see this error**:
1. Update to the latest version of the Animica CLI
2. Verify the fix: `git pull origin main`
3. The error should no longer occur

**Example of correct behavior**:
```bash
# Device parameter is accepted by CLI but NOT sent to RPC
animica miner mine-blocks --address anim1... --count 5 --device cuda
```

---

## Device Selection Issues

### Unsupported Device Error

**Error**: `unsupported device 'xyz'`

**Cause**: You specified an invalid device type.

**Solution**: Use one of the supported device types:
- `cpu` - CPU backend (always available)
- `cuda` - NVIDIA CUDA GPUs
- `rocm` - AMD ROCm GPUs
- `opencl` - OpenCL-capable devices
- `metal` - Apple Metal devices
- `auto` - Auto-detect best device (default)

**Example**:
```bash
# Correct usage
animica miner mine-blocks --address anim1... --count 5 --device auto
```

### Device Auto-Detection Fails

**Symptoms**: Warning message about device detection failure, falls back to CPU.

**Typical message**: `Could not auto-detect device. Falling back to CPU.`

**This is normal behavior** when:
- No GPU is available on the system
- GPU drivers are not installed
- Mining modules cannot access GPU

**Solution**:
- For CPU mining: no action needed, CPU fallback works fine
- For GPU mining: ensure GPU drivers are installed
- Explicitly specify device: `--device cpu` to skip auto-detection

---

## Mining Rewards and Balance Accrual

### Wallet Balance Not Increasing After Mining

**Problem**: You successfully mine blocks, but your wallet balance shows 0 or doesn't increase after mining multiple blocks.

**Status**: ✅ **FIXED** - Address parsing inconsistency resolved

**What was the bug**:
In earlier versions, there was an address format mismatch between mining reward application and balance queries:
- Mining rewards were credited using **32-byte digest** keys
- Balance queries looked up **34-byte (alg_id + digest)** keys
- Result: Rewards were stored but couldn't be retrieved

**Symptoms**:
```bash
# Mining appears successful
$ animica miner mine-blocks --address anim1... --count 5
✓ Mined 5 blocks
✓ Total reward: 20.0 ANM

# But balance shows 0 or doesn't increase
$ animica wallet show anim1...
Balance: 0 ANM  # ❌ Should show 20 ANM!
```

**Resolution**:
The bug has been fixed. Both mining and balance queries now use consistent **32-byte digest** addresses.

**Verification Steps**:

1. **Check you're on the latest version**:
   ```bash
   git pull origin main
   pip install -e . --force-reinstall
   ```

2. **Verify address format**:
   ```bash
   # Address must be valid Bech32 (starts with 'anim1')
   animica wallet list
   ```

3. **Test mining and balance query**:
   ```bash
   # Get initial balance
   animica wallet show anim1... --rpc-url http://127.0.0.1:8545
   
   # Mine blocks
   animica miner mine-blocks --address anim1... --count 3 \
     --rpc-url http://127.0.0.1:8545
   
   # Check balance again (should increase)
   animica wallet show anim1... --rpc-url http://127.0.0.1:8545
   ```

4. **Verify via RPC directly**:
   ```bash
   # Query balance via RPC
   curl -X POST http://127.0.0.1:8545/rpc \
     -H "Content-Type: application/json" \
     -d '{
       "jsonrpc": "2.0",
       "method": "state.getBalance",
       "params": ["anim1..."],
       "id": 1
     }'
   ```

### Common Pitfalls

**1. Wrong RPC Endpoint**
- Mining and balance queries must use the **same node**
- Different nodes may have different chain states

**Solution**:
```bash
# Use explicit RPC URL for both operations
export ANIMICA_RPC_URL="http://127.0.0.1:8545"
animica miner mine-blocks --address anim1... --count 5
animica wallet show anim1...
```

**2. Wallet Label vs Address Confusion**
- Mining accepts both wallet labels and raw Bech32 addresses
- Balance queries require the actual address

**Solution**:
```bash
# If using a label, ensure it exists in your wallet
animica wallet list

# Or use the full Bech32 address explicitly
animica miner mine-blocks --address anim1zqqjt... --count 5
```

**3. Chain Not Synced**
- Mining to an unsynced node won't reflect rewards from other miners
- Balance queries on stale state show outdated values

**Solution**:
```bash
# Check sync status
curl -X POST http://127.0.0.1:8545/rpc \
  -d '{"jsonrpc":"2.0","method":"chain.getHead","params":[],"id":1}'

# Wait for sync to complete before mining
```

**4. Block Not Canonical**
- If your mined block is orphaned due to a reorg, rewards are reverted
- This is normal blockchain behavior, not a bug

**Solution**:
- Wait for multiple confirmations before considering rewards "final"
- Monitor chain head to detect reorgs:
  ```bash
  # Watch for head changes
  watch -n 1 'curl -s http://127.0.0.1:8545/rpc \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"chain.getHead\",\"params\":[],\"id\":1}" \
    | jq -r ".result.height"'
  ```

### Reward Details

**Block Rewards**:
- Rewards are **immediately spendable** (no maturity lockup)
- Account-based model (not UTXO) - balance is directly updated
- Rewards calculated per emission schedule in `spec/params.yaml`

**Typical Devnet Rewards** (as of recent updates):
- Miner share: ~80% of total block reward
- AICF treasury: ~10%
- Chain treasury: ~10%
- Example: 5 ANM total → ~4 ANM to miner

**Checking Reward Details**:
```python
# Via Python API
from rpc.tests import new_test_client, rpc_call

client, cfg, _ = new_test_client()
result = rpc_call(client, "miner.mine", {"count": 1, "address": "anim1..."})

print(f"Mined: {result['result']['mined']} blocks")
print(f"Height: {result['result']['height']}")
print(f"Total reward: {result['result']['totalReward']} nANM")
print(f"Per-block rewards: {result['result']['rewards']}")
```

### Balance vs Reward Reporting

**Balance Query** (`state.getBalance`):
- Returns **total balance** in account
- Includes all sources: mining rewards, transfers, etc.
- Denominated in base units (nANM): 1 ANM = 1,000,000,000 nANM

**Mining Response** (`miner.mine`):
- Returns **reward for this mining session only**
- Does not include previous balance
- Useful for tracking incremental rewards

**Example**:
```bash
# Initial balance: 10 ANM
# Mine 2 blocks with 4 ANM reward each

# Mining response shows:
{
  "totalReward": 8000000000,  # 8 ANM from this session
  "rewards": [
    {"height": 101, "reward": 4000000000},
    {"height": 102, "reward": 4000000000}
  ]
}

# Balance query shows:
{
  "result": "18000000000"  # 18 ANM total (10 + 8)
}
```

---

## Theta Adjustment and Difficulty

### High Network Load Causing Slow Mining

**Problem**: Mining becomes very difficult under high network load.

**Status**: ✅ **IMPROVED** in recent update

**What was changed**:
- Increased `theta_max_micro`: 40M → 60M micro-nats (mining)
- Increased `step_clamp_micro`: 600k → 1M micro-nats (faster adaptation)
- Updated network config limits:
  - Mainnet: 32M → 60M micro-nats
  - Testnet: 24M → 48M micro-nats
  - Devnet: 12M → 24M micro-nats

**Understanding Theta (Θ)**:
- Theta represents mining difficulty (higher = harder)
- Measured in micro-nats (µ-nats), where 1 nat = 1,000,000 µ-nats
- Typical range: 0.3 to 60 nats (300,000 to 60,000,000 µ-nats)
- Target block time: 12 seconds

**Theta Adjustment Behavior**:
- **Fast blocks** (< 12s): Theta increases (harder mining)
- **Slow blocks** (> 12s): Theta decreases (easier mining)
- Uses EMA (Exponential Moving Average) for smooth adjustments

**Monitoring Theta**:
```python
# Check current mining theta
from rpc.methods.miner import _MINING_STATE

state = _MINING_STATE.get("theta_state")
if state:
    print(f"Current theta: {state.theta_micro / 1e6:.3f} nats")
    print(f"Min: {state.params.theta_min_micro / 1e6:.1f} nats")
    max_display = "unbounded" if state.params.theta_max_micro is None else f"{state.params.theta_max_micro / 1e6:.1f} nats"
    print(f"Max: {max_display}")
```

**If mining is too difficult**:
- Theta may have grown very high due to sustained high hash rate
- With unbounded theta, difficulty can scale indefinitely to match network capacity
- Wait for adjustment window (blocks arrive slower, theta decreases)
- Check RPC logs for theta adjustment messages
- Consider increasing mining hardware or joining a pool

**If mining is too easy**:
- This is self-correcting (fast blocks increase theta)
- Network will stabilize around target block time

---

## Network Connectivity

### Proxy Validation Failures

**Symptoms**: Mining attempts fail with proxy errors.

**Example errors**:
```
WARNING: Attempt 1/3 failed for miner.mine: [Errno -5] No address associated with hostname
ERROR: All 3 attempts failed for miner.mine
```

**Solutions**:

1. **Disable proxy mode** (mine directly to local/specified RPC):
   ```bash
   animica miner mine-blocks --address anim1... --count 5 --no-proxy
   ```

2. **Check network connectivity**:
   ```bash
   # Test connectivity to trusted RPC
   curl http://127.0.0.1:8545/rpc
   ```

3. **Use custom RPC endpoint**:
   ```bash
   animica miner mine-blocks --address anim1... --count 5 \
     --rpc-url http://your-node:8545
   ```

4. **Check proxy configuration**:
   - Default trusted RPC: `http://127.0.0.1:8545/rpc`
   - Max retries: 3 (configurable via `ANIMICA_PROXY_MAX_RETRIES`)
   - Retry delay: 1000ms (configurable via `ANIMICA_PROXY_RETRY_DELAY_MS`)

### RPC Connection Refused

**Error**: `Failed to connect to RPC`

**Common causes**:
1. Node not running
2. Wrong RPC URL
3. Firewall blocking connection
4. Node still synchronizing

**Solutions**:
```bash
# 1. Check if node is running
ps aux | grep animica

# 2. Verify RPC endpoint
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"chain.getHead","params":[],"id":1}'

# 3. Check RPC URL in CLI
animica miner mine-blocks --address anim1... --count 5 \
  --rpc-url http://127.0.0.1:8545
```

---

## Performance Issues

### Mining is Slower Than Expected

**Possible causes and solutions**:

1. **High Theta (difficulty)**:
   - Check current theta (see Theta section above)
   - Wait for adjustment if recently spiked
   - Normal under high network hash rate

2. **CPU bottleneck**:
   - Use GPU if available: `--device cuda` or `--device auto`
   - Monitor CPU usage: `top` or `htop`

3. **Network latency**:
   - Use local node instead of remote RPC
   - Check ping to RPC endpoint
   - Consider setting up local validator

### Frequent Forks / Stale Shares at Short Block Times

**Symptoms**:
- Blocks are found quickly but your node reports frequent forks or stale work.
- Miners are submitting solutions that are rejected as stale.

**Why it happens**:
- When the target block time is only a few seconds, normal internet propagation
  delays can be comparable to the block interval. That means two miners can
  legitimately mine different tips before hearing about each other.

**What to tune**:
- **Increase the target block interval** in `spec/params.yaml` (`block.target_seconds`)
  if you want fewer forks and more stable convergence.
- Ensure miners **refresh templates on head changes** (the node now tags each
  template with a head generation and marks stale submissions explicitly).
- Optionally enforce a **minimum spacing** on block timestamps for dev/test
  networks by setting:
  - `ANIMICA_MIN_BLOCK_SPACING_MS` (e.g., `1000` for a 1s floor)
  - `ANIMICA_MAX_FUTURE_SECONDS` (default `5`) to reject far-future timestamps

**Reality check**: With 2–3 second blocks, forks are expected in real networks
unless propagation is extremely fast. The fix is reliable fork-choice/reorg
handling plus clear stale signaling—not eliminating forks entirely.

4. **Mempool congestion**:
   - Block building may be slow with many pending txs
   - This is normal behavior (ensures tx ordering)

### Blocks Fail to Mine

**Symptoms**: `mined: 0` in response, no blocks produced.

**Solutions**:

1. **Check address format**:
   ```bash
   # Must be valid Animica Bech32 address (starts with 'anim1')
   animica miner mine-blocks --address anim1... --count 5
   ```

2. **Check node logs**:
   ```bash
   tail -f ~/.animica/logs/node.log
   ```

3. **Verify chain state**:
   ```bash
   # Get current head
   curl -X POST http://127.0.0.1:8545/rpc \
     -d '{"jsonrpc":"2.0","method":"chain.getHead","params":[],"id":1}'
   ```

4. **Insufficient gas/resources**:
   - Check node configuration
   - Verify sufficient disk space
   - Check memory availability

---

## Environment Variables

Useful environment variables for mining:

```bash
# RPC endpoint
export ANIMICA_RPC_URL="http://127.0.0.1:8545/rpc"

# Trusted RPC for proxy validation
export ANIMICA_TRUSTED_RPC_URL="http://127.0.0.1:8545/rpc"

# Default payout address
export ANIMICA_MINER_ADDRESS="anim1..."

# Default mining device
export ANIMICA_MINER_DEVICE="auto"

# Max nonce iterations per block
export ANIMICA_MINER_MAX_NONCE="100000"

# Proxy configuration
export ANIMICA_PROXY_MAX_RETRIES="3"
export ANIMICA_PROXY_RETRY_DELAY_MS="1000"
```

---

## Getting Help

If you encounter issues not covered here:

1. **Check RPC logs**:
   ```bash
   tail -f ~/.animica/logs/rpc.log
   ```

2. **Enable verbose mode**:
   ```bash
   animica miner mine-blocks --address anim1... --count 5 --verbose
   ```

3. **Report issues**:
   - GitHub: https://github.com/animicaorg/all/issues
   - Include: error messages, logs, command used
   - Specify: network (mainnet/testnet/devnet), version

4. **Community support**:
   - Discord: [Animica Community](https://discord.gg/animica)
   - Forum: [Animica Forum](https://forum.animica.org)

---

## Version History

- **v0.2.0** (2025-01): Mining rewards and balance accrual guide
  - Added comprehensive section on balance accrual troubleshooting
  - Documented address parsing fix (32-byte vs 34-byte)
  - Added common pitfalls and verification steps
  - Clarified reward details and balance vs reward reporting

- **v0.1.0** (2024-12): Initial troubleshooting guide
  - Fixed device parameter RPC error (-32602)
  - Improved theta scaling for high network load
  - Enhanced error handling and fallback logic
