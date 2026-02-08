"""
Regression test for PQ signature verification during p2p peer tx import.

This test ensures that transactions from peers are correctly verified
using the same signing preimage as local transactions, fixing the issue
where obj.get("body", obj) was extracting only the body portion.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

# Mark as asyncio-compatible
pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _allow_fake_pq(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable fake PQ backend for testing."""
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
    monkeypatch.setenv("ANIMICA_PQ_VERIFY_DEBUG", "1")


def test_verify_pq_signature_consistency_with_signing() -> None:
    """
    Test that the same preimage is used for signing and verification.
    
    Verifies that pq_sign_tx and _verify_pq_signature both use
    tx_signing_preimage() consistently.
    """
    from pq.py.keygen import keygen_sig
    from animica.tx.signing import ChainContext, pq_sign_tx, pq_verify_tx
    import cbor2
    
    kp = keygen_sig("sphincs_shake_128s")
    
    body = {
        "chainId": 1,
        "from": "anim1test",
        "to": "anim1dest",
        "nonce": 2,
        "value": 2000,
        "gasLimit": 21000,
        "maxFee": 1000000000,
        "data": b"",
    }
    
    ctx = ChainContext(
        chain_id=1,
        genesis_hash=b"\x88" * 32,
        network="devnet",
        fork_id=None,
        domain="tx",
        prehash="sha3-512",
    )
    
    # Sign (CLI path: passes body directly)
    sig = pq_sign_tx(body, kp.secret_key, kp.public_key, kp.alg_id, ctx)
    
    # Verify using the same body
    verify_result = pq_verify_tx(body, sig, kp.public_key, ctx)
    assert verify_result.ok is True
    
    # Now create envelope and verify again (p2p import path)
    envelope = {
        "body": body,
        "sig": {
            "algId": sig.alg_id,
            "pk": kp.public_key,
            "sig": sig.sig,
            "domain": sig.domain,
            "prehash": sig.prehash,
            "chainId": 1,
        },
    }
    
    # Verify with full envelope (after fix, this should work)
    verify_result_envelope = pq_verify_tx(envelope, sig, kp.public_key, ctx)
    assert verify_result_envelope.ok is True
    
    # Verify preimage hex matches
    assert verify_result.preimage_hex == verify_result_envelope.preimage_hex
    assert verify_result.sign_hash_hex == verify_result_envelope.sign_hash_hex


def test_sphincs_pubkey_and_sig_sizes() -> None:
    """Verify SPHINCS+ produces correct-sized keys and signatures."""
    from pq.py.keygen import keygen_sig
    from pq.py.registry import get_sig
    
    # Get metadata
    info = get_sig("sphincs_shake_128s")
    assert info is not None
    assert info.alg_id == 0x1002  # 4098
    assert info.pubkey_size == 64
    assert info.signature_size == 7856
    
    # Generate and check
    kp = keygen_sig("sphincs_shake_128s")
    assert len(kp.public_key) == 64
    assert len(kp.secret_key) == 64


def test_extract_body_handles_normalized_envelope() -> None:
    """
    Test that _extract_body in signing.py handles normalized envelopes.
    
    After tx normalization, envelopes have {"tx": {...}, "sigs": [...]}
    instead of {"body": {...}, "sig": {...}}.
    """
    from animica.tx.signing import _extract_body
    
    body_content = {
        "chainId": 1,
        "from": "anim1test",
        "to": "anim1dest",
        "nonce": 3,
        "value": 3000,
        "gasLimit": 21000,
        "maxFee": 1000000000,
        "data": b"",
    }
    
    # Test with "body" key (CLI format)
    envelope_body = {"body": body_content, "sig": {}}
    extracted_body = _extract_body(envelope_body)
    assert extracted_body["chainId"] == 1
    assert extracted_body["nonce"] == 3
    
    # Test with "tx" key (normalized format)
    envelope_tx = {"tx": body_content, "sigs": []}
    extracted_tx = _extract_body(envelope_tx)
    assert extracted_tx["chainId"] == 1
    assert extracted_tx["nonce"] == 3
    
    # Both should produce identical results
    assert extracted_body == extracted_tx


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
