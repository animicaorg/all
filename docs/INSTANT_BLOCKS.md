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

### Instant Blocks Are Enabled by Default

**Instant blocks are automatically enabled** in Animica mainnet, testnet, and devnet configurations. Transactions submitted via any method will create instant blocks immediately:

```bash
# No configuration needed - instant blocks work by default!
animica tx send --from <addr> --to <addr> --value 1.0
# → Instant block created automatically with the transaction
```

### Disabling Instant Blocks (Optional)

To disable instant blocks if needed:

```bash
export ANIMICA_INSTANT_BLOCKS_ENABLED=false
```

Or in Docker Compose:

```yaml
environment:
  ANIMICA_INSTANT_BLOCKS_ENABLED: "false"
```

### Automatic Trigger (Recommended)

**Instant blocks are automatically created when transactions arrive**, via:

1. **CLI Transaction Submission**:
   ```bash
   animica tx send --from <addr> --to <addr> --value 1.0
   # → Instant block created immediately with the transaction
   ```

2. **RPC Transaction Submission**:
   ```bash
   curl -X POST http://localhost:8545 \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"tx.sendRawTransaction","params":["0x..."],"id":1}'
   # → Instant block created immediately with the transaction
   ```

3. **P2P Inbound Transactions**:
   - Transactions received from P2P peers automatically trigger instant blocks
   - No additional configuration needed

**Integration Points**:
- `rpc/methods/tx.py::_tx_send_raw_transaction()` - Triggers after mempool admission
- `p2p/node/p2p_service.py::_admit_tx_result()` - Triggers after P2P admission

### Manual Instant Block Creation

From the mining RPC:

```python
from rpc.methods.miner import _mine_instant_block

success, reward, summary = _mine_instant_block()
# reward will always be 0 for instant blocks
```

### Programmatic Trigger

Trigger instant block creation from code:

```python
from rpc.methods.miner import trigger_instant_block_on_tx_arrival

# Call this to queue an instant block (best-effort)
trigger_instant_block_on_tx_arrival()
```

### Observability RPC Methods

Query instant blocks via RPC for monitoring and verification:

```bash
# List recent instant blocks
curl -X POST http://localhost:8545 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"miner.listInstantBlocks","params":{"limit":10},"id":1}'

# Get instant block statistics
curl -X POST http://localhost:8545 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"miner.getInstantBlockStats","params":{},"id":1}'
```

**Response examples:**

`miner.listInstantBlocks`:
```json
{
  "instantBlocks": [
    {
      "height": 105,
      "hash": "0x...",
      "timestamp": 1700000000,
      "txCount": 1,
      "reward": 0,
      "instantBlock": true,
      "canonicalHeight": 100
    }
  ],
  "total": 5,
  "limit": 10,
  "offset": 0
}
```

`miner.getInstantBlockStats`:
```json
{
  "enabled": true,
  "totalBlocks": 105,
  "canonicalHeight": 100,
  "instantBlockCount": 5,
  "instantBlockRatio": 0.0476
}
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

6. **Transaction RPC** (`rpc/methods/tx.py`)
   - Integrated instant block trigger in `_tx_send_raw_transaction()`
   - Auto-trigger after successful mempool admission
   - Updated `_ensure_tx_persisted_to_chain()` to prefer instant blocks

7. **P2P Service** (`p2p/node/p2p_service.py`)
   - Integrated instant block trigger in `_admit_tx_result()`
   - Auto-trigger for inbound P2P transactions
   - Best-effort triggering (doesn't fail tx admission)

### Specification

- **Header Format** (`spec/header_format.cddl`)
  - Documented `instantBlock` optional field
  - Updated invariants section

## Testing

Comprehensive tests in multiple locations:

1. **Unit Tests** (`core/chain/tests/test_instant_blocks.py`):
   - **Serialization**: Instant block flag roundtrip
   - **Zero Rewards**: Verify instant blocks have no rewards
   - **Canonical Height**: Verify height calculation excludes instant blocks
   - **Hash Computation**: Verify instant block flag affects hash
   - **Build Child**: Verify `build_child()` supports instant blocks

2. **Integration Tests** (`tests/integration/test_instant_block_tx_send.py`):
   - **Automatic Triggering**: Verify instant blocks created on tx send
   - **Block Properties**: Verify zero reward and non-advancing height
   - **Header Flag**: Verify instantBlock=True in created blocks
   - **Canonical Height Tracking**: Verify separate height counters

## Future Enhancements

### Completed Features ✅

1. **Mempool Integration**: ✅ Automatic trigger on transaction arrival (tx send & P2P)
2. **Testing**: ✅ Comprehensive unit and integration tests
3. **RPC Observability**: ✅ Dedicated RPC methods for querying instant blocks
   - `miner.listInstantBlocks` - List recent instant blocks with details
   - `miner.getInstantBlockStats` - Get statistics about instant block usage

### Potential Improvements

1. **P2P Propagation**: Optimize instant block propagation in gossip protocol
2. **Metrics**: Track instant block production rate and latency
3. **Batch Instant Blocks**: Group multiple transactions into single instant block
5. **Rate Limiting**: Add configurable limits on instant block production rate

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
