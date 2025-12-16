# Mining Troubleshooting Guide

This guide covers common issues when mining with Animica and how to resolve them.

⚠️ **Important**: As of the P2P-first update, mining uses local P2P validation by default (no proxy). See [P2P Sync Guide](p2p_sync.md) for P2P troubleshooting.

## Table of Contents

1. [RPC Parameter Errors](#rpc-parameter-errors)
2. [Device Selection Issues](#device-selection-issues)
3. [Theta Adjustment and Difficulty](#theta-adjustment-and-difficulty)
4. [Network Connectivity](#network-connectivity)
5. [Performance Issues](#performance-issues)

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
   curl https://rpc.animica.org/rpc
   ```

3. **Use custom RPC endpoint**:
   ```bash
   animica miner mine-blocks --address anim1... --count 5 \
     --rpc-url http://your-node:8545
   ```

4. **Check proxy configuration**:
   - Default trusted RPC: `https://rpc.animica.org/rpc`
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
export ANIMICA_TRUSTED_RPC_URL="https://rpc.animica.org/rpc"

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

- **v0.1.0** (2024-12): Initial troubleshooting guide
  - Fixed device parameter RPC error (-32602)
  - Improved theta scaling for high network load
  - Enhanced error handling and fallback logic
