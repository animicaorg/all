# Mining Pipeline Unification - Implementation Guide

## Overview

This document describes the unified mining pipeline implementation that ensures pool-based mining (`submitShare`) and CLI-based mining (`mine-blocks`) use the same canonical validation, state transition, and reward crediting paths.

## Problem Statement

### Before Implementation

1. **Missing RPC Methods:**
   - `chain.head` → -32601 Method not found
   - `chain.networkInfo` → -32601 Method not found

2. **Broken CLI Param Parsing:**
   - `animica rpc call state.getBalance '{"params":["addr"]}'` → -32602 Invalid params

3. **Divergent Mining Paths:**
   - `animica miner mine-blocks` → Full block production pipeline
   - Pool `submitShare` → Accepted shares but **never produced blocks**
   - Two separate code paths with different validation/crediting logic

### After Implementation

✓ All RPC methods work correctly
✓ CLI param parsing handles all formats
✓ **Single canonical mining pipeline** used by both pool and CLI miner

## Key Changes Summary

1. **RPC Aliases Added:** `chain.head`, `chain.networkInfo` 
2. **Param Parsing Fixed:** Handles `{"params": [...]}` wrapper format
3. **Mining Unified:** submitShare → submitBlock → BlockImporter (same as mine-blocks)
4. **Block Production:** Pool shares now produce real blocks when meeting network difficulty
5. **Rewards Credited:** Identical reward crediting logic for all mining paths

## Testing

Run the included tests to verify implementation:

```bash
# Test RPC param parsing
python test_rpc_param_parsing.py

# Test mining unification
python test_mining_pipeline_unification.py
```

Both tests should pass with ✓ status.

## Usage Examples

### RPC Method Usage

```bash
# Test chain.head alias (previously returned -32601)
animica rpc call chain.head

# Test chain.networkInfo (previously returned -32601)
animica rpc call chain.networkInfo

# Test wrapped params format (previously returned -32602)
animica rpc call state.getBalance '{"params":["anim1..."]}'
```

### Pool Mining

When a pool miner submits a share that meets network difficulty:

1. **Old Behavior:** Share marked as `isBlock=true` but not submitted to chain
2. **New Behavior:** Block automatically submitted through canonical path, height increments, rewards credited

## Architecture Diagram

See full architecture details in comments within the implementation files:
- `rpc/methods/miner.py` - submitShare with block submission logic
- `rpc/methods/chain.py` - New RPC aliases
- `python/animica/cli/rpc.py` - Fixed param parsing

## For More Details

See test files for validation of implementation:
- `test_mining_pipeline_unification.py` - Verifies all components
- `test_rpc_param_parsing.py` - Validates param handling
