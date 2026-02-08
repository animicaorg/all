"""
Test for PQ signature verification with transaction body normalization.

This test validates that signature verification works correctly when the
transaction body is normalized from CLI format to canonical format.

Regression test for issue: "Invalid post-quantum signature: verification failed"
where signatures created over original CLI body format failed verification
after the body was normalized by the RPC.
"""

from __future__ import annotations

import cbor2

from pq.py.keygen import keygen_sig
from animica.tx.signing import ChainContext, pq_sign_tx, pq_verify_tx, _extract_body


def test_pq_signature_survives_body_normalization():
    """
    Test that PQ signatures verify correctly after body normalization.
    
    Flow:
    1. CLI creates transaction with original body format (string addresses)
    2. CLI signs transaction
    3. Transaction is CBOR-encoded
    4. RPC decodes and normalizes body (converts to bytes, restructures)
    5. RPC verifies signature (should use original body, not normalized)
    
    Before fix: Step 5 would fail because verification used normalized body
    After fix: Step 5 succeeds because original body is preserved
    """
    # Generate keypair (SPHINCS+ for this test, but works for any algorithm)
    kp = keygen_sig("sphincs_shake_128s")
    
    # Create transaction in CLI format (string addresses, flat structure)
    cli_body = {
        "chainId": 1,
        "from": "anim1zqtest",  # String address
        "to": "anim1zqdest",    # String address
        "nonce": 0,
        "value": 10 * 1_000_000_000,
        "gasLimit": 21000,
        "maxFee": 1000000,
        "data": b"",
    }
    
    # Sign the transaction
    ctx = ChainContext(
        chain_id=1,
        genesis_hash=b"\x00" * 32,
        network="testnet",
        domain="tx",
        prehash="sha3-512",
    )
    sig = pq_sign_tx(cli_body, kp.secret_key, kp.public_key, kp.alg_id, ctx)
    
    # Verify locally (should pass)
    vr = pq_verify_tx(cli_body, sig, kp.public_key, ctx)
    assert vr.ok is True, f"Local verification failed: {vr.reason}"
    
    # Create transaction envelope and encode to CBOR
    envelope = {
        "body": cli_body,
        "sig": {
            "algId": kp.alg_id,
            "pk": kp.public_key,
            "sig": sig.sig,
            "domain": sig.domain,
            "prehash": sig.prehash,
            "chainId": 1,
        }
    }
    raw_cbor = cbor2.dumps(envelope, canonical=True)
    
    # Decode (simulating RPC receive)
    decoded = cbor2.loads(raw_cbor)
    
    # Normalize the body (simulating what RPC does)
    from core.utils.tx import normalize_tx_body
    normalized_tx = normalize_tx_body(decoded["body"])
    
    # Simulate RPC behavior: both "body" (original) and "tx" (normalized) present
    rpc_envelope = dict(decoded)
    rpc_envelope["tx"] = normalized_tx
    
    # Verify that _extract_body prefers original "body" over normalized "tx"
    extracted = _extract_body(rpc_envelope)
    assert "from" in extracted
    assert isinstance(extracted["from"], str), (
        "Expected original body with string addresses, got normalized body"
    )
    assert extracted["from"] == "anim1zqtest"
    
    # Verify signature using RPC-style envelope (original + normalized)
    vr2 = pq_verify_tx(rpc_envelope, sig, kp.public_key, ctx)
    assert vr2.ok is True, f"RPC-style verification failed: {vr2.reason}"


def test_extract_body_prefers_original_over_normalized():
    """
    Test that _extract_body extracts the original body when both
    "body" (original) and "tx" (normalized) keys are present.
    """
    # Envelope with both original and normalized bodies
    envelope = {
        "body": {
            "chainId": 1,
            "from": "anim1original",  # String
            "to": "anim1dest",
            "nonce": 0,
            "value": 100,
            "gasLimit": 21000,
            "maxFee": 1000000,
            "data": b"",
        },
        "tx": {
            "v": 1,
            "chainId": 1,
            "from": b"\x11" * 32,  # Bytes (normalized)
            "gas": {"price": 1000000, "limit": 21000},
            "payload": {
                "t": 0,
                "v": {
                    "to": b"\x22" * 32,
                    "amount": 100,
                    "data": b"",
                },
            },
            "accessList": [],
            "nonce": 0,
        },
        "sigs": [],
    }
    
    # Extract body should prefer original "body" over normalized "tx"
    extracted = _extract_body(envelope)
    
    assert "from" in extracted
    assert isinstance(extracted["from"], str), (
        "Expected original body, got normalized"
    )
    assert extracted["from"] == "anim1original"


def test_extract_body_falls_back_to_normalized_when_no_original():
    """
    Test that _extract_body uses normalized "tx" when original "body" is absent.
    
    This handles the case where a transaction only has the normalized format,
    which can happen with internally-generated transactions.
    """
    # Envelope with only normalized body (no original "body")
    envelope = {
        "tx": {
            "v": 1,
            "chainId": 1,
            "from": b"\x11" * 32,  # Bytes (normalized)
            "gas": {"price": 1000000, "limit": 21000},
            "payload": {
                "t": 0,
                "v": {
                    "to": b"\x22" * 32,
                    "amount": 100,
                    "data": b"",
                },
            },
            "accessList": [],
            "nonce": 0,
        },
        "sigs": [],
    }
    
    # Extract body should fall back to normalized "tx"
    extracted = _extract_body(envelope)
    
    # Should get normalized format
    assert "v" in extracted or "gas" in extracted or "payload" in extracted
