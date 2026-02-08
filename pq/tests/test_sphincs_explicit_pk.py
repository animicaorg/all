"""
Test SPHINCS+ fallback explicit public key handling.

This test verifies the fix for the issue where wallets created with real liboqs
would fail verification when the system later runs with the pure-Python fallback.

The issue was that the fallback always derived pk from sk during signing,
but real liboqs generates pk and sk independently. When a wallet created
with liboqs is used with the fallback, the stored pk doesn't match the
derived pk, causing verification failures.

The fix allows the sign() function to accept an explicit pk parameter.
"""

import os
import pytest


def test_sphincs_fallback_explicit_pk():
    """
    Test that SPHINCS+ fallback can sign with an explicit public key
    that doesn't match the derived pk from sk.
    
    This simulates a wallet created with real liboqs being used with the fallback.
    """
    # Enable fallback for this test
    os.environ.setdefault("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
    
    try:
        from pq.py.algs import pure_python_fallbacks as fb
    except ImportError:
        pytest.skip("Pure python fallbacks not available")
    
    # Generate two separate keypairs
    sk1, pk1_from_keypair = fb.sphincs_shake_128s_keypair()
    sk2, pk2_independent = fb.sphincs_shake_128s_keypair()
    
    # Use sk1 but pk2 (simulates wallet with independent pk/sk like real liboqs)
    msg = b"test transaction data"
    
    # Sign with explicit pk that doesn't match sk
    sig = fb.sphincs_shake_128s_sign(msg, sk1, pk=pk2_independent)
    
    # Verification should succeed with the explicit pk
    result = fb.sphincs_shake_128s_verify(msg, sig, pk2_independent)
    assert result is True, "Verification should succeed with explicit pk"
    
    # Verification should fail with the "wrong" pk (the one derived from sk1)
    result_wrong = fb.sphincs_shake_128s_verify(msg, sig, pk1_from_keypair)
    assert result_wrong is False, "Verification should fail with derived pk when explicit pk was used"


def test_sphincs_fallback_backward_compat_no_pk():
    """
    Test backward compatibility: when pk is not provided, it should be derived from sk.
    
    This ensures old code that doesn't pass pk still works.
    """
    os.environ.setdefault("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
    
    try:
        from pq.py.algs import pure_python_fallbacks as fb
    except ImportError:
        pytest.skip("Pure python fallbacks not available")
    
    # Generate a keypair
    sk, pk_expected = fb.sphincs_shake_128s_keypair()
    
    msg = b"backward compat test"
    
    # Sign without providing pk (old behavior - should derive it)
    sig = fb.sphincs_shake_128s_sign(msg, sk, pk=None)
    
    # Should verify with the keypair's pk
    result = fb.sphincs_shake_128s_verify(msg, sig, pk_expected)
    assert result is True, "Backward compat: sign without pk should derive it and verify"


def test_dilithium3_fallback_explicit_pk():
    """
    Test that Dilithium3 fallback also supports explicit pk parameter.
    """
    os.environ.setdefault("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
    
    try:
        from pq.py.algs import pure_python_fallbacks as fb
    except ImportError:
        pytest.skip("Pure python fallbacks not available")
    
    # Generate two separate keypairs
    sk1, pk1_from_keypair = fb.dilithium3_keypair()
    sk2, pk2_independent = fb.dilithium3_keypair()
    
    # Use sk1 but pk2 (simulates wallet with independent pk/sk like real liboqs)
    msg = b"test transaction data"
    
    # Sign with explicit pk that doesn't match sk
    sig = fb.dilithium3_sign(msg, sk1, pk=pk2_independent)
    
    # Verification should succeed with the explicit pk
    result = fb.dilithium3_verify(msg, sig, pk2_independent)
    assert result is True, "Dilithium3: Verification should succeed with explicit pk"


def test_sphincs_sign_via_higher_level_api():
    """
    Test that the fix works through the higher-level pq.py.sign API.
    """
    os.environ.setdefault("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
    
    try:
        from pq.py.keygen import keygen_sig
        from pq.py.sign import sign_detached, verify_detached
    except ImportError:
        pytest.skip("PQ modules not available")
    
    # Generate a keypair
    kp = keygen_sig(4098)  # sphincs_shake_128s
    
    msg = b"high-level API test"
    
    # Sign with explicit pk through sign_detached
    sig = sign_detached(
        msg,
        alg=4098,
        sk=kp.secret_key,
        pk=kp.public_key,
        domain="test",
        prehash="sha3-512"
    )
    
    # Verify
    result = verify_detached(
        msg,
        sig,
        kp.public_key,
        domain="test",
        prehash="sha3-512"
    )
    
    assert result is True, "High-level API: verification should succeed"


def test_transaction_signing_with_fallback():
    """
    Test the complete transaction signing flow with the fallback.
    
    This is the actual use case that was failing in the bug report.
    """
    os.environ.setdefault("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
    
    try:
        from pq.py.keygen import keygen_sig
        from python.animica.tx.signing import pq_sign_tx, pq_verify_tx, ChainContext
    except ImportError:
        pytest.skip("Transaction signing modules not available")
    
    # Generate a wallet
    kp = keygen_sig(4098)  # sphincs_shake_128s
    
    # Create a transaction body
    body = {
        'from': kp.address,
        'to': 'anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz',
        'value': 10000000000,
        'gasLimit': 21000,
        'maxFee': 1,
        'data': b'',
        'chainId': 1,
        'nonce': 0,
        'validAfter': 2,
        'validUntil': 122,
        'salt': os.urandom(16)
    }
    
    # Create chain context
    chain_ctx = ChainContext(
        chain_id=1,
        genesis_hash=bytes.fromhex('8ec4a0b923005e9039b815e526bd1b99cf6c11e21b6b9993a20dc7e6a6c79fdc'),
        network='testnet',
        fork_id=3236727352,
        domain='tx',
        prehash='sha3-512'
    )
    
    # Sign transaction
    pq_sig = pq_sign_tx(body, kp.secret_key, kp.public_key, kp.alg_id, chain_ctx)
    
    # Verify transaction
    verify_result = pq_verify_tx(body, pq_sig, kp.public_key, chain_ctx, from_addr=kp.address)
    
    assert verify_result.ok is True, f"Transaction verification should succeed. Reason: {verify_result.reason}"
