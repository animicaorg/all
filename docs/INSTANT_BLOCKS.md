# Instant Blocks: Zero-Reward, Non-Advancing Blocks

## Overview

Instant blocks are a special block type in the Animica blockchain that enable immediate transaction inclusion without affecting the chain's emission schedule or halving mechanics. They provide sub-second transaction finality while maintaining the economic properties of the main chain.

## Key Properties

### 1. Zero Rewards
- Instant blocks carry **no block rewards** (coinbase amount = 0)
- The `instantBlock` flag in the header signals this special status
- Rewards are computed via `consensus.rewards.compute_block_reward(instant_block=True)` which always returns an empty list

### 2. Non-Advancing Canonical Height
- Instant blocks **do not increment** the canonical height used for halving calculations
- The blockchain maintains two counters:
  - **Block height**: Increments for every block (normal and instant)
  - **Canonical height**: Increments only for normal blocks, used for emission schedule
- This ensures the halving schedule remains unchanged regardless of instant block frequency

### 3. No Proof-of-Work Required
- Instant blocks **skip PoW validation** (`nonce=0`)
- They are produced immediately when transactions arrive (if enabled)
- The block import process (`core.chain.block_import.BlockImporter`) skips PoW checks for instant blocks

### 4. Immediate Transaction Inclusion
- When enabled via `ANIMICA_INSTANT_BLOCKS_ENABLED=1`, transactions trigger instant block creation
- Provides near-instant transaction finality (< 1 second typical)
- Normal block production continues unchanged in parallel

## Architecture

### Header Structure

The `Header` dataclass (in `core/types/header.py`) includes:

```python
@dataclass(frozen=True)
class Header:
    # ... standard fields ...
    instantBlock: bool = False  # True for zero-reward, non-advancing blocks
```

The flag is included in CBOR serialization when `True` (omitted when `False` for space efficiency).

### Canonical Height Tracking

The `BlockDB` (in `core/db/block_db.py`) maintains:

- `META_CANONICAL_HEIGHT`: Tracks height excluding instant blocks
- Methods: `get_canonical_height()`, `set_canonical_height(height)`

### Halving Calculation

The `compute_canonical_height()` function (in `consensus/rewards.py`) computes the canonical height for a given block:

```python
def compute_canonical_height(
    height: int,           # Actual block height
    instant_block: bool,   # Is this an instant block?
    canonical_height: int | None = None  # Previous canonical height
) -> int:
    """
    Returns the canonical height for halving calculations.
    Instant blocks preserve the previous canonical height.
    """
```

## Usage

### Enabling Instant Blocks

Set the environment variable:

```bash
export ANIMICA_INSTANT_BLOCKS_ENABLED=1
```

Or in configuration:

```python
os.environ["ANIMICA_INSTANT_BLOCKS_ENABLED"] = "1"
```

### Manual Instant Block Creation

From the mining RPC:

```python
from rpc.methods.miner import _mine_instant_block

success, reward, summary = _mine_instant_block()
# reward will always be 0 for instant blocks
```

### Automatic Trigger on Transaction Arrival

When a transaction is added to the mempool, the system can automatically trigger instant block creation:

```python
from rpc.methods.miner import trigger_instant_block_on_tx_arrival

# Call this in mempool notification handler
trigger_instant_block_on_tx_arrival()
```

## Block Import Flow

When importing an instant block:

1. **Header Validation**: Standard checks (chainId, height continuity, timestamp)
2. **PoW Check**: **SKIPPED** for instant blocks (checks `header.instantBlock == True`)
3. **Transaction Execution**: Normal execution and state updates
4. **Reward Application**: Zero rewards applied (enforced by `compute_block_reward`)
5. **Canonical Height**: Preserved (not incremented)
6. **Storage**: Stored normally with `instantBlock` flag

## Consensus Rules

### Instant Block Requirements

1. `header.instantBlock == True`
2. `header.nonce == 0` (no PoW)
3. `coinbase_outputs == []` (zero rewards)
4. Block hash **includes** the `instantBlock` flag (affects header hash)

