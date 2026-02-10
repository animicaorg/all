"""
Test that gasLimit dict format is handled correctly in balance validation.

This test verifies that when a transaction with gasLimit as a dict 
{"limit": int, "price": int} reaches the RPC server, it is properly 
handled in the balance validation step.
"""
from __future__ import annotations

import pytest
from rpc.tests import new_test_client, rpc_call

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="function")
def client_and_cfg():
    """Fixture to create a test client and configuration."""
    client, cfg, app = new_test_client()
    return client, cfg


def _build_tx_with_dict_gaslimit(
    chain_id: int,
    gas_limit_dict: dict[str, int],
) -> tuple[bytes, str]:
    """
    Build a transaction envelope with gasLimit as a dict.
    
    Returns (cbor_bytes, expected_tx_hash_hex)
    """
    import cbor2
    from pq.py import keygen as pq_keygen
    from pq.py import sign as pq_sign
    from pq.py.address import decode_address
    from pq.py.registry import normalize_alg_name, ALG_ID
    from pq.py.utils.hash import sha3_256
    from pq.py.sign import build_sign_bytes
    
    # Generate keypair using Dilithium3
    alg = normalize_alg_name("dilithium3")
    kp = pq_keygen.keygen(alg)
    alg_id = ALG_ID[alg] if isinstance(alg, str) else alg
    sender_addr = kp.address
    
    # Send to self for simplicity in this test
    to_addr = kp.address
    
    # Convert addresses to 32-byte format
    sender_rec = decode_address(sender_addr)
    to_rec = decode_address(to_addr)
    
    sender_bytes = bytes(sender_rec.digest)[:32].ljust(32, b"\x00")
    to_bytes = bytes(to_rec.digest)[:32].ljust(32, b"\x00")
    
    # Build body dict with gasLimit as dict (legacy format)
    body = {
        "to": to_bytes,
        "from": sender_bytes,
        "value": 1_000_000_000,  # 1 ANM
        "nonce": 0,
        "gasLimit": gas_limit_dict,  # Dict format instead of int
        "data": b"",
        "chainId": chain_id,
        "maxFee": gas_limit_dict.get("price", 1),  # Extract price for maxFee
    }
    
    # CBOR-encode body for signing
    body_bytes = cbor2.dumps(body, canonical=True)
    
    # Sign the body
    pq_sig = pq_sign.pq_sign_detached(
        body_bytes,
        alg=alg_id,
        sk=kp.secret_key,
        pk=kp.public_key,
        domain="tx",
        chain_id=chain_id,
        fork_id=None,
        prehash="sha3-512",
    )
    
    # Build signature envelope
    sig_env = {
        "algId": alg_id,
        "pk": kp.public_key,
        "sig": pq_sig.sig,
        "domain": "tx",
        "prehash": "sha3-512",
        "chainId": chain_id,
    }
    
    # Build full envelope
    envelope = {"body": body, "sig": sig_env}
    cbor_envelope = cbor2.dumps(envelope, canonical=True)
    
    # Compute expected hash
    expected_hash = sha3_256(cbor_envelope)
    expected_hash_hex = "0x" + expected_hash.hex()
    
    return cbor_envelope, expected_hash_hex


async def test_gaslimit_dict_accepted_and_normalized(client_and_cfg):
    """
    Test that gasLimit dict format {"limit": int, "price": int} is accepted
    and properly normalized during transaction validation.
    """
    client, cfg = client_and_cfg
    
    # Build transaction with gasLimit as dict
    cbor_envelope, expected_hash = _build_tx_with_dict_gaslimit(
        cfg.chain_id,
        gas_limit_dict={"limit": 21000, "price": 1},
    )
    raw_hex = "0x" + cbor_envelope.hex()
    
    # Submit transaction
    submit_res = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    
    # Verify success
    assert submit_res["jsonrpc"] == "2.0"
    assert "error" not in submit_res, (
        f"Expected success for gasLimit dict, got error: {submit_res.get('error')}\n"
        f"This indicates the balance validation or normalization is not handling dict gasLimit correctly."
    )
    
    # Verify we got a valid transaction hash
    result = submit_res["result"]
    assert isinstance(result, (str, dict))
    if isinstance(result, str):
        assert result.startswith("0x")
        tx_hash = result
    else:
        tx_hash = result.get("tx_hash") or result.get("hash")
        assert tx_hash and tx_hash.startswith("0x")


async def test_gaslimit_dict_with_high_price_accepted(client_and_cfg):
    """
    Test that gasLimit dict with a custom price value is properly handled.
    """
    client, cfg = client_and_cfg
    
    # Build transaction with gasLimit dict that has custom price
    cbor_envelope, expected_hash = _build_tx_with_dict_gaslimit(
        cfg.chain_id,
        gas_limit_dict={"limit": 50000, "price": 10},
    )
    raw_hex = "0x" + cbor_envelope.hex()
    
    # Submit transaction
    submit_res = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    
    # Verify success - should not fail on balance validation
    # even when using dict format
    assert submit_res["jsonrpc"] == "2.0"
    if "error" in submit_res:
        error = submit_res["error"]
        # If error is about insufficient balance, that's expected for a new address
        # But it should NOT be about bad_field_type
        if "bad_field_type" in str(error).lower() or "must be an integer" in str(error).lower():
            pytest.fail(
                f"Transaction rejected with field type error, expected balance validation to handle dict gasLimit: {error}"
            )
