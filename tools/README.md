# Animica Tools

This directory contains utility scripts and tools for managing and debugging Animica nodes.

## Balance Management

### check_balance_inflation.py

Detect balance inflation for a single address. Useful for quick checks and manual inspection.

**Usage:**
```bash
python tools/check_balance_inflation.py \
  --rpc http://localhost:8545 \
  --address anim1zqquzgffx7raqljy3veg024ph8m8e2cyax8m98uzean8r46xskf09mc4a6avv
```

**Output:**
```
⚠️  INFLATION DETECTED!
Inflation factor: 5x
Current balance: 464,100 ANM
Corrected balance: 92,820 ANM

RECOMMENDATION:
1. Update to the latest version
2. Apply balance correction (divide by 5)
3. Monitor logs for rebuilds
```

### correct_balance_inflation.py

**NEW**: Automatically detect and correct balance inflation across all accounts.

**Features:**
- Scans entire state DB for inflated balances
- Applies corrections in batch
- Generates audit trail
- Supports dry-run mode
- Requires explicit confirmation

**Usage:**
```bash
# Dry run (detection only)
python tools/correct_balance_inflation.py \
  --db-path ~/.animica/chain-1337/state.db \
  --dry-run

# Apply corrections
python tools/correct_balance_inflation.py \
  --db-path ~/.animica/chain-1337/state.db \
  --apply
```

**See also:** `BALANCE_CORRECTION_GUIDE.md` for complete documentation.

## Performance

### bench_block_processing.py

Benchmark block processing performance.

**Usage:**
```bash
python tools/bench_block_processing.py \
  --blocks 100 \
  --db-path ~/.animica/chain-1337
```

## Debugging

### compare_pq_debug.py

Compare PQ (Post-Quantum) signature debugging output between different implementations.

**Usage:**
```bash
python tools/compare_pq_debug.py \
  --file1 debug_output1.txt \
  --file2 debug_output2.txt
```

## Contributing

When adding new tools:
1. Add a brief description to this README
2. Include usage examples
3. Document any dependencies
4. Add error handling for common cases
5. Follow existing patterns for argument parsing
