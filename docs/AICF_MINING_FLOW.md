# AICF Mining Flow

## Overview

The AICF Credit Flow enables miners to directly contribute to AI training by automatically allocating a portion of their block rewards to an AI Compute Fund (AICF). These credits fund off-chain GPU workers who execute training/eval/distillation jobs.

## Key Concepts

### AICF Credits

- **Credits**: Virtual tokens minted from block rewards
- **1 credit = 1 nANM** (base unit)
- Credits are tracked separately from on-chain ANM balances
- Credits can only be spent on AICF-funded jobs
- All credit operations are logged to an immutable ledger

### Credit Sources

1. **Block Rewards** (primary source)
   - Each accepted block triggers credit minting
   - Configurable slice via `aicf_slice_bps` (default: 1000 = 10%)
   - Miner receives (100% - slice) in ANM, AICF gets slice in credits

2. **Transaction Fees** (future)
   - Sum of gas fees from all transactions in the block
   - Same slice percentage applied

3. **Shares** (optional, future)
   - Pool mining shares can mint smaller credits
   - Rate-limited per-miner and globally to prevent spam

## Flow Diagram

```
Block Acceptance
       ↓
Calculate Rewards (consensus/rewards.py)
       ↓
Split: Miner Payout (90%) | AICF Credits (10%)
       ↓                    ↓
State DB Update          AICF Protocol State
(miner balance)          (credit minting)
       ↓                    ↓
   Tx Receipt        Credit Ledger Entry
                            ↓
                     Miner Credits Balance
```

## Implementation Details

### 1. On Block Acceptance

When `miner.submitBlock` succeeds:

1. Block is validated and imported
2. Block rewards are calculated using `consensus.rewards.compute_block_reward`
3. Rewards are applied to state (miner receives ANM payout)
4. **AICF credit minting** is triggered:
   - Load AICF protocol state DB
   - Compute split: `(miner_payout, aicf_credits) = compute_credit_split(total, aicf_slice_bps)`
   - Update miner's AICF credit balance
   - Update global AICF totals
   - Log event to immutable ledger

### 2. Credit Allocation Function

```python
def compute_credit_split(total_amount: int, aicf_slice_bps: int = 1000):
    """
    Split reward/fee amount into (miner_amount, aicf_credits).
    
    - aicf_slice_bps: basis points (1000 = 10%)
    - Returns: (miner_amount, aicf_credits)
    - Invariant: miner_amount + aicf_credits == total_amount
    """
    aicf_credits = (total_amount * aicf_slice_bps) // 10_000
    miner_amount = total_amount - aicf_credits
    return miner_amount, aicf_credits
```

**Properties:**
- **Deterministic**: same inputs always produce same outputs
- **Integer-safe**: no floating point, uses integer division
- **Total-preserving**: miner + AICF always equals input total
- **Remainder-safe**: any rounding remainder goes to miner

### 3. State Schema

#### Global Totals (`aicf_credit_totals`)
- `balance_total`: Total credits available
- `minted_total`: Cumulative credits minted
- `spent_total`: Cumulative credits spent
- `last_update_height`: Last block height that updated credits
- `last_update_hash`: Last block hash

#### Per-Miner Balances (`aicf_miner_credits`)
- `miner_address`: Miner address (32-byte hex)
- `balance`: Current available credits
- `lifetime_earned`: Total credits ever earned
- `lifetime_spent`: Total credits ever spent
- `last_mint_height`: Last mint block height
- `last_mint_hash`: Last mint block hash

#### Immutable Ledger (`aicf_credit_ledger`)
- `ledger_id`: Deterministic event ID (SHA256)
- `event_type`: `'credit_minted'` or `'credit_spent'`
- `block_height`: Block height where event occurred
- `block_hash`: Block hash
- `amount`: Credit amount
- `source`: `'reward'`, `'fees'`, or `'share'`
- `miner_address`: For minted events
- `job_id`: For spent events
- `recipients_json`: For spent events (payout recipients)
- `metadata_json`: Additional event data

### 4. Ledger ID Generation

```python
def _compute_ledger_id(event_type, block_height, block_hash, miner_address, amount, source):
    data = f"{event_type}|{block_height}|{block_hash}|{miner_address}|{amount}|{source}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
```

**Ensures:**
- **Idempotency**: Replaying same block produces same ID
- **Uniqueness**: Different blocks produce different IDs
- **Determinism**: No randomness or timestamps in ID

### 5. Configuration

#### Chain Parameters (`spec/params.yaml`)

```yaml
monetary:
  issuance:
    aicf_slice_bps: 1000  # 10% of block rewards go to AICF credits
```

**Valid range**: 0-10000 basis points (0%-100%)

**Examples:**
- 0 bps = 0% (all rewards to miner)
- 500 bps = 5%
- 1000 bps = 10% (default)
- 2000 bps = 20%
- 10000 bps = 100% (all rewards to AICF)

