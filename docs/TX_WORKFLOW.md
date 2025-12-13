# Transaction Workflow Guide

This guide explains how to submit transactions, verify they're in the mempool, mine blocks, and check balances in the Animica blockchain.

## Overview

The typical transaction workflow consists of:
1. Submit a signed transaction via RPC
2. Verify transaction is in the mempool (pending)
3. Mine a block to include the transaction
4. Verify transaction is in the block
5. Check updated balances

## Prerequisites

- Running Animica node (local devnet recommended for testing)
- Wallet with funds (or use faucet for testnet/devnet)
- Animica CLI installed

## Step-by-Step Workflow

### 1. Start a Local Node

```bash
# Start a local devnet node
animica node up --network devnet

# Or specify custom RPC port
animica node up --rpc-port 18546
```

### 2. Check Node Status

```bash
# Verify node is running
animica node status

# Check current chain head
animica chain head
```

### 3. Create or Import Wallet

```bash
# Create a new wallet
animica wallet new

# Or import existing wallet
animica key import --file keystore.json

# List available wallets
animica wallet list
```

### 4. Check Initial Balance

```bash
# Check balance for wallet address
animica wallet show 0

# Or check balance directly
animica chain balance <address>
```

### 5. Submit a Transaction

```bash
# Send ANM transfer
animica tx send \
  --from 0 \
  --to anim1zqq49tq6k0re53fvrfaj73amnnldfclxfhqawe92qps3yfm22ztgaesaspr0y \
  --value 1.5 \
  --gas-limit 21000 \
  --max-fee 1

# The command will output the transaction hash
# Example: Transaction submitted: 0x71f820618c75a0f5cf1b56c900f09ff45f184e4b8930354bcc4eacde4c84b4ca
```

### 6. Verify Transaction is Pending

```bash
# List all pending transactions in mempool
animica mempool list

# Show mempool statistics
animica mempool stats

# Get transaction details by hash
animica tx get <tx_hash>
```

Expected output:
```
Pending transactions (1):
    1. 0x71f820618c75a0f5cf1b56c900f09ff45f184e4b8930354bcc4eacde4c84b4ca
```

### 7. Mine Blocks

```bash
# Mine a single block
animica miner mine-blocks --count 1

# Or mine multiple blocks
animica miner mine-blocks --count 3

# Specify custom miner address for rewards
animica miner mine-blocks --count 1 --address <miner_address>
```

Expected output:
```
Mining 1 block(s)...
Block mined at height 65
Miner reward: 5000000000 nANM (5.0 ANM)
```

### 8. Verify Transaction is Included

```bash
# Get block by number
animica chain block <height>

# Or get recent blocks
animica chain blocks --count 5

# Get transaction receipt
animica tx receipt <tx_hash>
```

The transaction should appear in the block's transaction list.

### 9. Check Updated Balances

```bash
# Check sender balance (should decrease by value + gas fees)
animica wallet show 0

# Check recipient balance (should increase by value)
animica chain balance <recipient_address>
```

## Troubleshooting

### Transaction Not in Mempool

If your transaction isn't appearing in `animica mempool list`:

1. **Check transaction was submitted successfully**: Verify you received a transaction hash
2. **Check logs**: Look for rejection reasons in node logs
   ```bash
   animica node logs --tail 50
   ```
3. **Common issues**:
   - Insufficient funds in sender account
   - Gas price too low
   - Nonce mismatch (transaction with same nonce already submitted)
   - Chain ID mismatch
   - Invalid signature

### Transaction Not Included in Block

If transaction is in mempool but not included after mining:

1. **Check mempool before and after mining**:
   ```bash
   animica mempool list
   animica miner mine-blocks --count 1
   animica mempool list  # Should be empty or missing your tx
   ```

2. **Verify transaction execution**:
   ```bash
   animica tx receipt <tx_hash>
   ```
   
   Status codes:
   - `SUCCESS`: Transaction executed successfully
   - `REVERT`: Transaction reverted (insufficient funds, etc.)
   - `OOG`: Out of gas

3. **Enable debug logging**:
   ```bash
   export ANIMICA_LOG_LEVEL=DEBUG
   animica node restart
   ```

### Balance Not Updated

If balances don't reflect the transaction:

1. **Verify transaction status**:
   ```bash
   animica tx receipt <tx_hash>
   ```
   
2. **Check if transaction reverted**: Look at the `status` field in the receipt
   - Reverted transactions consume gas but don't transfer value
   
3. **Verify block was applied**: Check that the chain height increased
   ```bash
   animica chain head
   ```

## RPC API Reference

For programmatic access, use these RPC methods:

### Submit Transaction
```bash
curl -X POST http://127.0.0.1:18546/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tx.sendRawTransaction",
    "params": {"rawTx": "0x..."}
  }'
```

### List Pending Transactions
```bash
curl -X POST http://127.0.0.1:18546/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "mempool.getPending",
    "params": []
  }'
```

### Get Mempool Stats
```bash
curl -X POST http://127.0.0.1:18546/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "mempool.getStats",
    "params": []
  }'
```

### Mine Blocks
```bash
curl -X POST http://127.0.0.1:18546/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "miner.mine",
    "params": [1]
  }'
```

### Get Transaction by Hash
```bash
curl -X POST http://127.0.0.1:18546/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tx.getTransactionByHash",
    "params": {"txHash": "0x..."}
  }'
```

### Get Transaction Receipt
```bash
curl -X POST http://127.0.0.1:18546/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tx.getTransactionReceipt",
    "params": {"txHash": "0x..."}
  }'
```

## Units

- **ANM**: Base unit (1 ANM = 1,000,000,000 nANM)
- **nANM**: Nano-ANM, smallest unit (like wei in Ethereum)
- Gas prices and fees are in nANM
- Account balances are in nANM

## Example: Complete Transfer Workflow

```bash
# 1. Check initial balances
echo "Sender initial balance:"
animica wallet show 0
echo "Recipient initial balance:"
animica chain balance anim1zqq49tq6k0re53fvrfaj73amnnldfclxfhqawe92qps3yfm22ztgaesaspr0y

# 2. Submit transaction
TX_HASH=$(animica tx send \
  --from 0 \
  --to anim1zqq49tq6k0re53fvrfaj73amnnldfclxfhqawe92qps3yfm22ztgaesaspr0y \
  --value 2.5 \
  --gas-limit 21000 \
  --max-fee 1 \
  --json | jq -r '.result')

echo "Transaction submitted: $TX_HASH"

# 3. Verify in mempool
echo "Mempool before mining:"
animica mempool list

# 4. Mine block
animica miner mine-blocks --count 1

# 5. Verify mempool is empty
echo "Mempool after mining:"
animica mempool list

# 6. Get receipt
animica tx receipt $TX_HASH

# 7. Check final balances
echo "Sender final balance:"
animica wallet show 0
echo "Recipient final balance:"
animica chain balance anim1zqq49tq6k0re53fvrfaj73amnnldfclxfhqawe92qps3yfm22ztgaesaspr0y
```

## Additional Resources

- [RPC API Reference](./RPC_API.md)
- [Mining Guide](./MINING.md)
- [Wallet Management](./WALLET.md)
- [Chain Queries](./CHAIN_QUERIES.md)