### Validation

The block importer (`_pow_sanity` method) checks:

```python
if header.instantBlock:
    # Instant blocks skip PoW
    if header.nonce != 0:
        return "instant block must have nonce=0"
    return None  # Valid instant block
```

### Fork Choice

Instant blocks participate in fork choice but use zero weight contribution:
- Normal blocks: Weight = θ (acceptance threshold)
- Instant blocks: Weight = 0 (but still part of canonical chain)

## Economics

### Emission Schedule

The emission schedule is based on **canonical height only**:

```python
epoch = (canonical_height - 1) // epoch_length_blocks
subsidy = start_subsidy * (decay_factor ** epoch)
```

Instant blocks do not affect:
- Halving epochs
- Block subsidy calculations
- Total supply issuance

### Example Scenario

Chain state after 10 blocks (5 normal, 5 instant):

- Block height: 10
- Canonical height: 5
- Halving calculation: Uses canonical height 5
- Total rewards issued: 5 normal blocks × subsidy per block
- Instant blocks issued: 0 rewards

## Implementation Files

### Core Changes

1. **Header Type** (`core/types/header.py`)
   - Added `instantBlock: bool` field
   - Updated serialization methods

2. **Block Database** (`core/db/block_db.py`)
   - Added canonical height tracking
   - Methods: `get_canonical_height()`, `set_canonical_height()`

3. **Block Import** (`core/chain/block_import.py`)
   - Skip PoW for instant blocks
   - Track canonical height separately
   - Update canonical height on normal blocks only

4. **Rewards** (`consensus/rewards.py`)
   - Added `instant_block` parameter to `compute_block_reward()`
   - Returns empty list for instant blocks
   - Added `compute_canonical_height()` helper

5. **Mining** (`rpc/methods/miner.py`)
   - Added `_mine_instant_block()` function
   - Added `trigger_instant_block_on_tx_arrival()` hook
   - Configuration via `ANIMICA_INSTANT_BLOCKS_ENABLED`

### Specification

- **Header Format** (`spec/header_format.cddl`)
  - Documented `instantBlock` optional field
  - Updated invariants section

## Testing

Comprehensive tests in `core/chain/tests/test_instant_blocks.py`:

1. **Serialization**: Instant block flag roundtrip
2. **Zero Rewards**: Verify instant blocks have no rewards
3. **Canonical Height**: Verify height calculation excludes instant blocks
4. **Hash Computation**: Verify instant block flag affects hash
5. **Build Child**: Verify `build_child()` supports instant blocks

## Future Enhancements

### Potential Improvements

1. **P2P Propagation**: Optimize instant block propagation in gossip protocol
2. **RPC Methods**: Add dedicated instant block query methods
3. **Metrics**: Track instant block production rate and latency
4. **Mempool Integration**: Automatic trigger on first transaction arrival
5. **Batch Instant Blocks**: Group multiple transactions into single instant block

### Considerations

- **Storage Growth**: Instant blocks increase chain size (though with zero rewards)
- **Sync Performance**: Nodes must distinguish instant vs normal blocks during sync
- **Reorg Handling**: Instant blocks must be properly handled during chain reorganizations

## Security Considerations

### Attack Vectors

1. **Spam Prevention**: Rate limiting instant block production
2. **DOS Protection**: Mempool admission policy still applies
3. **Consensus Safety**: Instant blocks cannot affect canonical chain security
4. **Economic Security**: Zero rewards mean no incentive to mine invalid instant blocks

### Mitigations

- Instant blocks still require valid transactions and state transitions
- PoW is still required for normal blocks that advance canonical height
- Fork choice weights prevent instant-block-only chains from becoming canonical

## References

- Spec: `spec/header_format.cddl`
- Tests: `core/chain/tests/test_instant_blocks.py`
- Implementation: `core/types/header.py`, `consensus/rewards.py`, `rpc/methods/miner.py`
- Block Import: `core/chain/block_import.py`