## RPC API

### Query Methods

#### `state.getAicfSummary`

Get global AICF credit statistics.

**Params:** None

**Returns:**
```json
{
  "balance_total": "1000000000",
  "minted_total": "5000000000",
  "spent_total": "4000000000",
  "last_update_height": 1234,
  "last_update_hash": "0x..."
}
```

#### `state.getAicfMinerCredits`

Get credit balance for a specific miner.

**Params:**
- `address` (string): Miner address (hex or bech32)

**Returns:**
```json
{
  "miner_address": "0x...",
  "balance": "500000000",
  "lifetime_earned": "2000000000",
  "lifetime_spent": "1500000000",
  "last_mint_height": 1234,
  "last_mint_hash": "0x..."
}
```

## CLI Commands

### Check Global Status

```bash
animica aicf status
```

**Output:**
```
AICF Credit Summary:
  Total Balance: 1.000000000 credits
  Total Minted: 5.000000000 credits
  Total Spent: 4.000000000 credits

Last Update:
  Block Height: 1234
  Block Hash: 0x...
```

### Check Miner Credits

```bash
animica aicf miner-credits <address>
```

**Example:**
```bash
animica aicf miner-credits anim1xyz...
```

**Output:**
```
Miner Credits: 0x...
  Current Balance: 0.500000000 credits
  Lifetime Earned: 2.000000000 credits
  Lifetime Spent: 1.500000000 credits

Last Mint:
  Block Height: 1234
  Block Hash: 0x...
```

## Safety Guarantees

### Replay Safety

- **Deterministic minting**: Same block always mints same credits
- **Idempotent logging**: Replaying blocks won't create duplicate ledger entries
- **State consistency**: Credits track exactly with block history

### Economic Safety

- **No inflation**: Credits are minted from existing block rewards (not new supply)
- **Total preservation**: Miner ANM + AICF credits = original reward amount
- **Auditability**: Every credit allocation is logged immutably

### Failure Isolation

- **Mining continues on errors**: If AICF credit minting fails, block acceptance still succeeds
- **Graceful degradation**: Missing AICF state DB is logged but doesn't halt chain
- **Reconciliation**: Credit ledger can be rebuilt from block history if needed

## Testing

### Unit Tests

Run AICF credit split tests:

```bash
pytest tests/test_aicf_credit_split.py -v
```

**Coverage:**
- Split math correctness (basis points exactness)
- Edge cases (zero amount, zero slice, 100% slice)
- Rounding behavior
- Invalid input handling
- Configuration parsing

### Integration Tests

Run end-to-end mining with AICF:

```bash
# Mine 10 blocks and verify AICF credits
pytest tests/test_aicf_e2e_mining.py -v
```

## Job Marketplace (Future)

Once credits are minted, miners can spend them on:

### Training Jobs
- Submit training plan (dataset + hyperparams)
- Allocate credit budget
- GPU workers claim and execute
- Results verified and finalized
- Credits paid to workers

### Eval Jobs
- Benchmark model performance
- Run standardized test suites
- Validate training quality

### Distillation Jobs
- Compress large models
- Transfer knowledge to smaller models
- Optimize for inference

See [AICF_JOBS.md](./AICF_JOBS.md) for marketplace details (to be implemented).

## Troubleshooting

### Credits Not Minting

**Check 1: AICF slice configuration**
```bash
# Verify aicf_slice_bps in params.yaml
cat spec/params.yaml | grep aicf_slice_bps
```

**Check 2: Block acceptance**
```bash
# Verify blocks are being accepted
animica chain head
animica aicf status
```

**Check 3: Logs**
```bash
# Check for credit minting logs
grep "AICF credits minted" logs/node.log
```

### Credit Balance Mismatch

**Rebuild from ledger:**
```bash
# Future tool to reconcile credits from block history
animica aicf reconcile --from-height 0
```

### State DB Issues

**Check DB path:**
```bash
# Default: ~/.animica/data/aicf_protocol.db
ls -lh ~/.animica/data/aicf_protocol.db
```

**Reset AICF state (destructive):**
```bash
# WARNING: Deletes all credit history
rm ~/.animica/data/aicf_protocol.db
# Credits will be reminted on next block
```

## References

- **Source Code:**
  - `aicf/credits/minting.py` - Credit allocation logic
  - `aicf/protocol/state.py` - State management
  - `rpc/methods/miner.py` - Block acceptance integration
  - `rpc/methods/state.py` - RPC query endpoints

- **Configuration:**
  - `spec/params.yaml` - Chain parameters
  - `aicf/db/schema_protocol.sql` - Database schema

- **Documentation:**
  - [AICF_JOBS.md](./AICF_JOBS.md) - Job marketplace (future)
  - [consensus/rewards.py](../consensus/rewards.py) - Block reward calculation
