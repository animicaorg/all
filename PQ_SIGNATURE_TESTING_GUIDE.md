# Quick Testing Guide for PQ Signature Fix

## Prerequisites

1. Ensure liboqs-python is installed for PQ crypto:
   ```bash
   ./setup.sh  # or follow SETUP_LIBOQS_IMPROVEMENTS.md
   ```

2. Have a wallet with funds:
   ```bash
   animica wallet create --label alice
   # Note the address: anim1...
   ```

## Manual Testing

### 1. Dry-run Transaction (Verbose)

Test signature creation without broadcasting:

```bash
animica tx send \
  --from alice \
  --to anim1dest... \
  --value 1.0 \
  --chain-id 1 \
  --dry-run \
  --verbose
```

**Expected output:**
```
CHAIN CONTEXT DEBUG
  network: mainnet
  rpc_url: http://127.0.0.1:8545
  chain_id: 1
  chain_id_source: node auto-detect

PQ SIGNATURE DEBUG
  algorithm: dilithium3 (id=1)
  pubkey_len: 1952 bytes
  sig_len: 2420 bytes
  message_len: 82 bytes
  message_prefix: a867636861696e4964...
  chain_id: 1

=== Dry-Run Mode ===
From:       anim1...
To:         anim1dest...
Value:      1.0 ANM
Gas Limit:  21000
Max Fee:    1.0 gwei
Nonce:      0
Chain ID:   1
Tx Hash:    0x...
Raw Size:   4500 bytes

✓ Transaction built and signed (not broadcast)
```

**Validation:**
- ✓ PQ algorithm shows dilithium3 or sphincs_shake_128s
- ✓ Signature length > 2000 bytes (Dilithium3) or > 7000 bytes (SPHINCS+)
- ✓ Chain ID matches node (1 for mainnet)
- ✓ No errors

### 2. Broadcast Transaction

Actually send the transaction:

```bash
animica tx send \
  --from alice \
  --to anim1dest... \
  --value 1.0 \
  --chain-id 1
```

**Expected output:**
```
=== Transaction Submitted ===
Tx Hash: 0xabc123...
From:    anim1alice...
To:      anim1dest...
Value:   1.0 ANM

✓ Transaction broadcast successfully
```

**Validation:**
- ✓ Returns transaction hash (0x...)
- ✓ No error -32012 "Invalid post-quantum signature"
- ✓ Transaction appears in mempool/block

### 3. Verify Transaction

Check the transaction was accepted:

```bash
# Query by transaction hash
curl -X POST http://127.0.0.1:8545 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tx.getTransactionByHash",
    "params": ["0xabc123..."]
  }'
```

**Expected:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "hash": "0xabc123...",
    "from": "anim1alice...",
    "to": "anim1dest...",
    "value": 1000000000000000000,
    "nonce": 0,
    "gas": 21000,
    ...
  }
}
```

## Automated Testing

### SDK Tests

Run the PQ signature round-trip tests:

```bash
cd /home/runner/work/all/all
python3 -m pytest sdk/python/tests/test_pq_signature_roundtrip.py -v
```

**Expected:**
```
test_pq_signer_sign_tx_with_chain_id PASSED
test_sdk_sign_bytes_returns_cbor_body PASSED
test_node_verification_matches_sdk_signature PASSED
test_node_verification_rejects_flipped_signature PASSED
test_node_verification_rejects_wrong_chain_id PASSED
test_packed_signed_envelope_has_required_fields PASSED

6 passed
```

### RPC Tests

Run the node verification tests:

```bash
cd /home/runner/work/all/all
python3 -m pytest rpc/tests/test_tx_pq_signatures.py -v
```

**Expected:**
```
test_sendRawTransaction_accepts_valid_pq_signature PASSED
test_sendRawTransaction_rejects_tampered_signature PASSED
test_sendRawTransaction_rejects_wrong_chain_id PASSED
test_sendRawTransaction_requires_sig_field PASSED

4 passed
```

## Troubleshooting

### Error: -32012 Invalid post-quantum signature

**Cause:** Signature verification failed

**Debug steps:**
1. Run with `--verbose` to see signature details
2. Check chain_id matches node: `curl -X POST http://127.0.0.1:8545 -d '{"jsonrpc":"2.0","method":"chain.getChainId","params":[],"id":1}'`
3. Check PQ library is installed: `python3 -c "from pq.py import sign, verify; print('OK')"`
4. Check algorithm is supported: `python3 -c "from pq.py.registry import ALG_ID; print(ALG_ID)"`

### Error: ModuleNotFoundError: No module named 'pq'

**Fix:**
```bash
cd /home/runner/work/all/all
./setup.sh
# or
pip install -e pq/
```

### Error: Chain ID mismatch

**Cause:** Transaction chain_id doesn't match node

**Fix:**
```bash
# Check node's chain ID
animica chain info

# Use correct chain ID
animica tx send --from alice --to anim1dest... --value 1.0 --chain-id <correct-id>
```

## Node Logs

When debugging, check node logs for verification details:

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG

# Start node
animica node start

# Look for lines like:
# DEBUG:rpc.methods.tx:PQ signature verification: alg_id=1, pubkey_len=1952, sig_len=2420, msg_len=82, chain_id=1
# DEBUG:rpc.methods.tx:PQ signature verification result: PASS (domain=tx, alg=dilithium3)
```

## Success Criteria

✅ CLI dry-run shows PQ signature details with `-v`
✅ Transaction broadcasts without -32012 error
✅ Transaction hash returned and found in mempool
✅ SDK tests pass (6/6)
✅ RPC tests pass (4/4)
✅ Node logs show "verification result: PASS"
