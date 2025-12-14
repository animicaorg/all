# Verification Guide: Transaction Lifecycle Fixes for chainId=2

This guide documents how to manually verify that the transaction lifecycle fixes work correctly on testnet (chainId=2).

## Prerequisites

1. Running testnet node on chainId=2, port 18546
2. Wallet configured with test addresses
3. Python environment with animica CLI installed

## Quick Verification Steps

### 1. Check Feature Detection (No liboqs Warning)

```bash
# This should NOT emit liboqs warnings anymore
python3 -c "from pq.py import sign; print('Import successful, no warnings')"
```

**Expected**: No "liboqs-python faulthandler" warning appears.

### 2. Send Transaction and Verify Mempool

```bash
# Send a transaction
animica tx send \
  --from anim1zqqsw6mr86yqnee42p6ds9e22y5ye6mquq5cthxump2fmxgx5e9s7fsuugat5 \
  --to anim1zqqmgcs5auklzpk8yd2d6k4dsh5pcxlcuqyx3r84dj4230uktcmzwesv0nsuj \
  --value 1 \
  --chain-id 2 \
  --rpc-url http://127.0.0.1:18546/rpc \
  -v

# Note the transaction hash from output
# Example: 0xabc123...

# Check mempool
curl -X POST http://127.0.0.1:18546/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"mempool.getPending","params":[]}'
```

**Expected**:
- Transaction submitted successfully
- Transaction hash appears in mempool.getPending

### 3. Verify Full Transaction Fields

```bash
# Get transaction by hash (replace with your tx hash)
curl -X POST http://127.0.0.1:18546/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0",
    "id":1,
    "method":"tx.getTransactionByHash",
    "params":["0xYOUR_TX_HASH_HERE"]
  }'
```

**Expected**:
- Returns full transaction object
- NOT just `{hash, value:0}`
- Has fields: hash, from, to, nonce, gas, value, chainId

### 4. Mine Block and Verify Inclusion

```bash
# Mine 1 block
curl -X POST http://127.0.0.1:18546/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"miner.mine","params":[1]}'

# Get the mined block (use height from mine result)
curl -X POST http://127.0.0.1:18546/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0",
    "id":1,
    "method":"chain.getBlockByNumber",
    "params":[HEIGHT, false, false]
  }'
```

**Expected**:
- Block contains transaction hash in `transactions` array
- NOT `[null]`
- txsRoot is non-zero (not `0x0000...`)

### 5. Verify State Updates

```bash
# Check nonce incremented
curl -X POST http://127.0.0.1:18546/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0",
    "id":1,
    "method":"state.getNonce",
    "params":["anim1zqqsw6mr86yqnee42p6ds9e22y5ye6mquq5cthxump2fmxgx5e9s7fsuugat5"]
  }'

# Check recipient balance increased
curl -X POST http://127.0.0.1:18546/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0",
    "id":1,
    "method":"state.getBalance",
    "params":["anim1zqqmgcs5auklzpk8yd2d6k4dsh5pcxlcuqyx3r84dj4230uktcmzwesv0nsuj"]
  }'
```

**Expected**:
- Sender nonce incremented (was 0, now 1)
- Recipient balance increased by transfer amount

### 6. Verify Mempool Cleared

```bash
# Check mempool after mining
curl -X POST http://127.0.0.1:18546/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"mempool.getPending","params":[]}'
```

**Expected**:
- Transaction no longer in pending pool
- Empty array or array without our tx hash

### 7. Test Pending Nonce (Back-to-Back Sends)

```bash
# Get initial pending nonce
curl -X POST http://127.0.0.1:18546/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0",
    "id":1,
    "method":"state.getPendingNonce",
    "params":["anim1zqqsw6mr86yqnee42p6ds9e22y5ye6mquq5cthxump2fmxgx5e9s7fsuugat5"]
  }'

# Send first transaction (will use nonce from pending nonce)
animica tx send --from ADDR --to DEST --value 1 --chain-id 2 --rpc-url http://127.0.0.1:18546/rpc

# Send second transaction immediately (should use nonce+1)
animica tx send --from ADDR --to DEST --value 1 --chain-id 2 --rpc-url http://127.0.0.1:18546/rpc

# Check pending nonce again
curl -X POST http://127.0.0.1:18546/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0",
    "id":1,
    "method":"state.getPendingNonce",
    "params":["anim1zqqsw6mr86yqnee42p6ds9e22y5ye6mquq5cthxump2fmxgx5e9s7fsuugat5"]
  }'
```

**Expected**:
- First tx uses nonce N
- Second tx uses nonce N+1 (not N again!)
- Pending nonce returns N+2
- Both transactions have different hashes

## Success Criteria

✅ No liboqs warnings in normal CLI operations
✅ Transactions appear in mempool after submission
✅ tx.getTransactionByHash returns full fields (not stub)
✅ Mining includes transactions in blocks
✅ Block RPC returns tx hashes, never [null]
✅ txsRoot is non-zero when txs included
✅ Nonces increment after mining
✅ Balances update correctly (transfer + fees)
✅ Mempool clears after inclusion
✅ Back-to-back sends use incrementing pending nonces
✅ Different tx hashes for different nonces

## Troubleshooting

### Transaction Not in Mempool

Check:
- ChainId matches node (2 for testnet)
- Transaction signature is valid
- No RPC errors in node logs

### Transaction Not Included in Block

Check:
- Transaction actually in mempool before mining
- Node logs for "chainId mismatch" or "nonce gap" warnings
- Sender has sufficient balance for value + fees

### Nonce Not Incrementing

Check:
- Transaction was actually executed (check block inclusion)
- No transaction reverts (check receipt status)
- State DB is persisting correctly

### Back-to-Back Sends Use Same Nonce

Check:
- CLI is using state.getPendingNonce (not just state.getNonce)
- Node version includes pending nonce fix
- Mempool is tracking pending transactions correctly

## Automated Test

Run the integration test:

```bash
export TEST_TX_CHAINID2=1
pytest tests/integration/test_tx_chainid2_lifecycle.py -xvs
```

This test covers the complete lifecycle automatically.
