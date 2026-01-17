# Fix for "No mining job received from server" Error

## Problem Description

When miners connect to a Stratum server, they would sometimes fail with the error:
```
Waiting for mining job...
Error: No mining job received from server
[client] rx loop error: eof
```

This occurred when:
1. The Stratum bridge starts with a placeholder address (`anim1placeholder`)
2. The bridge cannot fetch a valid template because the node rejects the placeholder address
3. A miner connects, subscribes, and authorizes with a valid address
4. The authorization triggers template fetching and job publishing
5. However, the miner times out waiting for the job because of a race condition

## Root Cause

There were two issues:

### Issue 1: Race Condition in Authorization Handler

In `mining/stratum_server.py`, the authorize method was sending the authorization response **before** calling the `authorize_hook`:

```python
# OLD CODE (BUGGY)
await self._send(session, res_authorize(id_val, True))  # Send response first
...
if self._authorize_hook is not None:
    await self._authorize_hook(...)  # Call hook after
```

This caused a race where:
1. Client sends AUTHORIZE request
2. Server immediately sends AUTHORIZE response (before fetching job)
3. Client receives response and starts waiting for job
4. Server's authorize_hook runs (fetches template, publishes job)
5. Job NOTIFY is sent, but client may have already timed out

### Issue 2: Lack of Retry Logic

When the bridge's initial template fetch failed (due to placeholder address), there was no retry logic when a miner authorized with a valid address. The template fetch might fail on first attempt, leaving no job to publish.

## Solution

### Fix 1: Call authorize_hook Before Sending Response

Moved the `authorize_hook` call to **before** sending the authorization response:

```python
# NEW CODE (FIXED)
if self._authorize_hook is not None:
    await self._authorize_hook(...)  # Call hook first (fetches job)

await self._send(session, res_authorize(id_val, True))  # Send response after
```

This ensures:
1. Client sends AUTHORIZE request
2. Server's authorize_hook runs (fetches template, publishes job, sends NOTIFY)
3. Server sends AUTHORIZE response
4. Due to TCP ordering, NOTIFY arrives before AUTHORIZE response
5. Client's rx loop processes NOTIFY and sets `last_job`
6. Client receives AUTHORIZE response
7. Client's wait loop immediately finds job is available

### Fix 2: Add Retry Logic in authorize_hook

Added retry logic in the bridge's `authorize_hook` to handle template fetch failures:

```python
async def authorize_hook(session, worker, address):
    if is_valid_animica_address(address):
        await bridge.set_payout_address(address)
        
        # Retry up to 3 times with 0.5s delay
        for attempt in range(3):
            job_dict = await bridge.get_current_job()
            if job_dict:
                await server.publish_job(job_dict)
                break
            else:
                await asyncio.sleep(0.5)
                await bridge._poll_template()
```

### Fix 3: Improved Logging

Enhanced logging throughout the bridge to help diagnose issues:
- Log template fetch attempts and failures with reasons
- Log template details (height, parent hash) when available
- Log when mining is not enabled and why
- Log when templates are successfully fetched and jobs published

## Testing

Created comprehensive tests in `mining/tests/test_stratum_authorize_job_race.py`:

1. **test_client_receives_job_after_authorization**: Verifies job is received after authorization
2. **test_client_waits_for_job_like_cli**: Simulates the exact CLI wait loop scenario
3. **test_authorize_hook_runs_before_response**: Verifies timing of hook execution

All tests pass, confirming the fix works correctly.

## Files Changed

1. `mining/stratum_server.py`:
   - Moved `authorize_hook` call before sending response in the AUTHORIZE handler

2. `mining/stratum_bridge.py`:
   - Added retry logic in `authorize_hook` using configurable constants
   - Improved logging in `_poll_template` method
   - Enhanced initial template fetch logging

3. `mining/tests/test_stratum_authorize_job_race.py`:
   - New test file with 3 comprehensive tests

4. `docs/FIX_STRATUM_JOB_RACE.md`:
   - Documentation of the issue and fix

## Verification

To verify the fix works:

1. Start a node:
   ```bash
   animica node up
   ```

2. Start stratum bridge:
   ```bash
   animica stratum up
   ```

3. Connect a miner with valid address:
   ```bash
   animica miner stratum --address anim1... --url stratum+tcp://127.0.0.1:3333 --count 1
   ```

Expected output:
```
✓ Connected to 127.0.0.1:3333
✓ Subscribed (session: ...)
✓ Authorized
✓ Received initial job
Mining started...
```

The miner should receive a job immediately and start mining without the "No mining job received" error.
