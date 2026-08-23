---
title: "AICF (AI Compute Fund) - User Guide"
description: "How the AI Compute Fund pool is funded from block rewards and fees, how miners earn and claim credits, and the CLI and RPC calls involved."
group: "aicf"
order: 1
draft: false
---

*Source: `docs/AICF.md` — this page mirrors the repository documentation.*

## Overview

The AICF (AI Compute Fund) is a protocol-level mechanism for funding AI and quantum compute providers on the Animica network. It operates on a block-based credit system where miners earn credits for each block they mine, and these credits can be claimed proportionally from a pool funded by block rewards and transaction fees.

## Key Concepts

### Epochs

- **Epoch**: A fixed-length window of blocks (default: 100 blocks)
- **Epoch Number**: Computed as `floor(block_height / epoch_length)`
- **Finalization**: Epochs are finalized 2 blocks after completion, making their budget distributable

### Credits

- **Credits**: Non-transferable points awarded to miners when they mine a block
- **Credits per Block**: Fixed amount (default: 1,000,000 credits)
- **Total Credits**: Sum of all credits awarded in an epoch
- **User Credits**: Credits earned by a specific miner in an epoch

### Funding Sources

The AICF pool is funded by:

1. **Block Reward Slice** (5% default)
   - Taken from miner's block reward
   - Configured via `block_reward_slice_bps` (500 = 5%)

2. **Transaction Fee Slice** (20% default)
   - Taken from transaction priority fees
   - Configured via `fee_slice_bps` (2000 = 20%)

3. **ENA Call Fees** (80% to AICF, default)
   - Fees charged for External Network Access (ENA) calls
   - Base fee: `ena_call_fee_base_nano` (0.00001 ANM default)
   - AICF portion: `ena_call_fee_aicf_bps` (8000 = 80%)

4. **Governance Top-ups**
   - Manual injections from treasury
   - Requires governance approval

### Claiming

- **Claimable Epochs**: Only epochs that are finalized (current_epoch - 2 or earlier)
- **Pro-rata Distribution**: Share = (your_credits / total_credits) * budget
- **Idempotent**: Claiming twice does not double-pay
- **Max Epochs**: Claims are limited to 100 epochs per transaction (configurable)

## Configuration Parameters

All parameters are defined in `spec/params.yaml` under `networks.[network].aicf`:

```yaml
aicf:
  epoch_length_blocks: 100          # Blocks per epoch
  block_reward_slice_bps: 500       # 5% of block reward to AICF
  fee_slice_bps: 2000               # 20% of tx fees to AICF
  ena_call_fee_base_nano: 10000     # 0.00001 ANM per ENA call
  ena_call_fee_aicf_bps: 8000       # 80% of ENA fee to AICF
  epoch_payout_bps: 5000            # 50% of epoch inflows distributable
  credits_per_block: 1000000        # Credits awarded per block
  max_claim_epochs: 100             # Max epochs per claim
  prune_after_epochs: 10000         # Epochs to keep before pruning
```

## RPC Methods

### `aicf.getParams`

Get AICF configuration parameters.

**Request:**
```bash
curl -X POST https://mainnet.animica.org/rpc \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "aicf.getParams",
    "params": []
  }'
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "epoch_length_blocks": 100,
    "block_reward_slice_bps": 500,
    "fee_slice_bps": 2000,
    "ena_call_fee_base_nano": 10000,
    "ena_call_fee_aicf_bps": 8000,
    "epoch_payout_bps": 5000,
    "credits_per_block": 1000000,
    "max_claim_epochs": 100,
    "prune_after_epochs": 10000
  }
}
```

### `aicf.getStatus`

Get current AICF pool status.

**Request:**
```bash
curl -X POST https://mainnet.animica.org/rpc \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "aicf.getStatus",
    "params": []
  }'
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "pool_balance": "0x2540be400",
    "current_epoch": 42,
    "current_height": 4200,
    "last_finalized_epoch": 40
  }
}
```

### `aicf.getClaimable`

Get claimable rewards for an address.

**Request:**
```bash
curl -X POST https://mainnet.animica.org/rpc \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "aicf.getClaimable",
    "params": ["anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz", true]
  }'
```

**Parameters:**
- `address` (required): Bech32m or hex-encoded address
- `includeDetails` (optional): Include per-epoch breakdown

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "claimable": "0x3b9aca00",
    "epochs": [38, 39, 40],
    "details": [
      {
        "epoch": 38,
        "credits": "0xf4240",
        "total_credits": "0x1e8480",
        "share": "0x1312d00"
      },
      {
        "epoch": 39,
        "credits": "0xf4240",
        "total_credits": "0x1e8480",
        "share": "0x1312d00"
      },
      {
        "epoch": 40,
        "credits": "0xf4240",
        "total_credits": "0x1e8480",
        "share": "0x1312d00"
      }
    ]
  }
}
```

### `aicf.claim`

Get claim information (read-only). To execute a claim, send a transaction via `tx.sendRawTransaction`.

**Request:**
```bash
curl -X POST https://mainnet.animica.org/rpc \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "aicf.claim",
    "params": ["anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"]
  }'
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "claimable": "0x3b9aca00",
    "epochs": [38, 39, 40],
    "message": "This is a read-only response. To claim, you must send a transaction through tx.sendRawTransaction with the claim operation."
  }
}
```

### `aicf.topUp`

Governance-only method to add funds to AICF pool.

**Request:**
```bash
curl -X POST https://mainnet.animica.org/rpc \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "aicf.topUp",
    "params": ["0x2540be400"]
  }'
