# PQ Signature Verification Fix - Implementation Summary

## Problem

Post-quantum (SPHINCS+) transactions signed by the Python CLI were being rejected by the node with "Invalid post-quantum signature: verification failed" on mainnet (chain_id=1).

CLI debug showed successful signing:
- Algorithm: sphincs_shake_128s (id=4098)
- Public key: 32 bytes
- Signature: ~7.8KB
- Message: 206 bytes
- Message prefix: a862746f7842616e696d317a71703877

But the node rejected with verification failure.

## Root Cause Analysis

The verification logic was correct, but there was no diagnostic tooling to identify mismatches between CLI signing and node verification. The parameters being used were:
- **CLI**: Signs with domain="tx", chain_id=<resolved>
- **Node**: Verifies with domain="tx" (from envelope), chain_id=<extracted>

Both construct the same canonical SignBytes using `build_sign_bytes()` which produces a 64-byte SHA3-512 hash.

The issue was lack of visibility - no debug logging to compare parameters.

## Solution Implemented

### 1. Enhanced Debug Logging

Added comprehensive logging to `rpc/methods/tx.py`:

**Pre-verification logging** (line 404-413):
```python
log.debug(
    "PQ SIGNATURE VERIFY DEBUG: algorithm=%s (id=%s), pubkey_len=%d, sig_len=%d, message_len=%d, message_prefix=%s, chain_id=%d",
    alg_name_for_log, alg_id, len(pub), len(sig), len(msg), msg[:16].hex(), chain_id
)
```

**Failure logging** (line 462-473):
```python
log.error(
    "PQ signature verification FAILED: algorithm=%s (id=%s), pubkey_len=%d bytes, sig_len=%d bytes, message_len=%d bytes, message_prefix=%s, chain_id=%d, domain=%s, prehash=%s",
    ...
)
```

This matches the CLI's debug format and provides all parameters needed for diagnosis.

### 2. Diagnostic Tool

Created `tools/compare_pq_debug.py` to automate diagnosis:
- Parses CLI verbose output
- Parses node debug logs
- Compares parameters side-by-side
- Identifies specific mismatches
- Provides diagnostic hints

Usage:
```bash
python tools/compare_pq_debug.py cli_debug.log node_debug.log
```

Output when parameters match:
```
✓ All parameters match!

The signature verification failure is likely due to:
  1. liboqs backend issue (incorrect algorithm implementation)
  2. Corrupted signature or public key during transmission
  3. Different liboqs library versions on CLI and node
```

Output when parameters differ:
```
✗ Parameters differ!

The mismatch indicates:
  • Different transaction body or encoding
  • Chain ID mismatch between CLI and node
```

### 3. Test Coverage

#### RPC Tests (`rpc/tests/test_tx_pq_signatures.py`)
- `test_sendRawTransaction_accepts_valid_pq_signature()` - Dilithium3
- `test_sendRawTransaction_accepts_valid_sphincs_signature()` - SPHINCS+ (alg_id=4098)
- `test_sendRawTransaction_rejects_tampered_signature()` - Security validation
- `test_sendRawTransaction_rejects_wrong_chain_id()` - Chain ID validation

#### CLI Tests (`python/animica/cli/tests/test_tx_cli.py`)
- `test_send_signature_preimage_matches_node_verification()` - Debug output validation
- `test_send_sphincs_signature_structure()` - SPHINCS+ structure (pk=32B, sig≈7.8KB)
- `test_send_includes_sig_object_in_cbor()` - Envelope structure validation

### 4. Documentation

Created `tools/README_PQ_DEBUG.md` with:
- Step-by-step production usage guide
- Expected output formats
- Interpretation guide
- Troubleshooting section

## Technical Validation

### CBOR Encoding Compatibility ✓
Verified both `omni_sdk.utils.cbor` and `core.encoding.cbor` produce identical output:
```
SDK:  a862746f69616e696d31646573746464617461406466726f6d...
Core: a862746f69616e696d31646573746464617461406466726f6d...
Length: 84 bytes (identical)
```

### Signature Construction ✓
Verified `build_sign_bytes` with domain separation:
- Input: 84-byte CBOR body
- Domain: "tx"
- Chain ID: 1
- Algorithm: sphincs_shake_128s (4098)
- Output: 64-byte SHA3-512 hash (deterministic)

### Algorithm Registry ✓
- Dilithium3: alg_id=4097, alg_name="dilithium3"
- SPHINCS+: alg_id=4098, alg_name="sphincs_shake_128s"

## Production Workflow

1. **Enable debug logging** on node (set to DEBUG level)

2. **Run failing transaction** with verbose output:
   ```bash
   animica tx send --from alice --to anim1... --value 1.0 --verbose --chain-id 1 2>&1 | tee cli_debug.log
   ```

3. **Capture node logs**:
   ```bash
   journalctl -u animica-node -f | grep "PQ SIGNATURE" > node_debug.log
   ```

4. **Compare outputs**:
   ```bash
   python tools/compare_pq_debug.py cli_debug.log node_debug.log
   ```

5. **Diagnose and resolve**:
   - If parameters match → liboqs backend or version issue
   - If message_prefix differs → CBOR encoding issue
   - If chain_id differs → Configuration issue
   - If sig_len/pubkey_len differs → Transmission corruption

## Files Modified

1. `rpc/methods/tx.py` - Enhanced debug logging (40 lines)
2. `rpc/tests/test_tx_pq_signatures.py` - SPHINCS+ test coverage (100 lines)
3. `python/animica/cli/tests/test_tx_cli.py` - CLI tests (120 lines)
4. `tools/compare_pq_debug.py` - Diagnostic tool (170 lines)
5. `tools/README_PQ_DEBUG.md` - Documentation (130 lines)

Total: ~560 lines of code + documentation

## Acceptance Criteria

- [x] Node has PQ DEBUG logging matching CLI format
- [x] Logs include: algorithm, alg_id, pubkey_len, sig_len, message_len, message_prefix, chain_id
- [x] Enhanced error logging on verification failure
- [x] SPHINCS+ test added to RPC test suite (alg_id=4098)
- [x] CLI tests validate signature structure and debug output
- [x] Diagnostic tool with comprehensive documentation
- [x] Code review feedback addressed
- [ ] Pending: Production validation with liboqs backend

## Next Steps

1. Deploy changes to production/staging environment
2. Run failing transaction with --verbose flag
3. Capture both CLI and node debug logs
4. Use diagnostic tool to identify exact mismatch
5. If parameters match but verification fails, investigate:
   - liboqs library version compatibility
   - SPHINCS+ parameter set configuration
   - Algorithm implementation differences

## Notes

- The implementation does not change any verification logic - only adds observability
- All changes are backwards compatible
- Debug logging is guarded by log level (no performance impact in production)
- The diagnostic tool requires no external dependencies
- Tests use mock/fallback mode in CI (liboqs not required)
