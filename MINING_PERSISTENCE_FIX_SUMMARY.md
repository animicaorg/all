# Mining, Persistence, and Difficulty Fixes

This document summarizes the fixes applied to address persistence, mining payouts, and block difficulty issues in Animica.

## Issues Addressed

### 1. Persistence: Data Not Saved Under `~/.animica`

**Problem:** No data was being saved under `~/.animica` because parent directories were not created automatically.

**Solution:**
- Modified `rpc/config.py` to create parent directories when expanding database URIs
- The `_expand_sqlite_uri()` function now calls `Path.mkdir(parents=True, exist_ok=True)` for all file-based database paths
- Verified that wallet and key storage already created directories correctly

**Files Changed:**
- `rpc/config.py`: Enhanced `_expand_sqlite_uri()` to create parent directories

**Testing:**
- Added `rpc/tests/test_persistence.py` with tests for directory creation
- Manual tests in `test_mining_manual.py` verify persistence works

### 2. Mining Payouts Not Working

**Problem:** Mining payouts were correctly implemented in `_apply_block_reward()` but the RPC method signature needed to be updated to accept address parameters.

**Solution:**
- The `miner.mine` RPC method already accepts an `address` parameter (added in previous work)
- The `_mine_once()` function correctly passes `payout_address` to `_apply_block_reward()`
- Block rewards are credited to:
  1. Custom payout address if specified via `--address` parameter
  2. `ANIMICA_MINER_ADDRESS` environment variable if set
  3. First premine address from `MAINNET_PREMINE_DISTRIBUTION` as fallback
  4. Zero address as last resort (with warning)

**Files Changed:**
- `rpc/methods/miner.py`: Already had payout logic; verified it works correctly
- `python/animica/cli/mining.py`: Enhanced documentation for `mine-blocks` command

**Testing:**
- Existing tests in `rpc/tests/test_miner_reward.py` verify payouts work
- Tests include: default address, custom address, env variable, invalid address fallback

### 3. Block Difficulty: Instant Blocks Without Target Enforcement

**Problem:** The `_mine_once()` function created blocks with `nonce=0` and submitted them immediately without checking if they met the difficulty target.

**Solution:**
- Completely rewrote `_mine_once()` to perform actual proof-of-work mining
- Mining now:
  1. Computes difficulty target from theta (acceptance threshold) parameter
  2. Iterates through nonces (0 to `ANIMICA_MINER_MAX_NONCE`)
  3. Computes block hash for each nonce
  4. Checks if `hash_int <= target`
  5. Submits block only when a valid nonce is found
- Target calculation uses `_theta_to_target()` which derives a 256-bit target from theta
- Default max nonce is 100,000 (configurable via `ANIMICA_MINER_MAX_NONCE` env var)

**Files Changed:**
- `rpc/methods/miner.py`: Rewrote `_mine_once()` to perform actual mining with nonce iteration

**Testing:**
- Manual tests in `test_mining_manual.py` verify:
  - Target calculation from theta
  - Nonce iteration finds valid hashes
  - Max nonce env variable is respected

## Configuration

### Environment Variables

**Persistence:**
- `ANIMICA_RPC_DB_URI`: Database location (default: `sqlite:///~/.animica/chain-{chain_id}/animica.db`)
- Directories are created automatically under `~/.animica/`

**Mining Payouts:**
- `ANIMICA_MINER_ADDRESS`: Default payout address (bech32 or hex format)
- `--address` CLI parameter: Override payout address for specific mining operation

**Mining Difficulty:**
- `ANIMICA_MINER_MAX_NONCE`: Maximum nonce iterations per block (default: 100000)
- Higher values allow mining harder blocks but take longer
- Lower values speed up tests but may fail on high-difficulty blocks

### Default Paths

The system uses chain-specific directories to isolate data by network:
- **Mainnet (chain_id=1)**: `~/.animica/chain-1/`
- **Testnet (chain_id=2)**: `~/.animica/chain-2/`
- **Devnet (chain_id=1337)**: `~/.animica/chain-1337/`

Each directory contains:
- `animica.db`: Chain state database (SQLite by default)
- Block data and transaction index
- State snapshots and receipts

Wallet and key data are stored separately:
- **Wallets**: `~/.animica/wallets.json` (or via `ANIMICA_WALLET_FILE`)
- **Keys**: `~/.animica/keys/` (or via `--key-dir` parameter)

## Usage Examples

### Mining with Custom Payout Address

