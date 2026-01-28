# Chain Reset Implementation - Summary

## Overview

This implementation successfully resets the Animica blockchain from a new genesis hash, allowing the chain to start fresh from block 0 while maintaining chain_id=1.

## Implementation Details

### New Genesis Hash
```
0xec3915d93db8586ea7a11e4deb98ca21317ee3772dd1e4a0fd78cb923aa07ca0
```

### Changes Made

#### 1. Genesis Files
- **core/genesis/mainnet.json**: Updated with new timestamp (`2026-01-28T17:18:22Z`), new beacon seed, and genesis version `reset-2026-01-28b`
- **core/genesis/genesis.json**: Updated to match mainnet genesis (canonical reference)

#### 2. Network Parameters
- **core/network_params.py**: Updated `MAINNET_GENESIS_HASH_HEX` constant to reflect new genesis hash

#### 3. Chain Metadata
- **spec/chains.json**: Updated mainnet entry with new genesis hash and timestamp
- **chains/animica.mainnet.json**: Updated genesis metadata

#### 4. P2P Infrastructure
- **p2p/checkpoints/builtin.py**: Updated mainnet genesis checkpoint
- **p2p/checkpoints/tests/test_builtin.py**: Updated test expectations for new genesis

#### 5. Tests
- **scripts/tests/test_update_genesis_hash.py**: Updated expected genesis hash validation

#### 6. Documentation
- **docs/CHAIN_RESET.md**: Updated with new genesis details
- **docs/VERIFIER_NODE_RESTART.md**: Updated genesis hash reference
- **docs/README.md**: Updated chain reset notice
- **CHAIN_RESET_2026-01-28b.md**: New comprehensive guide for node operators

## Validation

### Test Results
All comprehensive validation tests pass:

1. **Genesis File Loading**: ✓
   - Mainnet genesis loads correctly
   - Testnet genesis unaffected
   - Devnet genesis unaffected

2. **Hash Consistency**: ✓
   - Genesis hash computation is deterministic
   - Multiple computations yield identical results
   - Hash matches across all reference points

3. **Network Parameters**: ✓
   - `network_params.py` matches genesis file
   - `get_pinned_genesis_hash()` returns correct hash
   - Chain ID correctly set to 1

4. **P2P Checkpoints**: ✓
   - Genesis checkpoint matches genesis hash
   - Checkpoint height is 0
   - Built-in checkpoints function correctly

5. **Database Initialization**: ✓
   - Genesis loads successfully into new database
   - Head height is 0
   - State root correctly computed
   - Chain ID correctly stored

6. **Metadata Files**: ✓
   - `spec/chains.json` matches genesis
   - `chains/animica.mainnet.json` matches genesis
   - All metadata files consistent

7. **Update Script**: ✓
   - `update_genesis_hash.py` works correctly
   - Detects genesis mismatches
   - Updates database correctly

### Code Quality
- **Code Review**: No issues found
- **Security Scan**: No vulnerabilities detected (no new code logic)
- **Test Coverage**: All affected areas covered by tests

## Node Operator Instructions

### Quick Start (Recommended)
```bash
animica node down
animica node up --auto-reset-genesis-mismatch
```

### Manual Reset
```bash
animica node down
animica node reset --network mainnet --yes
animica node up
```

### Verification
```bash
animica rpc call chain.getBlock '{"params": [0]}'
# Should show genesis hash: 0xec3915d93db8586ea7a11e4deb98ca21317ee3772dd1e4a0fd78cb923aa07ca0
```

## Technical Details

### Genesis Properties
| Property | Value |
|----------|-------|
| Genesis Hash | `0xec3915d93db8586ea7a11e4deb98ca21317ee3772dd1e4a0fd78cb923aa07ca0` |
| State Root | `0xdb9a6a1ef950c0a8a9b6f4b0b9ebbd80f0e1c23d7ecb1ebfd01c72f91e4a04a2` |
| Beacon Seed | `0x01e8e5daac8677a364752309a0595721a7079f3685fccbba2bb16293405a225c` |
| Genesis Time | `2026-01-28T17:18:22Z` |
| Genesis Version | `reset-2026-01-28b` |
| Chain ID | 1 |
| Initial Theta | 1,000,000 μ-nats |
| Gamma Cap | 2,000,000 μ-nats |

### Premine Allocation
Total: 81,000,000 ANM allocated to:
- Treasury: 39,600,000 ANM
- AICF: 20,250,000 ANM
- Foundation: 8,100,000 ANM
- Faucet: 3,240,000 ANM
- Dev Reserve: 9,810,000 ANM

Address: `anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz`

## Impact Analysis

### Breaking Changes
- **Hard Reset**: All nodes must reset their chain data
- **P2P Enforcement**: Nodes refuse connections from peers with old genesis
- **Data Loss**: Old chain data becomes invalid (except premine allocation)

### No Changes To
- Chain ID (remains 1)
- Premine allocation (unchanged)
- Consensus parameters (theta, gamma)
- Network infrastructure (ports, protocols)
- Wallet keys (stored separately)

## Files Modified

### Core
- core/genesis/mainnet.json
- core/genesis/genesis.json
- core/network_params.py

### Metadata
- spec/chains.json
- chains/animica.mainnet.json

### P2P
- p2p/checkpoints/builtin.py
- p2p/checkpoints/tests/test_builtin.py

### Tests
- scripts/tests/test_update_genesis_hash.py

### Documentation
- docs/CHAIN_RESET.md
- docs/VERIFIER_NODE_RESTART.md
- docs/README.md
- CHAIN_RESET_2026-01-28b.md (new)

## Next Steps

1. **Merge PR**: Once approved, merge to main branch
2. **Deploy**: Node operators should pull latest code
3. **Reset Nodes**: Follow instructions in CHAIN_RESET_2026-01-28b.md
4. **Monitor**: Watch for P2P connectivity and sync status
5. **Support**: Help node operators with any issues

## References

- [Chain Reset Guide](CHAIN_RESET_2026-01-28b.md) - Comprehensive instructions
- [Genesis Loader](core/genesis/loader.py) - Genesis loading implementation
- [Network Parameters](core/network_params.py) - Genesis hash constants
- [Update Script](scripts/update_genesis_hash.py) - Genesis hash updater

## Conclusion

The chain reset implementation is complete, tested, and ready for deployment. All validation tests pass, and comprehensive documentation is provided for node operators. The implementation ensures a clean, deterministic genesis that all nodes can independently verify.
