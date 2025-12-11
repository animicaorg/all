#!/usr/bin/env python3
"""
Compare PQ signature debug output from CLI and node to diagnose mismatches.

Usage:
    python compare_pq_debug.py CLI_OUTPUT NODE_OUTPUT

Where CLI_OUTPUT and NODE_OUTPUT are text files containing the debug logs.

Example CLI output:
    PQ SIGNATURE DEBUG
      algorithm: sphincs_shake_128s (id=4098)
      pubkey_len: 32 bytes
      sig_len: 7856 bytes
      message_len: 206 bytes
      message_prefix: a862746f7842616e696d317a71703877
      chain_id: 1

Example Node output:
    PQ SIGNATURE VERIFY DEBUG: algorithm=sphincs_shake_128s (id=4098), pubkey_len=32, sig_len=7856, message_len=206, message_prefix=a862746f7842616e696d317a71703877, chain_id=1
"""

import re
import sys


def parse_cli_output(text):
    """Parse CLI debug output."""
    result = {}
    
    # Match algorithm line
    m = re.search(r'algorithm:\s*(\w+)\s*\(id=(\d+)\)', text)
    if m:
        result['algorithm'] = m.group(1)
        result['alg_id'] = int(m.group(2))
    
    # Match other fields
    for field in ['pubkey_len', 'sig_len', 'message_len']:
        m = re.search(rf'{field}:\s*(\d+)', text)
        if m:
            result[field] = int(m.group(1))
    
    # Match message_prefix
    m = re.search(r'message_prefix:\s*([0-9a-fA-F]+)', text)
    if m:
        result['message_prefix'] = m.group(1).lower()
    
    # Match chain_id
    m = re.search(r'chain_id:\s*(\d+)', text)
    if m:
        result['chain_id'] = int(m.group(1))
    
    return result


# Compile regex patterns at module level for performance
_NODE_LOG_PATTERN = re.compile(
    r'algorithm=(\w+)\s*\(id=(\d+)\),\s*'
    r'pubkey_len=(\d+),?\s*'
    r'sig_len=(\d+),?\s*'
    r'message_len=(\d+),?\s*'
    r'message_prefix=([0-9a-fA-F]+),?\s*'
    r'chain_id=(\d+)'
)


def parse_node_output(text):
    """Parse node debug output."""
    result = {}
    
    # Look for the log line
    m = _NODE_LOG_PATTERN.search(text)
    
    if m:
        result['algorithm'] = m.group(1)
        result['alg_id'] = int(m.group(2))
        result['pubkey_len'] = int(m.group(3))
        result['sig_len'] = int(m.group(4))
        result['message_len'] = int(m.group(5))
        result['message_prefix'] = m.group(6).lower()
        result['chain_id'] = int(m.group(7))
    
    return result


def compare_params(cli, node):
    """Compare CLI and node parameters and report differences."""
    print("=" * 70)
    print("PQ Signature Debug Comparison")
    print("=" * 70)
    print()
    
    if not cli:
        print("❌ Could not parse CLI output")
        return False
    
    if not node:
        print("❌ Could not parse node output")
        return False
    
    all_match = True
    
    # Compare each field
    for key in ['algorithm', 'alg_id', 'pubkey_len', 'sig_len', 'message_len', 'message_prefix', 'chain_id']:
        cli_val = cli.get(key, "NOT FOUND")
        node_val = node.get(key, "NOT FOUND")
        
        match = cli_val == node_val
        symbol = "✓" if match else "✗"
        
        print(f"{symbol} {key:20s}: CLI={cli_val!r:30s} Node={node_val!r}")
        
        if not match:
            all_match = False
    
    print()
    print("=" * 70)
    
    if all_match:
        print("✓ All parameters match!")
        print()
        print("The signature verification failure is likely due to:")
        print("  1. liboqs backend issue (incorrect algorithm implementation)")
        print("  2. Corrupted signature or public key during transmission")
        print("  3. Different liboqs library versions on CLI and node")
        return True
    else:
        print("✗ Parameters differ!")
        print()
        print("The mismatch indicates:")
        
        # Map mismatches to diagnostic messages
        mismatch_diagnostics = {
            'message_prefix': "  • Different transaction body or encoding",
            'chain_id': "  • Chain ID mismatch between CLI and node",
            'alg_id': "  • Algorithm ID mismatch",
            'pubkey_len': "  • Public key length mismatch",
            'sig_len': "  • Signature length mismatch",
        }
        
        for field, diagnostic in mismatch_diagnostics.items():
            if cli.get(field) != node.get(field):
                print(diagnostic)
        
        return False


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    
    cli_file = sys.argv[1]
    node_file = sys.argv[2]
    
    try:
        with open(cli_file, 'r') as f:
            cli_text = f.read()
    except Exception as e:
        print(f"Error reading CLI output file: {e}")
        sys.exit(1)
    
    try:
        with open(node_file, 'r') as f:
            node_text = f.read()
    except Exception as e:
        print(f"Error reading node output file: {e}")
        sys.exit(1)
    
    cli_params = parse_cli_output(cli_text)
    node_params = parse_node_output(node_text)
    
    success = compare_params(cli_params, node_params)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
