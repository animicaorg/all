#!/usr/bin/env python3
"""
Test that demonstrates the TypeError fix in decode_tx_envelope.

This test creates a transaction envelope with proper CBOR encoding
and verifies that it can be decoded successfully.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from coretx.types import TxBody, TxAuth, TxEnvelope, TxId, TxKind
from coretx.canonical import encode_tx_envelope, decode_tx_envelope


def test_decode_with_valid_types():
    """Test that valid transaction can be encoded and decoded"""
    print("Testing transaction encoding/decoding...")
    
    # Create a valid transaction body
    body = TxBody(
        version=1,
        chain_id=1,
        nonce=42,  # This is an int
        from_addr=b"\x00" * 32,
        to_addr=b"\x01" * 32,
        value=100,
        fee=1000,
        gas_limit=21000,
        data=b"test data",
        memo="test transaction",
        timestamp=1234567890,
        kind=TxKind.TRANSFER,
    )
    
    # Create auth
    auth = TxAuth(
        scheme_id=1,
        pubkey_bytes=b"\x02" * 32,
        signature_bytes=b"\x03" * 64,
        prehash_id=1,
    )
    
    # Create envelope
    envelope = TxEnvelope(
        body=body,
        auth=auth,
        txid=TxId(bytes32=b"\x04" * 32),
    )
    
    # Encode to CBOR
    cbor_data = encode_tx_envelope(envelope)
    print(f"  Encoded to {len(cbor_data)} bytes of CBOR")
    
    # Decode back
    decoded = decode_tx_envelope(cbor_data)
    print(f"  Decoded successfully")
    
    # Verify all numeric fields are ints
    assert isinstance(decoded.body.nonce, int), f"nonce is {type(decoded.body.nonce)}, not int"
    assert isinstance(decoded.body.version, int), f"version is {type(decoded.body.version)}, not int"
    assert isinstance(decoded.body.chain_id, int), f"chain_id is {type(decoded.body.chain_id)}, not int"
    assert isinstance(decoded.body.value, int), f"value is {type(decoded.body.value)}, not int"
    assert isinstance(decoded.body.fee, int), f"fee is {type(decoded.body.fee)}, not int"
    assert isinstance(decoded.body.gas_limit, int), f"gas_limit is {type(decoded.body.gas_limit)}, not int"
    assert isinstance(decoded.body.timestamp, int), f"timestamp is {type(decoded.body.timestamp)}, not int"
    assert isinstance(decoded.auth.scheme_id, int), f"scheme_id is {type(decoded.auth.scheme_id)}, not int"
    assert isinstance(decoded.auth.prehash_id, int), f"prehash_id is {type(decoded.auth.prehash_id)}, not int"
    
    print("  ✅ All numeric fields are int type")
    
    # Verify values match
    assert decoded.body.nonce == 42
    assert decoded.body.chain_id == 1
    assert decoded.body.value == 100
    print("  ✅ All values match")
    
    return True


if __name__ == "__main__":
    try:
        success = test_decode_with_valid_types()
        if success:
            print("\n✅ Test passed: decode_tx_envelope properly handles types")
            sys.exit(0)
        else:
            print("\n❌ Test failed")
            sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
