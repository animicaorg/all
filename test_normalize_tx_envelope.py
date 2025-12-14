"""
Test to verify that _normalize_tx_envelope correctly converts CLI format to core format.
"""

def test_normalize_cli_format():
    """
    Test that a transaction in CLI format can be normalized to core format.
    """
    import sys
    sys.path.insert(0, '/home/runner/work/all/all')
    
    from rpc.methods.miner import _normalize_tx_envelope, _construct_tx_from_dict
    
    # Simulate what the CLI sends (simplified for testing)
    cli_envelope = {
        "body": {
            "chainId": 1337,
            "from": "anim1zqqnwdd4fm7lvlsryjcf3shh9gegwrqp7znr0muew2muy8ehyk4pqhchalapl",
            "to": "anim1zqqupmns7j5k5ap7xglwwmh9fvkfwuj85psl9nwxf9ujs8v5fm5rg9cksd2mn",
            "nonce": 0,
            "value": 1000000000,  # 1 ANM
            "gasLimit": 21000,
            "maxFee": 1,
            "data": b"",
        },
        "sig": {
            "algId": 0x1001,  # dilithium3
            "pk": b"\x00" * 1952,  # dummy pubkey (Dilithium3 is 1952 bytes)
            "sig": b"\x00" * 3309,  # dummy signature (Dilithium3 is 3309 bytes)
            "domain": "tx",
            "prehash": "sha3-512",
            "chainId": 1337,
        }
    }
    
    print("="*70)
    print("Testing TX envelope normalization (CLI → Core format)")
    print("="*70)
    
    print("\n1. Input (CLI format):")
    print(f"   body keys: {list(cli_envelope['body'].keys())}")
    print(f"   sig keys: {list(cli_envelope['sig'].keys())}")
    
    try:
        normalized = _normalize_tx_envelope(cli_envelope)
        print("\n2. Normalized output:")
        print(f"   top-level keys: {list(normalized.keys())}")
        
        if "tx" in normalized:
            print(f"   tx keys: {list(normalized['tx'].keys())}")
            tx_obj = normalized['tx']
            print(f"   tx.chainId: {tx_obj.get('chainId')}")
            print(f"   tx.from type: {type(tx_obj.get('from'))}")
            print(f"   tx.nonce: {tx_obj.get('nonce')}")
            if 'gas' in tx_obj:
                print(f"   tx.gas: {tx_obj['gas']}")
            if 'payload' in tx_obj:
                print(f"   tx.payload: {tx_obj['payload']}")
        
        if "sigs" in normalized:
            print(f"   sigs count: {len(normalized['sigs'])}")
            if normalized['sigs']:
                sig = normalized['sigs'][0]
                print(f"   sig[0] keys: {list(sig.keys())}")
        
        print("\n3. ✓ Normalization succeeded!")
        
        # Now try to construct a Tx from it
        print("\n4. Attempting to construct Tx from normalized envelope...")
        tx = _construct_tx_from_dict(normalized)
        if tx is not None:
            print(f"   ✓ Tx construction succeeded!")
            print(f"   Tx type: {type(tx).__name__}")
            print(f"   Tx hash: {tx.txid().hex()[:16]}...")
            return True
        else:
            print(f"   ✗ Tx construction failed (returned None)")
            return False
            
    except Exception as e:
        print(f"\n✗ Error during normalization or construction:")
        print(f"   {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    import sys
    success = test_normalize_cli_format()
    sys.exit(0 if success else 1)
