# Quick Reference: Mining Configuration Changes

## TL;DR

Mining is now **3x easier** with **10x more nonce attempts**. No configuration changes needed for local/devnet. Production can override with environment variables.

## What Changed

| Setting | Before | After | Impact |
|---------|--------|-------|--------|
| Starting Difficulty | 3.0 nats | 1.0 nat | 3x easier to mine |
| Max Nonce per Window | 1,000,000 | 10,000,000 | 10x more attempts |
| Total Nonce Search | 5,000,000 | 50,000,000 | 10x larger space |
| Difficulty Growth | Fast | Slow | ~35% gentler |

## For Users

### Local/Devnet Mining (No Action Needed)

Just mine as usual:
```bash
animica mine-blocks --count 100
```

Mining will now succeed reliably through 100+ blocks instead of failing at 10-20.

### Production Mining (Optional Override)

If you want higher difficulty for mainnet:

```bash
# Use higher starting difficulty
export ANIMICA_DEFAULT_THETA_MICRO=5000000  # 5.0 nats

# Or even higher for very high hashrate
export ANIMICA_DEFAULT_THETA_MICRO=10000000  # 10.0 nats

# Then start mining
animica mine-blocks --count 1000
```

### Testing the Changes

Verify new configuration:
```bash
python3 test_lower_difficulty_config.py
```

Expected output:
```
✅ All tests passed!
```

## Environment Variables

All existing environment variables still work:

```bash
# Override defaults
export ANIMICA_DEFAULT_THETA_MICRO=2000000      # Custom difficulty
export ANIMICA_MINER_MAX_NONCE=5000000          # Custom max nonce
export ANIMICA_MINER_MAX_TOTAL_NONCE=25000000   # Custom total nonce
```

## Troubleshooting

### Still Getting PoW Failures?

Try even lower difficulty:
```bash
export ANIMICA_DEFAULT_THETA_MICRO=500000  # 0.5 nats (very easy)
```

Or increase nonce limits even more:
```bash
export ANIMICA_MINER_MAX_NONCE=50000000      # 50M attempts
export ANIMICA_MINER_MAX_TOTAL_NONCE=200000000  # 200M total
```

### Mining Too Fast?

For production mainnet, increase difficulty:
```bash
export ANIMICA_DEFAULT_THETA_MICRO=10000000  # 10.0 nats (harder)
```

### Check Current Settings

```bash
python3 -c "
import os
print(f'Theta: {int(os.getenv(\"ANIMICA_DEFAULT_THETA_MICRO\", \"1000000\"))/1e6:.1f} nats')
print(f'Max Nonce: {int(os.getenv(\"ANIMICA_MINER_MAX_NONCE\", \"10000000\")):,}')
print(f'Total Nonce: {int(os.getenv(\"ANIMICA_MINER_MAX_TOTAL_NONCE\", \"50000000\")):,}')
"
```

## Benefits

✅ **Easier Local Mining**: 3x easier to start mining  
✅ **More Resilient**: 10x more nonce attempts  
✅ **Stable Growth**: Difficulty increases gradually  
✅ **Backwards Compatible**: All overrides still work  
✅ **Zero Config**: Works out of the box  

## See Also

- [LOWER_DIFFICULTY_IMPLEMENTATION.md](LOWER_DIFFICULTY_IMPLEMENTATION.md) - Full implementation details
- [MINING_POW_FAILURE_FIX_SUMMARY.md](MINING_POW_FAILURE_FIX_SUMMARY.md) - Previous dt_seconds clamping fix

## Questions?

If mining still fails after these changes:
1. Check your environment variables aren't overriding to high values
2. Try the troubleshooting steps above
3. Open an issue with your configuration and logs