```

**Parameters:**
- `amount` (required): Hex quantity or decimal string

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "success": false,
    "message": "Top-up functionality not yet implemented. Requires governance transaction."
  }
}
```

## How It Works

### 1. Mining a Block

When a miner successfully mines a block:

1. The block is validated and applied to the chain
2. Credits are awarded to the miner:
   - Amount: `credits_per_block` (default: 1,000,000)
   - Tracked in: `aicf.epoch.{epoch}.credits_user.{miner_address}`
   - Total updated: `aicf.epoch.{epoch}.credits_total`

3. Funding flows into AICF pool:
   - Block reward slice: 5% of base reward → AICF pool
   - Transaction fees: 20% of priority fees → AICF pool
   - Tracked in: `aicf.epoch.{epoch}.inflow`

### 2. Epoch Finalization

At epoch boundaries (every 100 blocks):

1. Previous epoch (E-1) is finalized
2. Budget computed: `budget = min(inflow * 50%, pool_balance)`
3. Budget marked as distributable
4. Remaining 50% stays as reserve in pool

### 3. Claiming Rewards

Miners can claim rewards from finalized epochs:

1. Call `aicf.getClaimable(address)` to check claimable amount
2. For each finalized epoch:
   - Compute share: `(user_credits / total_credits) * budget`
   - Sum across all unclaimed epochs
3. Send claim transaction (to be implemented)
4. AICF pool transfers claimable amount to miner
5. Last claimed epoch is updated

### 4. Replay Protection

- Claims are idempotent: calling twice returns 0 the second time
- State key `aicf.last_claimed_epoch.{address}` tracks progress
- Only epochs > last_claimed are processed

## State Schema

All state is stored in the chain state DB with deterministic keys:

```
aicf.epoch_length                           → u64  (epoch length config)
aicf.epoch.{E}.credits_total                → u128 (total credits in epoch E)
aicf.epoch.{E}.credits_user.{address}       → u128 (credits for user in epoch E)
aicf.epoch.{E}.budget                       → u128 (distributable budget for epoch E)
aicf.epoch.{E}.inflow                       → u128 (total inflow to pool in epoch E)
aicf.last_claimed_epoch.{address}           → u64  (last epoch claimed by address)
aicf.pool_balance                           → u128 (current pool balance, cached)
```

## Security Considerations

1. **Overflow Protection**: All arithmetic uses safe checked operations
2. **Determinism**: All logic is purely deterministic; no I/O dependencies
3. **Idempotency**: Claims are replay-safe and idempotent
4. **Reorg Safety**: State keys are epoch/address-scoped for reorg resilience
5. **Budget Caps**: Budgets are capped by available pool balance
6. **Max Epochs**: Claims are limited to prevent DoS

## Future Work

1. **Claim Transactions**: Implement actual claim transaction execution
2. **Governance Top-up**: Implement permissioned governance top-up transactions
3. **ENA Call Fees**: Integrate ENA call fee routing
4. **State Pruning**: Implement configurable state pruning after N epochs
5. **Multi-sig Claims**: Support for claiming to different addresses
6. **Delegation**: Allow miners to delegate claims to other addresses

## Examples

### Check Your Claimable Rewards

```bash
# Replace with your address
ADDRESS="anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"

curl -X POST https://mainnet.animica.org/rpc \
  -H 'Content-Type: application/json' \
  -d "{
    \"jsonrpc\": \"2.0\",
    \"id\": 1,
    \"method\": \"aicf.getClaimable\",
    \"params\": [\"$ADDRESS\", true]
  }" | jq
```

### Monitor AICF Pool

```bash
# Get current status
curl -X POST https://mainnet.animica.org/rpc \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "aicf.getStatus",
    "params": []
  }' | jq

# Get parameters
curl -X POST https://mainnet.animica.org/rpc \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "aicf.getParams",
    "params": []
  }' | jq
```

## Troubleshooting

### "Insufficient AICF pool balance" Error

This should never happen if budget computation is correct. It indicates:
- Pool was drained below budget commitments
- State corruption or bug in budget calculation

**Resolution**: Report as a critical bug with block height and epoch number.

### "Invalid address format" Error

Ensure your address is:
- Valid bech32m with "anim" prefix, OR
- Valid 0x-prefixed 64-character hex (32 bytes)

### Zero Claimable Rewards

Possible reasons:
- You haven't mined any blocks in finalized epochs
- All your epochs have been claimed
- Current epoch is not yet finalized (wait 2+ epochs)
- Budget for your epochs is zero (no inflows)

## Support

For issues or questions:
- GitHub: https://github.com/animicaorg/all/issues
- Discord: https://discord.gg/animica
- Docs: https://docs.animica.org
