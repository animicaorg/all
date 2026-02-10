"""
Test to verify that coinbase transaction payload extraction works correctly
after fixing the discriminated union payload structure issue.

This test specifically checks:
1. Direct Tx object payload extraction (TxTransfer object)
2. Serialized Tx dict payload extraction (discriminated union with "t" and "v" fields)
"""

import sys
sys.path.insert(0, "/home/runner/work/all/all")

from core.types.tx import UnsignedTx, Tx, TxTransfer

def _get(obj, *names, default=None):
    """Same _get implementation from transfers.py"""
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def extract_amount_from_tx(tx):
    """
    Extract amount using the new fixed logic from transfers.py.
    This mimics the exact extraction logic after the fix.
    """
    # Extract transfer amount from tx (check multiple locations for compatibility)
    amount = _get(tx, "value", "amount")
    if amount is None:
        # Try nested structure: tx.unsigned.payload.amount (canonical Tx format)
        unsigned = _get(tx, "unsigned", "tx")  # Also check "tx" for serialized form
        if unsigned is not None:
            payload = _get(unsigned, "payload")
            if payload is not None:
                # For discriminated union payloads {"t": kind, "v": value}, extract the "v" field
                payload_value = _get(payload, "v")
                if payload_value is not None:
                    # This is a serialized/dict payload, get amount from "v" field
                    amount = _get(payload_value, "amount", "value")
                else:
                    # This is a direct TxTransfer object, get amount directly
                    amount = _get(payload, "amount", "value")
    return amount


def extract_to_from_tx(tx):
    """
    Extract recipient address using the new fixed logic from transfers.py.
    This mimics the exact extraction logic after the fix.
    """
    # Extract recipient address from tx (check multiple locations for compatibility)
    to = _get(tx, "to", "recipient", "to_address")
    if to is None:
        # Try nested structure: tx.unsigned.payload.to (canonical Tx format)
        unsigned = _get(tx, "unsigned", "tx")  # Also check "tx" for serialized form
        if unsigned is not None:
            payload = _get(unsigned, "payload")
            if payload is not None:
                # For discriminated union payloads {"t": kind, "v": value}, extract the "v" field
                payload_value = _get(payload, "v")
                if payload_value is not None:
                    # This is a serialized/dict payload, get to from "v" field
                    to = _get(payload_value, "to", "recipient")
                else:
                    # This is a direct TxTransfer object, get to directly
                    to = _get(payload, "to", "recipient")
    return to


def test_direct_tx_object():
    """Test extraction from direct Tx object (not serialized)."""
    print("\n=== Test 1: Direct Tx object (TxTransfer payload) ===")
    
    # Build coinbase transaction
    unsigned_tx = UnsignedTx.build_coinbase(
        chain_id=1337,
        height=1,
        to=b'\x01' * 32,
        amount=100_000_000_000,  # 100 ANM
    )
    coinbase_tx = Tx(unsigned=unsigned_tx, sigs=tuple())
    
    # Extract amount and to
    amount = extract_amount_from_tx(coinbase_tx)
    to = extract_to_from_tx(coinbase_tx)
    
    print(f"  Extracted amount: {amount}")
    print(f"  Expected amount:  {100_000_000_000}")
    print(f"  Extracted to:     {to.hex()[:32]}...")
    print(f"  Expected to:      {(b'\\x01' * 32).hex()[:32]}...")
    
    assert amount == 100_000_000_000, f"Amount mismatch: {amount} != 100_000_000_000"
    assert to == b'\x01' * 32, f"To address mismatch"
    
    print("  ✓ PASS: Direct Tx object extraction works")
    return True


def test_serialized_tx_dict():
    """Test extraction from serialized Tx dict (discriminated union)."""
    print("\n=== Test 2: Serialized Tx dict (discriminated union payload) ===")
    
    # Build and serialize coinbase transaction
    unsigned_tx = UnsignedTx.build_coinbase(
        chain_id=1337,
        height=1,
        to=b'\x02' * 32,
        amount=200_000_000_000,  # 200 ANM
    )
    coinbase_tx = Tx(unsigned=unsigned_tx, sigs=tuple())
    
    # Serialize to dict (this is what happens when tx goes through CBOR or storage)
    tx_dict = coinbase_tx.to_obj()
    
    print(f"  Serialized payload structure: {list(tx_dict['tx']['payload'].keys())}")
    print(f"  Payload type tag: {tx_dict['tx']['payload']['t']}")
    print(f"  Payload value keys: {list(tx_dict['tx']['payload']['v'].keys())}")
    
    # Extract amount and to from serialized form
    amount = extract_amount_from_tx(tx_dict)
    to = extract_to_from_tx(tx_dict)
    
    print(f"  Extracted amount: {amount}")
    print(f"  Expected amount:  {200_000_000_000}")
    print(f"  Extracted to:     {to.hex()[:32]}...")
    print(f"  Expected to:      {(b'\\x02' * 32).hex()[:32]}...")
    
    assert amount == 200_000_000_000, f"Amount mismatch: {amount} != 200_000_000_000"
    assert to == b'\x02' * 32, f"To address mismatch"
    
    print("  ✓ PASS: Serialized Tx dict extraction works")
    return True


def test_old_code_fails_on_serialized():
    """Demonstrate that the old code fails on serialized Tx."""
    print("\n=== Test 3: Old code (without fix) fails on serialized Tx ===")
    
    # Build and serialize
    unsigned_tx = UnsignedTx.build_coinbase(
        chain_id=1337,
        height=1,
        to=b'\x03' * 32,
        amount=300_000_000_000,
    )
    coinbase_tx = Tx(unsigned=unsigned_tx, sigs=tuple())
    tx_dict = coinbase_tx.to_obj()
    
    # Old extraction logic (without the fix)
    unsigned = _get(tx_dict, "unsigned", "tx")
    payload = _get(unsigned, "payload")
    amount_old = _get(payload, "amount", "value")  # This returns None!
    
    print(f"  Old code extracted amount: {amount_old}")
    print(f"  Expected amount:           {300_000_000_000}")
    print(f"  Result: Old code returns None → defaults to 0 → NO REWARDS!")
    
    assert amount_old is None, "Old code should fail to extract amount from discriminated union"
    
    print("  ✓ PASS: Confirmed old code fails (returns None)")
    return True


def main():
    """Run all tests."""
    print("\n" + "="*70)
    print("REWARD PAYLOAD EXTRACTION FIX VERIFICATION")
    print("="*70)
    
    try:
        test_direct_tx_object()
        test_serialized_tx_dict()
        test_old_code_fails_on_serialized()
        
        print("\n" + "="*70)
        print("ALL TESTS PASSED ✓")
        print("="*70)
        print("\nThe fix correctly handles both:")
        print("  1. Direct Tx objects (TxTransfer payload)")
        print("  2. Serialized Tx dicts (discriminated union with 't' and 'v' fields)")
        print("\nRewards will now be credited correctly!")
        return 0
        
    except AssertionError as e:
        print("\n" + "="*70)
        print("TEST FAILED ✗")
        print("="*70)
        print(f"Error: {e}")
        return 1
    except Exception as e:
        print("\n" + "="*70)
        print("TEST ERROR ✗")
        print("="*70)
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
