# Multi-Node Mining Guide

This guide explains how to efficiently mine with multiple nodes to the same wallet address using the `--miner-id` parameter.

## Problem

When multiple mining nodes mine to the same wallet address without coordination, they waste computational resources by checking the same nonces:

```
Node 1 checks: 0, 2, 4, 6, 8, 10, ...
Node 2 checks: 0, 2, 4, 6, 8, 10, ...  ← Duplicate work!
Node 3 checks: 0, 2, 4, 6, 8, 10, ...  ← Duplicate work!
```

This results in:
- 🔴 **Wasted CPU cycles** checking nonces already tested by other nodes
- 🔴 **Lower effective hashrate** than the sum of individual nodes
- 🔴 **Inefficient mining** when scaling horizontally

## Solution: Miner ID Partitioning

The `--miner-id` parameter (0-255) assigns each node a unique identifier that partitions the nonce search space:

```
Node 1 (miner-id=0): 0, 512, 1024, 1536, ...
Node 2 (miner-id=1): 2, 514, 1026, 1538, ...  ← Different nonces!
Node 3 (miner-id=2): 4, 516, 1028, 1540, ...  ← Different nonces!
```

Benefits:
- ✅ **Zero overlap** between mining nodes
- ✅ **Linear hashrate scaling** with node count
- ✅ **Efficient resource utilization** across all nodes
- ✅ **Simple setup** with just one additional parameter

## Quick Start

### Single Node (default behavior)

No changes needed for single-node mining:

```bash
python -m mining.cli.miner mine-blocks \
  --address anim1youraddress \
  --count 10 \
  --workers 4
```

### Multi-Node Mining

Assign unique miner IDs (0-255) to each node:

**Node 1:**
```bash
python -m mining.cli.miner mine-blocks \
  --address anim1shared \
  --count 10 \
  --miner-id 0 \
  --workers 4
```

**Node 2:**
```bash
python -m mining.cli.miner mine-blocks \
  --address anim1shared \
  --count 10 \
  --miner-id 1 \
  --workers 4
```

**Node 3:**
```bash
python -m mining.cli.miner mine-blocks \
  --address anim1shared \
  --count 10 \
  --miner-id 2 \
  --workers 4
```

### Continuous Mining (orchestrator mode)

For long-running mining operations:

**Node 1:**
```bash
python -m mining.cli.miner start \
  --miner-id 0 \
  --threads 8
```

**Node 2:**
```bash
python -m mining.cli.miner start \
  --miner-id 1 \
  --threads 8
```

## Configuration

### Environment Variable

Set a default miner ID via environment variable:

```bash
export ANIMICA_MINER_ID=0
python -m mining.cli.miner mine-blocks --address anim1... --count 10
```

### Docker Compose Example

```yaml
version: '3.8'
services:
  miner-1:
    image: animica/miner:latest
    environment:
      - ANIMICA_MINER_ID=0
      - ANIMICA_MINER_WORKERS=4
    command: mine-blocks --address anim1shared --count 100
  
  miner-2:
    image: animica/miner:latest
    environment:
      - ANIMICA_MINER_ID=1
      - ANIMICA_MINER_WORKERS=4
    command: mine-blocks --address anim1shared --count 100
  
  miner-3:
    image: animica/miner:latest
    environment:
      - ANIMICA_MINER_ID=2
      - ANIMICA_MINER_WORKERS=4
    command: mine-blocks --address anim1shared --count 100
```

## Performance Characteristics

### Nonce Space Distribution

The implementation uses a global worker ID formula:
```
global_worker_id = miner_id * workers + worker_id
stride = workers * 256  # Supports up to 256 miners
start_nonce = global_worker_id
```

### Example with 3 miners, 2 workers each:

| Miner | Worker | Global ID | Nonce Sequence |
|-------|--------|-----------|----------------|
| 0     | 0      | 0         | 0, 512, 1024, 1536, ... |
| 0     | 1      | 1         | 1, 513, 1025, 1537, ... |
| 1     | 0      | 2         | 2, 514, 1026, 1538, ... |
| 1     | 1      | 3         | 3, 515, 1027, 1539, ... |
| 2     | 0      | 4         | 4, 516, 1028, 1540, ... |
| 2     | 1      | 5         | 5, 517, 1029, 1541, ... |

### Efficiency Gain

**Before (without miner-id):**
- 3 nodes × 4 workers = 12 workers
- All checking overlapping nonces
- Effective hashrate ≈ 4 workers (significant waste)

**After (with miner-id):**
- 3 nodes × 4 workers = 12 workers
- All checking unique nonces
- Effective hashrate = 12 workers (100% efficiency)

**Result:** ~3x improvement in effective hashrate!

## Best Practices

### 1. Use Sequential Miner IDs

Start from 0 and increment:
```bash
# Good
--miner-id 0
--miner-id 1
--miner-id 2

# Avoid gaps (less efficient)
--miner-id 0
--miner-id 5
--miner-id 10
```

### 2. Match Worker Counts

For optimal distribution, use the same number of workers on each node:

```bash
# All nodes with 4 workers (optimal)
--miner-id 0 --workers 4
--miner-id 1 --workers 4
--miner-id 2 --workers 4
```

### 3. Scale Up to 256 Miners

The system supports up to 256 concurrent miners (IDs 0-255):

```bash
# Maximum scale
--miner-id 0    # through
--miner-id 255
```

### 4. Monitor Efficiency

Track block discovery rate across all nodes to verify proper coordination:

```bash
# Expected: blocks/time ≈ sum of individual node rates
```

## Troubleshooting

### Issue: Nodes still seem to overlap

**Cause:** Nodes using the same miner-id

**Solution:** Ensure each node has a unique miner-id (0-255)

### Issue: Low hashrate even with miner-id

**Cause:** Worker count mismatch or network issues

**Solution:** 
1. Verify all nodes have similar worker counts
2. Check network connectivity between nodes
3. Monitor CPU usage on each node

### Issue: Invalid miner-id error

**Cause:** miner-id out of range

**Solution:** Use values between 0-255 inclusive

## Technical Details

### Implementation

The nonce space partitioning is implemented in `mining/parallel_nonce_search.py`:

```python
def iter_stride(
    start_nonce: int,
    max_nonce: int,
    worker_id: int,
    workers: int,
    *,
    miner_id: int = 0
) -> Iterable[int]:
    """Generate non-overlapping nonce sequences."""
    global_worker_id = miner_id * workers + worker_id
    stride = workers * 256
    nonce = start_nonce + global_worker_id
    
    while nonce < end:
        yield nonce
        nonce += stride
```

### Testing

Run the test suite to verify correct partitioning:

```bash
python -m mining.tests.test_miner_id_partitioning
```

Expected output:
```
✓ Miner ID overlap prevention test passed
✓ Miner ID coverage test passed
✓ Miner ID default behavior test passed
✓ Miner ID stride consistency test passed
✓ Parallel search with miner ID test passed
✓ Global worker ID calculation test passed

✓ All miner_id tests passed!
```

## Conclusion

The `--miner-id` parameter enables efficient multi-node mining by eliminating duplicate work. This simple one-parameter addition can triple (or more) the effective hashrate when mining with multiple nodes to the same address.

For questions or issues, please open a GitHub issue with the `mining` label.
