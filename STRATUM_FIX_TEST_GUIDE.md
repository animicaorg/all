# Manual Testing Guide for Stratum Mining Fix

This guide explains how to manually test the fix for the Stratum mining job reception issue.

## Issue Being Fixed

Previously, when starting a Stratum bridge with `animica stratum up`, miners connecting with `animica miner stratum` would never receive jobs because the bridge was started with a placeholder address.

## Test Scenario

We'll simulate the real-world usage:
1. Start a Stratum bridge server (with placeholder address)
2. Connect a miner with a valid address
3. Verify the miner receives a job immediately after authorization

## Prerequisites

- Python 3.12+ with asyncio support
- pytest and pytest-asyncio installed
- The animica repository with the fix applied

## Automated Test

Run the provided test suite:

```bash
cd /home/runner/work/all/all
python -m pytest test_stratum_address_update.py -xvs
```

Expected output:
```
test_stratum_address_update.py::test_bridge_updates_address_on_miner_authorization PASSED
test_stratum_address_update.py::test_end_to_end_miner_receives_job_after_authorization PASSED
```

## Manual Integration Test

If you have a running Animica node, you can test the real scenario:

### Step 1: Start the Stratum bridge

```bash
# In terminal 1
animica stratum up --no-daemon
```

Expected output:
```
Starting Stratum server in foreground (press Ctrl+C to stop)
Server URL: stratum+tcp://127.0.0.1:3333
RPC URL: http://127.0.0.1:8545
Note: Payout address will be set by connecting miners
Stratum bridge listening on 127.0.0.1:3333
```

### Step 2: Connect a miner with a valid address

```bash
# In terminal 2
animica miner stratum \
  --address anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq3j5kq \
  --url stratum+tcp://127.0.0.1:3333 \
  --count 1
```

Expected output:
```
Connecting to Stratum server: 127.0.0.1:3333
Payout address: anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq3j5kq
Mining threads: 1
Target blocks: 1

✓ Connected to 127.0.0.1:3333
✓ Subscribed (session: ...)
✓ Authorized
✓ Received initial job  <-- This should appear immediately
```

### What Should Happen

1. **Before the fix**: Miner would wait 10 seconds and show "Error: No mining job received from server"
2. **After the fix**: Miner receives a job within ~100ms of authorization

### Terminal 1 Logs

After the miner connects, you should see in the bridge logs:

```
[Stratum] authorize worker=test_miner address=anim1qqq... session=...
Miner authorized with address anim1qqq..., updating bridge payout address
Updated payout address: anim1placeholder -> anim1qqq...
New template: job=... height=... parent=...
Published job ... to miner test_miner after address update
```

## Troubleshooting

### Miner still doesn't receive job

1. Check that the node RPC is accessible: `curl http://127.0.0.1:8545/rpc -d '{"jsonrpc":"2.0","method":"chain.getHead","params":[],"id":1}'`
2. Check bridge logs for template fetch errors
3. Verify the miner address is valid Bech32 format (starts with "anim1")

### Bridge won't start

1. Check if another process is using port 3333: `lsof -i :3333`
2. Try a different port: `animica stratum up --port 9999`

## Cleanup

After testing, stop the bridge:

```bash
# If running in foreground: Ctrl+C in terminal 1
# If running as daemon:
animica stratum down
```

## Verification

The fix is working correctly if:
- ✓ Miner receives job immediately after authorization (< 1 second)
- ✓ No "Error: No mining job received from server" message
- ✓ Bridge logs show address update and job publication
- ✓ Automated tests pass
