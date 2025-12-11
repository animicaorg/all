# PQ Signature Debug Tools

This directory contains diagnostic tools for debugging post-quantum signature verification issues.

## compare_pq_debug.py

A command-line tool to compare PQ signature debug output from the CLI and node to diagnose verification mismatches.

### Purpose

When a transaction signed by the CLI is rejected by the node with "Invalid post-quantum signature: verification failed", this tool helps identify the exact cause by comparing the signing parameters (CLI) with the verification parameters (node).

### Usage

```bash
python tools/compare_pq_debug.py CLI_OUTPUT NODE_OUTPUT
```

Where:
- `CLI_OUTPUT`: Text file containing CLI debug output (from `animica tx send --verbose`)
- `NODE_OUTPUT`: Text file containing node debug output (from node logs with DEBUG level)

### Example Workflow

1. **Run CLI with verbose logging** to capture signing parameters:
   ```bash
   animica tx send \
     --from alice \
     --to anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km \
     --value 1.0 \
     --verbose \
     --chain-id 1 \
     2>&1 | tee cli_debug.log
   ```

2. **Capture node debug logs** (requires DEBUG log level):
   ```bash
   # If using systemd:
   journalctl -u animica-node -f | grep "PQ SIGNATURE" > node_debug.log
   
   # Or if using log files:
   tail -f /var/log/animica-node.log | grep "PQ SIGNATURE" > node_debug.log
   ```

3. **Compare the outputs**:
   ```bash
   python tools/compare_pq_debug.py cli_debug.log node_debug.log
   ```

### Expected Output Format

#### CLI Debug Output (--verbose flag)
```
PQ SIGNATURE DEBUG
  algorithm: sphincs_shake_128s (id=4098)
  pubkey_len: 32 bytes
  sig_len: 7856 bytes
  message_len: 206 bytes
  message_prefix: a862746f7842616e696d317a71703877
  chain_id: 1
```

#### Node Debug Output (DEBUG log level)
```
PQ SIGNATURE VERIFY DEBUG: algorithm=sphincs_shake_128s (id=4098), pubkey_len=32, sig_len=7856, message_len=206, message_prefix=a862746f7842616e696d317a71703877, chain_id=1
```

### Interpreting Results

#### All Parameters Match ✓
```
======================================================================
✓ All parameters match!

The signature verification failure is likely due to:
  1. liboqs backend issue (incorrect algorithm implementation)
  2. Corrupted signature or public key during transmission
  3. Different liboqs library versions on CLI and node
```

**Resolution**: Check liboqs installation and version on both CLI and node. Ensure they're using the same version and are correctly installed.

#### Parameters Differ ✗
```
======================================================================
✗ Parameters differ!

The mismatch indicates:
  • Different transaction body or encoding
  • Chain ID mismatch between CLI and node
```

**Resolution**: 
- **message_prefix differs**: Transaction body is encoded differently. Check CBOR encoder versions.
- **chain_id differs**: CLI and node disagree on chain ID. Verify network configuration.
- **sig_len/pubkey_len differs**: Signature or public key truncated/corrupted during transmission.
- **alg_id differs**: Algorithm mismatch. Verify wallet algorithm matches node expectations.

### Exit Codes

- `0`: All parameters match between CLI and node
- `1`: Parameters differ (mismatch detected)
- `1`: Error reading input files

### Requirements

- Python 3.7+
- No external dependencies (uses only standard library)

### Troubleshooting

#### "Could not parse CLI output"
Ensure you captured the full CLI output including the "PQ SIGNATURE DEBUG" block. Use `--verbose` flag and redirect stderr: `2>&1 | tee cli_debug.log`

#### "Could not parse node output"
Ensure node is running with DEBUG log level. Check node configuration and enable PQ signature debug logging.

#### No output captured
If the transaction succeeds, there will be no verification failure logs. This tool is specifically for debugging failed verifications.

## Related Documentation

- [PQ Signature Implementation](../pq/py/README.md)
- [CLI Transaction Commands](../python/animica/cli/README.md)
- [RPC Transaction Methods](../rpc/methods/README.md)
