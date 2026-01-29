# User Guide: Node Robustness Features

## Overview

The wallet now handles node startup issues gracefully, allowing you to continue using wallet features even when the embedded node has problems.

## What Changed?

### Previous Behavior
- If P2P failed to connect, wallet would stop the node
- Log spam could freeze the UI
- No clear indication of what was wrong
- No easy way to recover

### New Behavior
- Node stays running even if P2P has issues
- Log spam is automatically collapsed
- Clear warning banner shows what's wrong
- Easy recovery actions available

## Node States Explained

### 🟢 Healthy
**What it means:** Everything is working normally
- Node process is running
- RPC is responding
- No issues detected in logs

**What you can do:**
- Use all wallet features
- Send/receive transactions
- Check balances

### 🟡 Degraded
**What it means:** Node is running but has some issues
- RPC is still working for basic operations
- P2P sync may be broken
- Some background errors detected

**What you can do:**
- ✅ Use local wallet features
- ✅ View balances
- ✅ Send transactions (if already synced)
- ❌ May not sync new blocks

**Recovery options:** See [Recovery Actions](#recovery-actions) below

### 🔴 Error
**What it means:** Node failed to start or crashed
- Process is not running
- RPC is unavailable

**What you can do:**
- Click "Restart Node" to try again
- Check diagnostics for error details
- Try "Reset Local Node Data" if persistent

## Recovery Actions

### 1. Open Node Logs

**When to use:** Want to see detailed error messages

**What it does:**
- Opens your system file manager
- Shows the logs directory
- You can view log files with any text editor

**Location:**
- macOS: `~/Library/Application Support/Animica/logs/`
- Linux: `~/.config/Animica/logs/`
- Windows: `%APPDATA%\Animica\logs\`

### 2. Reset Local Node Data

**When to use:** 
- Database corruption detected
- "missing head_hash" errors
- Node stuck in degraded state

**What it does:**
- Stops the node if running
- Deletes all blockchain data
- Forces a fresh sync from scratch

**⚠️ Warning:**
- You will need to re-sync the entire blockchain
- Your wallet keys are NOT affected
- Your balances will reappear after sync

**Steps:**
1. Click "Reset Local Node Data"
2. Confirm in the dialog box
3. Wait for deletion to complete
4. Click "Start Node"
5. Wait for sync (may take several minutes)

### 3. Copy Diagnostics

**When to use:** Reporting an issue or getting support

**What it does:**
- Collects system information
- Includes recent log lines
- Copies to clipboard

**Contains:**
- Wallet version
- Node state and info
- System paths
- Last 20 log lines

**Usage:**
1. Click "Copy Diagnostics"
2. Paste into support ticket or issue report
3. Personal data (addresses, keys) is NOT included

## Understanding Log Messages

### Normal Messages ✅
```
INFO: Starting RPC server on 127.0.0.1:8548
INFO: Node is ready
INFO: Synced to block 1234
```
These are fine, node is working.

### Warning Messages ⚠️
```
WARN: No peers available
WARN: Seed connection failed
```
These are usually not critical. Node can work without peers if already synced.

### Degraded Messages 🟡
```
sync: reset cursor due to missing head_hash in db (repeated 15 times)
UnboundLocalError: cannot access local variable 'asyncio'
TypeError: '>=' not supported between instances of 'NoneType' and 'int'
```
These trigger the degraded state. See recovery actions.

## Common Scenarios

### Scenario: "Node is degraded" banner appears

**Cause:** Node detected a known issue in logs

**Solution:**
1. Check the reason shown in banner
2. If "missing head_hash" → Try "Reset Local Node Data"
3. If Python error → Node has a bug (report to developers)
4. You can continue using wallet for local operations

### Scenario: Node won't start

**Symptoms:**
- State stuck at "Starting..."
- After 30 seconds: "Running (Degraded)"

**Solutions:**
1. Wait 1-2 minutes (node may still be initializing)
2. Check "Open Node Logs" for errors
3. Try "Restart Node"
4. If persists, try "Reset Local Node Data"

### Scenario: Repeated restarts

**Symptoms:**
- Node starts and stops repeatedly
- Getting slower each time

**Explanation:**
- Automatic restart backoff is working
- Delays increase: 1s, 2s, 4s, 8s, 16s, 32s, 60s (max)

**Solutions:**
1. Let it stabilize (usually succeeds after 2-3 tries)
2. Check logs for the root cause
3. If Python is missing, install Python 3.11+

### Scenario: "Connection refused" in logs

**Symptoms:**
```
WARN: Connection refused when dialing seed
```

**Explanation:**
- Seed nodes may be offline
- Firewall may be blocking
- This is NOT marked as degraded

**Solutions:**
- If you have other peers, ignore it
- Check firewall settings
- Verify internet connection

## Technical Details

### Health Check Process

1. **Phase 1 (0-30s):** Fast checks every 250ms
   - Trying to reach RPC endpoint
   - Checking if chain head is readable
   
2. **Phase 2 (30s+):** Slow checks every 2 seconds
   - Still trying to connect
   - Node marked as degraded but keeps trying
   
3. **Success:** When chain head responds
   - Transitions to "RPC Ready"
   - Then "Healthy" if no issues

### Log Deduplication

Repeated log lines are collapsed:
```
Before:
sync: reset cursor...
sync: reset cursor...
sync: reset cursor...
[100 more times]

After:
sync: reset cursor... (repeated 103 times)
```

**Window:** 2 seconds
**Buffer:** Last 5000 lines kept in memory

### Restart Backoff

Prevents rapid restart loops:

| Attempt | Delay |
|---------|-------|
| 1 | ~1 second |
| 2 | ~2 seconds |
| 3 | ~4 seconds |
| 4 | ~8 seconds |
| 5 | ~16 seconds |
| 6 | ~32 seconds |
| 7+ | ~60 seconds (max) |

Jitter (±20%) prevents thundering herd.

## Frequently Asked Questions

**Q: Why does it say "Degraded" but I can still use the wallet?**

A: The node's RPC interface (used by wallet) still works even if background sync is broken. You can view balances and send transactions with local data.

**Q: Will "Reset Local Node Data" delete my wallet/keys?**

A: No. Only blockchain data is deleted. Your keys, addresses, and wallet database are separate and safe.

**Q: How long does it take to re-sync after reset?**

A: Depends on network speed and blockchain size. Usually 5-30 minutes for devnet, longer for mainnet.

**Q: Can I use a remote node instead of embedded?**

A: Not yet in this version. Future enhancement planned.

**Q: The banner is annoying, can I hide it?**

A: The banner only shows when the node is actually degraded. Fix the underlying issue or restart the node to clear it.

**Q: What if node enters degraded state repeatedly?**

A: This indicates a persistent issue:
1. Check if Python 3.11+ is installed
2. Try "Reset Local Node Data"
3. Check system logs for Python errors
4. Report issue with diagnostics to developers

## Reporting Issues

When reporting node issues, include:

1. **Steps to reproduce:**
   - What you did before the issue
   - Which buttons you clicked
   
2. **Diagnostics:**
   - Click "Copy Diagnostics"
   - Paste into issue report
   
3. **Screenshots:**
   - Capture the degraded banner
   - Capture any error dialogs
   
4. **System info:**
   - OS version (macOS/Linux/Windows)
   - Wallet version (shown in About)
   - Network (devnet/testnet/mainnet)

## Additional Resources

- [Node State Machine](NODE_STATE_MACHINE.md) - Technical state transitions
- [Implementation Details](NODE_ROBUSTNESS_IMPLEMENTATION.md) - Developer documentation
- [GitHub Issues](https://github.com/animicaorg/all/issues) - Report bugs
