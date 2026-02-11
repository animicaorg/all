# AICF: AI Compute Fund

## Overview

The **AICF (AI Compute Fund)** is a protocol-level system in Animica that:

1. **Creates a dedicated pool** of 119M ANM (mainnet) that is NOT premined to any address
2. **Fills via AICF mining** - miners submit verifiable proofs of useful work to earn rewards
3. **Enables contracts** to request AI compute and pay fees that replenish the pool
4. **Provides ongoing demand** for AICF miners through contract consumption

**Key insight**: Instead of a premine to an address, the 119M ANM "earns into existence" through a dedicated AICF mining workflow with stronger work requirements than regular block mining.

## Design Principles

### 1. System-Level Accounting

The AICF pool is **NOT a wallet address**. It's a protocol-level accounting object similar to a treasury or escrow:

- Stored in system state (not UTXO/account model)
- Balance can only change via protocol rules
- Fully auditable and deterministic
- All nodes converge on same pool state

### 2. No Premine Address

Traditional approach (❌):
```
Genesis: anim1foundation... gets 119M ANM
```

AICF approach (✅):
```
Genesis: aicf_pool_balance = 0, cap = 119M ANM
AICF miners submit proofs → pool balance increases
Contracts consume compute → fees flow back to pool
```

### 3. Verifiable Work

AICF mining requires stronger proof than basic PoW:
- Deterministic work units (configurable difficulty)
- Verifiable on-chain (no trust required)
- Rate limited per miner per epoch
- Placeholder for real AI compute verification

**Current Implementation**: Deterministic proof-of-work with configurable difficulty  
**Future**: Plug in real AI model evaluation, quantum computation verification, etc.

### 4. Economic Sustainability

AICF creates a sustainable economic loop:

```
Miners → Submit AICF proofs → Earn ANM (minted to pool)
                                      ↓
Contracts → Request compute → Pay fees (replenish pool)
                                      ↓
                             Pool funds payouts to miners
```

## Architecture

### State Structure

**Pool State** (stored in `StateDB` under system prefix):

```python
{
  "balance": int,              # Current pool balance (nANM)
  "cap": int,                  # Maximum issuance cap (nANM)
  "issued_total": int,         # Cumulative issued into pool
  "spent_total": int,          # Cumulative spent from pool
  "miner_credits": {           # Miner address -> credits earned
    "<addr_hex>": int
  },
  "epoch_proofs": {            # Epoch -> miner -> proof count
    "<epoch>": {
      "<addr_hex>": int
    }
  }
}
```

**Invariants**:
- `balance >= 0`
- `balance == issued_total - spent_total`
- `issued_total <= cap` (during premine phase)
- All operations deterministic

### Parameters (from spec/params.yaml)

**Mainnet**:
```yaml
aicf_pool:
  enabled: true
  cap_anm: 119000000.0                    # 119M ANM
  reward_per_proof_anm: 10.0              # 10 ANM per proof
  max_proofs_per_block: 10                # Rate limit
  max_proofs_per_miner_per_epoch: 1000    # Per-miner limit
  epoch_blocks: 1440                      # ~5 days
  min_work_difficulty: 20                 # Difficulty threshold
  verification_timeout_ms: 5000
  fee_routing_pct: 100                    # 100% of fees to pool
```

**Testnet/Devnet**: Lower caps and easier difficulty for testing.

### Transaction Types

New transaction kind added:

```python
class TxKind(IntEnum):
    TRANSFER = 0
    DEPLOY = 1
    CALL = 2
    COINBASE = 3
    AICF_PROOF = 4  # AICF proof submission
```

**AICF_PROOF transactions** contain:
- Miner address (32 bytes)
- Work units (int)
- Proof data (bytes)
- Timestamp (int)
- Nonce (int)

## Usage

### Query Pool State

**CLI**:
```bash
animica aicf status
```

**RPC**:
```bash
curl -X POST http://localhost:8545/rpc -d '{
  "jsonrpc": "2.0",
  "method": "aicf.getPoolState",
  "params": {},
  "id": 1
}'
```

**Response**:
```json
{
  "enabled": true,
  "balance": 0,
  "cap": 119000000000000000,
  "issued_total": 0,
  "spent_total": 0,
  "balance_anm": 0.0,
  "cap_anm": 119000000.0,
  "percent_filled": 0.0
}
```

### Run AICF Miner

```bash
# Start miner (submits proofs continuously)
animica aicf miner \
  --address anim1your-address... \
  --difficulty 20 \
  --interval 60

# Submit specific number of proofs
animica aicf miner \
  --address anim1your-address... \
  --difficulty 20 \
  --count 10
```

**What the miner does**:
1. Performs deterministic proof-of-work (configurable difficulty)
2. Submits proof via `aicf.submitProof` RPC
3. If valid, pool balance increases by `reward_per_proof_anm`
4. Miner credits tracked for future payouts
5. Rate limited per epoch to prevent spam

### Query Parameters

```bash
animica aicf params
```

Shows all AICF configuration including caps, rewards, rate limits, etc.

### View Statistics

```bash
animica aicf stats
```

Shows:
- Total proofs submitted
- Total miners
- Top miners by credits

## Contract Integration

### Request Compute (Future)

Contracts will be able to request AI compute via syscall/precompile:

