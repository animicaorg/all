# Stratum Job Format Documentation

## Overview

This document describes the canonical job format used by the Animica Stratum pool protocol. Miners must receive jobs in this exact format to successfully hash and submit shares.

## Job Structure

When a pool server broadcasts a job via the `mining.notify` method, miners receive the following structure:

```json
{
  "jobId": "unique-job-id",
  "height": 100,
  "cleanJobs": true,
  "shareTarget": 0.01,
  "header": {
    "signBytes": "0xaa...aa",
    "mixSeed": "0xbb...bb",
    "number": 100,
    "target": "0xff...ff",
    "chainId": 1,
    "parentHash": "0x11...11",
    "stateRoot": "0x00...00",
    "thetaMicro": 800000,
    ...
  },
  "hints": {
    "mixSeed": "0xbb...bb"
  }
}
```

## Critical Fields

### Required for Hashing

Miners **must** have these fields to compute valid shares:

#### 1. `header.signBytes` (string, hex-encoded)

The canonical serialized block header bytes (excluding nonce) that miners hash with nonce variations.

- **Format**: `"0x"` + 160 hex chars (80 bytes)
- **Purpose**: The deterministic preimage for PoW hashing
- **Derivation**: Computed by node from current block template fields
- **Example**: `"0xaaaaaaaaaaaaaaaa..."`

**Critical**: Without this field, miners will log:
```
Warning: No signBytes in job header, skipping batch
```

#### 2. `header.mixSeed` (string, hex-encoded)

The mix seed for nonce domain evolution.

- **Format**: `"0x"` + 64 hex chars (32 bytes)
- **Purpose**: Binds the nonce search domain across blocks
- **Derivation**: Evolved from parent mixSeed + randomness beacon + chainId
- **Example**: `"0xbbbbbbbbbbbbbbbb..."`

**Note**: mixSeed is **also** present in `hints.mixSeed` for backward compatibility, but miners expect it in the `header` object.

#### 3. `height` (integer, top-level)

The block height being mined.

- **Format**: Non-negative integer
- **Purpose**: Displayed to users, used in share submission
- **Example**: `100`

**Critical**: Without this field at the top level, miners will display:
```
→ New job: ... (height ?)
```

The height is **also** present as `header.number` for header completeness.

### Mining Parameters

#### 4. `shareTarget` (float)

The difficulty ratio for share acceptance.

- **Format**: Float between 0.0 and 1.0
- **Purpose**: Defines share acceptance threshold relative to block difficulty
- **Example**: `0.01` (1% of block difficulty)

#### 5. `header.target` (string, hex-encoded)

The full 256-bit block target.

- **Format**: `"0x"` + 64 hex chars
- **Purpose**: Block difficulty target for PoW validation
- **Example**: `"0xffffffffffffff..."`

#### 6. `header.thetaMicro` (integer)

The current theta (Θ) difficulty in micro-nats.

- **Format**: Positive integer (µ-nats)
- **Purpose**: Acceptance threshold for PoIES consensus
- **Example**: `800000` (0.8 nats)

## Hash Computation

Miners compute the PoW hash as:

```python
digest = sha3_256(signBytes || mixSeed || nonce_le8)
```

Where:
- `signBytes`: 80 bytes from `header.signBytes`
- `mixSeed`: 32 bytes from `header.mixSeed`
- `nonce_le8`: 8-byte nonce in little-endian format

If `digest_int < share_target_256`:
  → Valid share, submit to pool

If `digest_int < block_target_256`:
  → Valid block, propagate to network

## Pool Implementation Notes

### Node RPC → Pool Adapter

The node's `miner.getWork` RPC returns:

```json
{
  "jobId": "...",
  "signBytes": "0x...",  // Top-level
  "header": { ... },     // Does NOT contain signBytes initially
  "hints": { "mixSeed": "0x..." },
  "height": 100,
  "target": "0x...",
  ...
}
```

### Pool Adapter → Stratum Server

The pool adapter (`MiningCoreAdapter`) extracts:

```python
sign_bytes = work.get("signBytes")  # From top level
height = work.get("height")
hints = work.get("hints", {})
```

Creates `MiningJob` with:
```python
MiningJob(
    sign_bytes=sign_bytes,  # Stored at MiningJob level
    hints=hints,            # Stored at MiningJob level
    height=height,
    ...
)
```

### Stratum Server → Miner

The stratum server (`StratumPoolServer._on_new_job`) constructs the final job:

```python
header = dict(job.header or {})

# Add signBytes to header (required)
if job.sign_bytes:
    header.setdefault("signBytes", job.sign_bytes)

# Add mixSeed to header (required) 
if job.hints and "mixSeed" in job.hints:
    header.setdefault("mixSeed", job.hints["mixSeed"])

# Add height to header
if job.height:
    header.setdefault("number", job.height)
```

Then broadcasts via `push_notify`:

```python
msg = push_notify(
    job_id=job.job_id,
    header=header,      # Now contains signBytes AND mixSeed
    share_target=job.share_target,
    height=job.height,  # At top level
    hints=job.hints,    # Also sent for compatibility
    clean_jobs=True,
)
```

## Backward Compatibility

For compatibility with different miner implementations:

1. **mixSeed**: Present in both `header.mixSeed` AND `hints.mixSeed`
2. **height**: Present in both top-level `height` AND `header.number`
3. **signBytes**: Present in both `header.signBytes` AND as `StratumJob.sign_bytes`

## Validation

Miners **must** validate:

1. `header.signBytes` is present and parseable as hex
2. `header.mixSeed` is present and parseable as hex (32 bytes)
3. `height` is present and is a non-negative integer
4. `shareTarget` is present and is a float > 0

If any validation fails, the miner should:
- Log a clear error message showing which field is missing
- Skip the batch (do not attempt to hash)
- Wait for the next job or reconnect

## Testing

To verify job format compatibility:

1. Start pool with stratum server
2. Connect miner via stratum protocol
3. Verify miner receives job and starts hashing immediately
4. Check miner logs show correct height (not "?")
5. Submit shares and verify pool accepts them

Example test script: `test_signbytes_fix_verification.py`

## Changes Log

### 2026-01-17: signBytes and mixSeed Format Fix

**Problem**: Miners received jobs but logged "No signBytes in job header" and displayed "height ?" because:
1. `mixSeed` was only in `hints`, not in `header` where miners expected it
2. `height` was only in `header.number`, not at top level where miners looked for it

**Solution**:
1. Added `mixSeed` to `header` dict from `hints` in `StratumPoolServer._on_new_job`
2. Added `height` parameter to `push_notify` protocol function
3. Updated `stratum_server._broadcast_job` to pass `height` to `push_notify`

**Files Changed**:
- `python/animica/stratum_pool/stratum_server.py`: Add mixSeed to header
- `mining/stratum_protocol.py`: Add height parameter to push_notify
- `mining/stratum_server.py`: Pass height to push_notify

**Result**: Miners now receive jobs in correct format and start hashing immediately.
