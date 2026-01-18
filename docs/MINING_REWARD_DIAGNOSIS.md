# Mining Reward Diagnosis Guide

## Problem Statement

After mining height 1 to the premine address on mainnet (chain_id=0), the wallet balance stays at 81,000,000 ANM instead of increasing to 81,000,300 ANM (premine + 300 ANM block reward).

## Diagnosis Steps

### 1. Verify Block Contains Coinbase Transactions

```bash
# Check if mined block has coinbase txs
animica chain get-block --height 1 --format json | jq '.txs | length'
# Should be >= 1 (at least the coinbase tx)

# Check coinbase transaction details
animica chain get-block --height 1 --format json | jq '.txs[0].unsigned.kind'
# Should be "coinbase"
```

### 2. Check State DB Directly

```python
# Direct state DB query
from core.db.sqlite import SQLiteKV
from core.db.state_db import StateDB
from pq.py.address import decode_address

premine_addr = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
rec = decode_address(premine_addr)
digest = bytes(rec.digest)

kv = SQLiteKV("/root/.animica/chain-0/animica.db")
state_db = StateDB(kv)
balance = state_db.get_balance(digest)
print(f"Balance: {balance / 1e9:.9f} ANM")
```

### 3. Check Block Import Logs

Look for these log entries in node output:
- `"state: block contains N coinbase transaction(s); skipping separate reward application"`
- `"state: block does NOT contain coinbase transactions; applying rewards separately"`
- `"Applied block reward"` with height, address, amount

### 4. Verify Coinbase Address Encoding

```python
# Ensure consistent address encoding
from core.utils.address import address_to_bytes
from rpc.state_service import parse_address

addr_str = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"

# Genesis/reward path
addr1 = address_to_bytes(addr_str)
# Wallet query path  
addr2 = parse_address(addr_str)

print(f"Genesis: {addr1.hex()}")
print(f"Wallet:  {addr2.hex()}")
print(f"Match: {addr1 == addr2}")
# Should print "Match: True"
```

## Common Issues

### Issue A: Block Missing Coinbase Transactions

**Symptom:** Block at height 1 has 0 transactions  
**Cause:** `_build_coinbase_transactions()` failed or wasn't called  
**Fix:** Check RPC logs for errors in coinbase tx creation

### Issue B: Wrong Address Format

**Symptom:** Address encoding mismatch between genesis and rewards  
**Cause:** Genesis uses full bech32 payload, rewards use digest only (or vice versa)  
**Fix:** Ensure `address_to_bytes()` returns 32-byte digest consistently

### Issue C: State Not Persisted

**Symptom:** Reward logged as "credited" but balance unchanged after node restart  
**Cause:** State DB commit issue or snapshot/revert problem  
**Fix:** Verify SQLite autocommit is enabled and no reverts after reward credit

### Issue D: Fork Choice Not Applying State

**Symptom:** Block imported but `_apply_block_state()` never called  
**Cause:** Empty `attached` list in reorg path  
**Fix:** Ensure fork_choice returns new block in `attached` for normal extensions

## Resolution Checklist

- [ ] Verify block contains coinbase transaction
- [ ] Confirm coinbase tx has correct recipient address (32-byte digest)
- [ ] Check block import logs show reward application
- [ ] Verify state DB has updated balance after block import
- [ ] Test wallet query uses same address format as state DB key
- [ ] Confirm SQLite is in autocommit mode
- [ ] Verify `_apply_block_state()` is called for newly mined blocks

## Manual Test

```bash
# 1. Start fresh mainnet node
rm -rf /root/.animica/chain-0
animica node start --network mainnet

# 2. Check initial balance
animica wallet show <premine-address> --rpc-url http://127.0.0.1:8547
# Should show: 81,000,000.000000000 ANM

# 3. Mine one block
animica miner mine-blocks --address <premine-address> --count 1

# 4. Check balance again
animica wallet show <premine-address> --rpc-url http://127.0.0.1:8547
# Should show: 81,000,300.000000000 ANM (not 81,000,000)
```

## Expected vs Actual

| Height | Expected Balance | Observed (Bug) | Delta |
|--------|-----------------|----------------|-------|
| 0      | 81,000,000 ANM | 81,000,000 ANM | ✅ OK |
| 1      | 81,000,300 ANM | 81,000,000 ANM | ❌ Missing 300 ANM |
| 2      | 81,000,600 ANM | 81,000,000 ANM | ❌ Missing 600 ANM |
