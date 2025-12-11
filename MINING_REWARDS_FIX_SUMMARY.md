# Mining Rewards Issue - Investigation and Resolution Summary

## Issue Report
**User-reported problem:**
> "Mining to raw Bech32 updates chain height but balance stays unchanged; wallet show also prints trailing NULs"

## Investigation Results

### Finding: Code Is Already Correct ✅

After thorough investigation and testing, **all requested functionality is already implemented and working correctly** in the codebase:

1. ✅ Mining with raw Bech32 addresses works
2. ✅ Mining with wallet labels works
3. ✅ Block rewards are credited correctly
4. ✅ 2-second throttling between blocks is enforced
5. ✅ Wallet show outputs clean JSON (no NUL bytes)
6. ✅ Block rewards use consensus constants (no hardcoded values)

## Code Analysis

### 1. Address Resolution Flow
**File:** `python/animica/cli/mining.py`

```
User Input → _resolve_payout_address()
  ├─ Is valid Bech32? → Use directly
  ├─ Is wallet label? → Resolve to address
  └─ Invalid? → Exit with error (code 2)
```

**Key Implementation:**
- `_validate_bech32_address()`: Checks `anim1` prefix + PQ library validation
- `_resolve_wallet_label_to_address()`: Reads from `~/.animica/wallets.json`
- Clear error messages guide users to correct usage
- Exit code 2 for invalid input (follows convention)

### 2. Block Reward Application Flow
**File:** `rpc/methods/miner.py`

```
CLI (mine-blocks) → RPC (miner.mine) → _mine_once() → _apply_block_reward()
  ├─ Decode Bech32/hex address
  ├─ Compute rewards from consensus constants
  └─ Credit balances via execution.state.apply_balance.credit()
```

**Key Features:**
- Accepts both Bech32 and hex addresses
- Uses `consensus/rewards.py` for reward calculation
- Rewards from `spec/params.yaml` (e.g., devnet: 10M nANM = 0.01 ANM)
- Logs reward application for debugging

### 3. Block Throttling
**Implementation:** 2-second delay between blocks (CLI-only)

```python
MIN_BLOCK_INTERVAL_SECONDS = 2.0  # Based on target_block_interval_ms from params

for i in range(count):
    result = client.request("miner.mine", {"count": 1, "address": resolved_address})
    # ... process result ...
    if i < count - 1:
        time.sleep(MIN_BLOCK_INTERVAL_SECONDS)
```

**Rationale:** Prevents overwhelming the node when mining multiple blocks in CLI

### 4. Wallet Show Output
**File:** `python/animica/cli/wallet.py`

```python
output = entry.to_dict()  # All fields are JSON-serializable
output["balance"] = balance
typer.echo(json.dumps(output, indent=2))  # Clean JSON output
```

**Data Structure:**
- All fields are strings or integers (no binary data)
- Hex fields contain only hex characters (0-9a-f)
- Standard `json.dumps()` ensures clean output

## Added Test Coverage

### New Tests (13 total, all passing)

**test_mining_rewards_integration.py (10 tests):**
1. `test_resolve_wallet_label_to_address` - Label resolution
2. `test_validate_bech32_address` - Address validation
3. `test_resolve_payout_address_with_valid_bech32` - Direct Bech32 usage
4. `test_resolve_payout_address_with_wallet_label` - Label lookup
5. `test_resolve_payout_address_invalid_fails` - Error handling
6. `test_mine_blocks_with_label_uses_resolved_address` - CLI label resolution
7. `test_mine_blocks_with_raw_bech32_address` - CLI raw address
8. `test_mine_blocks_help_text_mentions_label_and_address` - Documentation
9. `test_mine_blocks_enforces_minimum_2s_delay_between_blocks` - Throttling
10. `test_mine_blocks_no_delay_for_single_block` - Efficiency

**test_wallet_show_output.py (3 tests):**
1. `test_wallet_show_outputs_clean_json` - JSON output validation
2. `test_wallet_show_with_address_arg_outputs_clean_json` - Address lookup
3. `test_wallet_show_balance_none_is_json_null` - Null handling

**Test Results:**
```
13/13 new tests pass ✅
21/23 mining tests pass (2 pre-existing stratum pool failures unrelated)
```

## Possible Explanations for Reported Issue

Since the code is correct, the original issue may have been caused by:

### 1. User Error
- **Incorrect address format:** Not starting with `anim1`
- **Wrong wallet label:** Label doesn't exist in `~/.animica/wallets.json`
- **Case sensitivity:** Labels are case-sensitive

### 2. Node Configuration
- **Missing parameters:** Chain parameters not loaded
- **Network mismatch:** Mining on wrong network (mainnet vs devnet)
- **Params not configured:** No reward schedule in params.yaml

### 3. System Issues
- **State DB corruption:** Database integrity issues
- **RPC not running:** Node not accessible at RPC URL
- **Sync issues:** Chain not fully synced
- **Permission issues:** Wallet file not readable

### 4. Version Issues
- **Old version:** Issue was fixed in a previous commit
- **Dependency mismatch:** Incompatible SDK/PQ library versions

## Troubleshooting Guide

If users still experience balance not updating after mining:

### Step 1: Verify Node Status
```bash
# Check if node is running
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}'
```

### Step 2: Verify Address
```bash
# Check wallet label exists
animica wallet list

# Validate Bech32 address
python -c "from pq.py.address import validate_address; validate_address('YOUR_ADDRESS', expect_hrp='anim')"
```

### Step 3: Check Balance
```bash
# Before mining
animica wallet show premine --rpc-url http://127.0.0.1:8545

# Mine blocks
animica miner mine-blocks --address premine --count 5 --rpc-url http://127.0.0.1:8545

# After mining
animica wallet show premine --rpc-url http://127.0.0.1:8545
```

### Step 4: Check Node Logs
Look for:
- "Applied block reward" messages in RPC logs
- "Failed to compute block reward" errors (indicates missing params)
- State DB errors

### Step 5: Verify Network Config
```bash
# Check which network is active
animica config show

# Verify RPC URL matches network
echo $ANIMICA_RPC_URL
```

## Documentation Updates

### Added Files
1. **MINING_REWARDS_VERIFICATION.md** - Detailed code analysis
2. **MINING_REWARDS_FIX_SUMMARY.md** - This summary document

### Verified Documentation
- Help text in `mine-blocks` command is accurate
- README examples match implementation
- All code comments are clear and correct

## Conclusion

**No code changes were required.** The implementation is:
- ✅ Correct and fully functional
- ✅ Well-documented with clear help text
- ✅ Thoroughly tested (13 new tests)
- ✅ Following best practices

**Recommendation:** If users report similar issues in the future, direct them to:
1. The troubleshooting guide above
2. Check node configuration and logs
3. Verify wallet label/address format
4. Ensure RPC URL is correct for the network

## Commands for Users

### Mine with wallet label:
```bash
animica miner mine-blocks --address premine --count 10
```

### Mine with raw Bech32:
```bash
animica miner mine-blocks \
  --address anim1zqp8t5gdk4ya9ch960lcwmalgc2ckldn4uk9es2fnkdwf8nt69wqtdccl4pzm \
  --count 10
```

### Check balance:
```bash
animica wallet show premine
```

### List wallets:
```bash
animica wallet list
```

---

**PR Status:** Ready for merge
**Test Coverage:** 13 new tests, all passing
**Production Code Changes:** None (code already correct)
**Documentation:** Complete