```bash
# Mine 5 blocks to a bech32 address
animica miner mine-blocks --address anim1test123 --count 5

# Mine to a hex address
animica miner mine-blocks --address 0xabcd...1234 --count 10

# Mine with custom RPC endpoint
animica miner mine-blocks --address anim1test123 --count 10 --rpc-url http://localhost:8545
```

### Setting Default Miner Address

```bash
# Set default miner address via environment variable
export ANIMICA_MINER_ADDRESS=anim1test123

# Mine without --address (uses default)
animica miner mine-blocks --count 5
```

### Adjusting Mining Difficulty

```bash
# Reduce max nonce for faster tests (may fail on hard blocks)
export ANIMICA_MINER_MAX_NONCE=10000
animica miner mine-blocks --address anim1test123 --count 1

# Increase max nonce for very hard blocks
export ANIMICA_MINER_MAX_NONCE=1000000
animica miner mine-blocks --address anim1test123 --count 1
```

### Custom Database Location

```bash
# Use a custom database location
export ANIMICA_RPC_DB_URI=sqlite:///var/lib/animica/custom.db

# Or use RocksDB
export ANIMICA_RPC_DB_URI=rocksdb:///var/lib/animica/rocks_data

# Start the RPC server (directory will be created automatically)
python -m rpc
```

## Technical Details

### Mining Algorithm

The mining process implements a proof-of-work system similar to Bitcoin but adapted for Animica's PoIES consensus:

1. **Target Calculation**: The target is derived from theta (θ) in micro-nats:
   ```python
   max_target = (1 << 256) - 1
   base = max_target * 0.01  # 1% of search space
   target = base / (theta_micro / 1_000_000)
   ```

2. **Nonce Iteration**: For each nonce value:
   ```python
   header = Header(..., nonce=nonce_val)
   block_hash = header.hash()  # SHA3-256
   if int.from_bytes(block_hash, "big") <= target:
       submit_block(header)
   ```

3. **Difficulty Adjustment**: The theta parameter adjusts automatically based on block time:
   - Faster blocks → higher theta → lower target → harder mining
   - Slower blocks → lower theta → higher target → easier mining

### Reward Application

Block rewards are applied in `_apply_block_reward()`:

1. Determine payout address (priority order):
   - Custom address from `address` parameter
   - `ANIMICA_MINER_ADDRESS` environment variable
   - First premine address from distribution
   - Zero address (fallback with warning)

2. Compute block reward from `consensus.rewards.compute_block_reward()`
   - Returns list of `(address, amount)` tuples
   - Typically one entry for the block miner

3. Credit reward to state using `execution.state.apply_balance.credit()`
   - Updates state database
   - Changes persist to disk automatically

## Testing

### Manual Tests

Run the manual test script to verify all fixes:

```bash
python3 test_mining_manual.py
```

This tests:
- Persistence directory creation
- Mining target calculation
- Nonce iteration and hash validation
- Miner address resolution
- Environment variable handling

### Unit Tests

Run existing unit tests (requires pytest and dependencies):

```bash
# Test persistence
pytest rpc/tests/test_persistence.py -v

# Test mining rewards
pytest rpc/tests/test_miner_reward.py -v

# Test mining methods
pytest rpc/tests/test_miner_methods.py -v
```

## Compatibility

These changes are backward compatible:

- **Persistence**: Existing database files work; directories are created only if missing
- **Mining Payouts**: Old clients can still mine without specifying address (uses default)
- **Block Difficulty**: The `ANIMICA_MINER_MAX_NONCE` env var ensures tests don't hang on hard blocks

## Future Improvements

Potential enhancements for future work:

1. **Parallel Mining**: Use multiple threads/processes to search nonce space faster
2. **GPU Mining**: Implement GPU-accelerated hash search for production mining
3. **Mining Pools**: Support for pool mining with share submissions
4. **Difficulty Prediction**: Display estimated time to mine based on current theta
5. **Persistence Monitoring**: Add metrics for database size and I/O performance

## Related Files

### Core Implementation
- `rpc/config.py`: Configuration and DB URI handling
- `rpc/methods/miner.py`: Mining RPC methods and proof-of-work logic
- `python/animica/cli/mining.py`: CLI commands for mining

### Tests
- `rpc/tests/test_persistence.py`: Persistence directory creation tests
- `rpc/tests/test_miner_reward.py`: Mining payout tests
- `rpc/tests/test_miner_methods.py`: Mining RPC method tests
- `test_mining_manual.py`: Manual integration tests

### Related Modules
- `consensus/difficulty.py`: Theta retargeting and target calculation
- `consensus/rewards.py`: Block reward computation
- `execution/state/apply_balance.py`: Balance credit/debit functions
- `core/db/`: Database backend implementations