```python
# From within a smart contract
result = aicf.request_compute({
    "model": "llama3-8b",
    "input": "...",
    "max_units": 100
})

# Pay for compute (deducted from contract balance)
aicf.pay_for_compute(cost_in_anm)
```

**Determinism**: On-chain only sees deterministic receipt/commitment, not nondeterministic AI output directly.

**Fee routing**: Contract payments flow into AICF pool, creating ongoing demand.

## Security & Determinism

### Consensus Rules

AICF proofs are verified deterministically:
- Work units meet minimum difficulty threshold
- Miner hasn't exceeded epoch limits
- Pool hasn't exceeded cap (during premine phase)
- Proof data is well-formed and unique

### Replay Protection

- Each proof must have unique nonce
- Timestamp must be recent (within reasonable window)
- Duplicate proofs rejected

### Rate Limiting

Per-block:
- Max `max_proofs_per_block` proofs accepted
- Prevents spam attacks

Per-miner per-epoch:
- Max `max_proofs_per_miner_per_epoch` proofs
- Prevents single miner from monopolizing rewards

### Deterministic Verification

All verification happens on-chain with deterministic rules:
- No external API calls
- No nondeterministic randomness
- All nodes reach same decision

## Economics

### Supply Accounting

**Total ANM supply** = regular supply + AICF pool issuance

**Regular supply**:
- Mainnet premine: 81M ANM (existing)
- Block rewards: ~819M ANM over time (existing)

**AICF supply**:
- Cap: 119M ANM (new)
- Issued via AICF mining (not premine)
- Separate from coinbase

**Total**: 81M + 819M + 119M = 1,019M ANM maximum

### Reward Schedule

**Current**: Fixed 10 ANM per accepted proof (mainnet)

**Future considerations**:
- Dynamic rewards based on pool balance
- Decay over time like block rewards
- Market-driven pricing based on demand

### Fee Routing

When contracts request compute:
- Payment deducted from contract balance
- `fee_routing_pct` goes to AICF pool (default 100%)
- Remainder to miners/treasury (if configured)

## Operational Guide

### Genesis/Reset

When resetting chain:

```bash
# Hard reset (deletes all data)
animica node reset --hard --yes

# Start fresh
animica node up
```

**What happens**:
1. Chain resets to height 0
2. Genesis state created
3. AICF pool initialized: `balance=0, cap=119M`
4. No premine address with 119M ANM
5. Pool fills as AICF miners submit proofs

### Monitoring

**Check pool status**:
```bash
animica aicf status
```

**Check parameters**:
```bash
animica aicf params
```

**View miner stats**:
```bash
animica aicf stats
```

**RPC endpoints**:
- `aicf.getPoolState` - current state
- `aicf.getParams` - configuration
- `aicf.getUsageStats` - statistics
- `aicf.submitProof` - submit proof

### Troubleshooting

**Proof rejected - "insufficient work"**:
- Increase `--difficulty` flag on miner
- Check `min_work_difficulty` in params

**Proof rejected - "miner exceeded epoch limit"**:
- Wait for next epoch (see `epoch_blocks` in params)
- Current epoch: `current_height / epoch_blocks`

**Proof rejected - "would exceed cap"**:
- Pool has reached 119M ANM cap
- After cap, only fee-funded operations continue

## Future Enhancements

### Real AI Compute Verification

Replace placeholder proof-of-work with:
- Deterministic model evaluation on fixed datasets
- Merkle proofs of computation
- Zero-knowledge proofs
- Verifiable random functions (VRFs)

### Smart Contract Syscalls

Add precompile/syscall for contracts:
```python
@syscall
def aicf_request_compute(
    job_spec: bytes,
    max_units: int,
) -> (bool, bytes):
    # Submit job to AICF queue
    # Return (success, receipt)
    pass

@syscall
def aicf_verify_receipt(receipt: bytes) -> bool:
    # Verify receipt on-chain
    pass
```

### Dynamic Rewards

Adjust rewards based on:
- Pool balance (lower when depleted)
- Demand (higher when contracts need compute)
- Time (decay like block rewards)

### Governance

Allow parameters to be updated via governance:
- `reward_per_proof_anm`
- `max_proofs_per_block`
- `epoch_blocks`
- etc.

## References

- **Implementation**: 
  - `core/aicf_pool.py` - Pool state and verification logic
  - `core/db/state_db.py` - Pool storage in StateDB
  - `core/genesis/loader.py` - Genesis initialization
  - `rpc/methods/aicf.py` - RPC methods
  - `python/animica/cli/aicf.py` - CLI commands

- **Parameters**: `spec/params.yaml` (networks.*.aicf_pool)

- **Transaction type**: `TxKind.AICF_PROOF` in `core/types/tx.py` and `coretx/types.py`

## Summary

AICF provides a novel approach to blockchain-funded AI compute:

✅ **No premine address** - pool starts at 0  
✅ **Earned via mining** - 119M ANM fills through AICF proofs  
✅ **Contract integration** - smart contracts can request compute  
✅ **Sustainable economics** - fees replenish pool  
✅ **Deterministic** - all verification on-chain  
✅ **Auditable** - full transparency of pool state  

This creates a self-sustaining ecosystem where:
- Miners earn by providing verifiable work
- Contracts consume AI compute
- Fees flow back to reward miners
- Protocol benefits from productive use of funds
