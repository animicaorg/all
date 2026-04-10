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


def test_extract_body_prioritizes_normalized_tx_over_body() -> None:
    """
    Test that _extract_body prioritizes "tx" over "body" when both are present.
    
    This is the critical fix: when an envelope has both "tx" (normalized) and
    "body" (original/unnormalized), we must use "tx" for verification to match
    what was signed.
    """
    from animica.tx.signing import _extract_body
    
    normalized_body = {
        "chainId": 1,
        "from": "anim1test",
        "to": "anim1dest",
        "nonce": 4,
        "value": 4000,
        "gasLimit": 21000,
        "maxFee": 1000000000,
        "data": b"",
    }
    
    # Simulate an envelope that has BOTH keys (can happen during decoding/normalization)
    # The "body" might have slightly different formatting from "tx"
    original_body = {
        "chainId": 1,
        "from": "anim1test",
        "to": "anim1dest",
        "nonce": 4,
        "value": 4000,
        "gasLimit": 21000,
        "maxFee": 1000000000,
        "data": "",  # Different representation: empty string vs empty bytes
    }
    
    envelope_with_both = {
        "tx": normalized_body,  # Normalized (canonical)
        "body": original_body,  # Original (non-canonical)
        "sigs": []
    }
    
    # Should extract "tx" (normalized), not "body" (original)
    extracted = _extract_body(envelope_with_both)
    assert extracted["chainId"] == 1
    assert extracted["nonce"] == 4
    # Verify it used the normalized body (which has bytes for data)
    assert extracted["data"] == b"", f"Expected bytes b'', got {extracted['data']!r}"
    # The normalized body should have been extracted
    assert extracted == normalized_body


def test_peer_tx_verification_after_normalization() -> None:
    """
    Integration test simulating peer transaction import flow.
    
    Tests the full flow:
    1. Sign a transaction (CLI creates {"body": ..., "sig": ...})
    2. Encode to CBOR
    3. Decode from CBOR (as peer would receive it)
    4. Normalize envelope (creates {"tx": ..., "sigs": ...})
    5. Verify signature (should work with normalized envelope)
    
    This test verifies that the fix correctly handles the peer import scenario.
    """
    from pq.py.keygen import keygen_sig
    from animica.tx.signing import ChainContext, pq_sign_tx, pq_verify_tx, _extract_body
    import cbor2
    
    kp = keygen_sig("sphincs_shake_128s")
    
    # Step 1: Create transaction body (as CLI would)
    body = {
        "chainId": 1,
        "from": "anim1test",
        "to": "anim1dest",
        "nonce": 5,
        "value": 5000,
        "gasLimit": 21000,
        "maxFee": 1000000000,
        "data": b"",
    }
    
    ctx = ChainContext(
        chain_id=1,
        genesis_hash=b"\x99" * 32,
        network="devnet",
        fork_id=None,
        domain="tx",
        prehash="sha3-512",
    )
    
    # Step 2: Sign the transaction
    sig = pq_sign_tx(body, kp.secret_key, kp.public_key, kp.alg_id, ctx)
    
    # Step 3: Create envelope (as CLI would before sending)
    cli_envelope = {
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
    
    # Step 4: Encode to CBOR (as would be transmitted over network)
    cbor_bytes = cbor2.dumps(cli_envelope, canonical=True)
    
    # Step 5: Decode from CBOR (as peer receives it)
    decoded = cbor2.loads(cbor_bytes)
    
    # Step 6: Simulate normalization (what _decode_tx would do)
    # Normalize creates {"tx": <normalized_body>, "sigs": [...]}
    from core.utils.tx import normalize_tx_body
    normalized_body = normalize_tx_body(decoded.get("body"))
    
    # Create normalized envelope (as _normalize_tx_envelope would)
    normalized_envelope = {
        "tx": normalized_body,
        "sigs": [decoded.get("sig")],
    }
    
    # Step 7: Verify with normalized envelope (the critical test)
    # Before fix: would fail because _extract_body would use "body" if present
    # After fix: should work because _extract_body prioritizes "tx"
    verify_result = pq_verify_tx(normalized_envelope, sig, kp.public_key, ctx)
    assert verify_result.ok is True, f"Verification failed with normalized envelope: {verify_result.reason}"
    
    # Step 8: Verify that _extract_body uses the normalized body
    extracted = _extract_body(normalized_envelope)
    assert extracted == normalized_body, "Should extract normalized tx body"
    
    # Step 9: Also test the case where BOTH keys are present (the problematic case before fix)
    envelope_with_both = {
        "tx": normalized_body,      # Normalized (canonical) - should be used
        "body": decoded.get("body"), # Original (as decoded) - should be ignored
        "sigs": [decoded.get("sig")],
    }
    
    verify_result_both = pq_verify_tx(envelope_with_both, sig, kp.public_key, ctx)
    assert verify_result_both.ok is True, f"Verification failed with both keys present: {verify_result_both.reason}"
    
    # Verify consistency: both verification paths should produce same preimage
    assert verify_result.preimage_hex == verify_result_both.preimage_hex
    assert verify_result.sign_hash_hex == verify_result_both.sign_hash_hex


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
