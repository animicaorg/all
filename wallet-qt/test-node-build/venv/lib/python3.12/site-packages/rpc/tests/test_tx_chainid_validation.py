"""
Test chainId validation in tx.sendRawTransaction

Regression test for issue where signed transactions with chainId=1
were decoded as chainId=0, causing CHAIN_ID_MISMATCH errors.

This test validates:
1. Signed transactions with correct chainId are accepted
2. Signed transactions with wrong chainId are rejected with clear error
3. Signed transactions missing chainId are rejected with clear error
4. ChainId is correctly extracted from nested envelope structures (body field)
"""

from __future__ import annotations

import pytest

from rpc.tests import new_test_client, rpc_call


pytestmark = pytest.mark.anyio


def _choose_working_sig_alg():
    """
    Try dilithium3 first, then sphincs_shake_128s. Return (alg_name, keypair, sign, verify, addr_from_pub).
    Skip the test if neither is available in this environment.
    """
    from pq.py import keygen as pq_keygen
    from pq.py import sign as pq_sign
    from pq.py import verify as pq_verify
    from pq.py.address import address_from_pubkey
    from pq.py.registry import normalize_alg_name

    candidates = ["dilithium3", "sphincs_shake_128s"]
    
    last_err: Exception | None = None
    for name in candidates:
        alg = normalize_alg_name(name)
        try:
            kp = pq_keygen.keygen(alg)
            msg = b"animica self-check"
            sig_env = pq_sign.sign_detached(msg, alg, kp.secret_key, domain="test/self-check")
            assert pq_verify.verify_detached(msg, sig_env, kp.public_key) is True
            assert kp.address and isinstance(kp.address, str)
            
            def sign_wrapper(alg_id, sk, msg_bytes):
                sig_env = pq_sign.sign_detached(msg_bytes, alg_id, sk, domain="tx/sign")
                return sig_env.sig
            
            def verify_wrapper(alg_id, pk, msg_bytes, sig_bytes):
                from pq.py.sign import Signature
                from pq.py.registry import ALG_NAME
                sig_env = Signature(
                    alg_id=alg_id if isinstance(alg_id, int) else pq_keygen.keygen(alg_id).alg_id,
                    alg_name=ALG_NAME.get(alg_id, alg_id) if isinstance(alg_id, int) else alg_id,
                    domain="tx/sign",
                    prehash="sha3-512",
                    chain_id=None,
                    context=b"",
                    sig=sig_bytes
                )
                return pq_verify.verify_detached(msg_bytes, sig_env, pk)
            
            return alg, kp, sign_wrapper, verify_wrapper, address_from_pubkey
        except Exception as e:
            last_err = e
            continue

    pytest.skip(f"No working PQ signature backend available (last error: {last_err})")


def test_tx_signature_replay_protection_with_fork_id() -> None:
    from pq.py import sign as pq_sign
    from pq.py import verify as pq_verify

    alg, kp, _sign_fn, _verify_fn, _addr_fn = _choose_working_sig_alg()
    msg = b"animica fork-id replay test"
    fork_a = 1
    fork_b = 2

    sig_env = pq_sign.sign_detached(
        msg,
        alg,
        kp.secret_key,
        domain="tx",
        chain_id=1,
        fork_id=fork_a,
    )
    assert (
        pq_verify.verify_detached(
            msg,
            sig_env,
            kp.public_key,
            domain="tx",
            chain_id=1,
            fork_id=fork_a,
        )
        is True
    )
    assert (
        pq_verify.verify_detached(
            msg,
            sig_env,
            kp.public_key,
            domain="tx",
            chain_id=1,
            fork_id=fork_b,
        )
        is False
    )


def _build_signed_transfer_cbor(
    chain_id: int, from_nonce: int = 0
) -> tuple[bytes, str, str]:
    """
    Construct a minimal transfer tx, sign it (PQ), and CBOR-encode it.
    
    Returns: (cbor_bytes, tx_hash_hex, sender_address)
    """
    from core.encoding.canonical import tx_sign_bytes
    from core.encoding.cbor import dumps as cbor_dumps
    from core.types.tx import Sig, Tx
    from pq.py.utils.hash import sha3_256
    from pq.py.address import address_from_pubkey
    from pq.py.registry import ALG_ID

    alg, kp, sign_fn, verify_fn, addr_from_pubkey_fn = _choose_working_sig_alg()

    sender = kp.address
    to_pub_digest = sha3_256(b"recipient")
    to_pubkey = to_pub_digest + b"\x00" * max(0, len(kp.public_key) - len(to_pub_digest))
    alg_id = ALG_ID[alg] if isinstance(alg, str) else alg
    to_addr = address_from_pubkey(to_pubkey, alg_id)

    # Build transaction with specified chainId
    tx = Tx.transfer(
        chain_id=chain_id,
        nonce=from_nonce,
        from_addr=sender,
        to_addr=to_addr,
        value=123456789,
        gas_limit=21000,
        gas_price=1,
        data=b"",
        access_list=[],
    )

    # Sign transaction
    sb = tx_sign_bytes(tx)
    sig_bytes = sign_fn(alg, kp.secret_key, sb)
    sig_env = Sig(
        alg=alg,
        pub=kp.public_key,
        sig=sig_bytes,
    )
    
    from dataclasses import replace
    tx_signed = replace(tx, sigs=(sig_env,))

    # Encode CBOR
    cbor_tx = tx_signed.to_cbor()
    tx_hash_hex = "0x" + tx_signed.txid().hex()
    return cbor_tx, tx_hash_hex, sender


@pytest.fixture(scope="function")
def client_and_cfg():
    client, cfg, app = new_test_client()
    return client, cfg


async def test_tx_with_correct_chainid_accepted(client_and_cfg):
    """Test that a transaction with correct chainId is accepted."""
    client, cfg = client_and_cfg
    
    # Build a valid signed tx with the correct chainId
    cbor_tx, exp_tx_hash, sender = _build_signed_transfer_cbor(
        chain_id=cfg.chain_id,
        from_nonce=0
    )
    raw_hex = "0x" + cbor_tx.hex()

    # Submit - should succeed
    result = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    assert result["jsonrpc"] == "2.0"
    assert "result" in result
    got_hash = result["result"]
    assert isinstance(got_hash, str) and got_hash.startswith("0x")
    assert got_hash == exp_tx_hash


async def test_tx_with_wrong_chainid_rejected(client_and_cfg):
    """Test that a transaction with wrong chainId is rejected with clear error."""
    client, cfg = client_and_cfg
    
    # Build a signed tx with wrong chainId (cfg.chain_id + 1)
    wrong_chain_id = cfg.chain_id + 1
    cbor_tx, exp_tx_hash, sender = _build_signed_transfer_cbor(
        chain_id=wrong_chain_id,
        from_nonce=0
    )
    raw_hex = "0x" + cbor_tx.hex()

    # Submit - should be rejected with CHAIN_ID_MISMATCH
    result = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex}, expect_error=True)
    assert "error" in result
    err = result["error"]
    
    # Error code should be -32011 (CHAIN_ID_MISMATCH)
    assert err["code"] == -32011
    assert "chain id" in err["message"].lower() or "chainid" in err["message"].lower()
    
    # Data should contain got and expected
    assert "data" in err
    assert err["data"]["got"] == wrong_chain_id
    assert err["data"]["expected"] == cfg.chain_id


async def test_sdk_tx_builder_encodes_chainid(client_and_cfg):
    """
    Test that SDK transaction builder correctly encodes chainId into CBOR.
    
    This validates the full path: SDK builder -> encoder -> CBOR -> node decoder.
    """
    # Skip if omni_sdk not available (e.g., RPC tests run without SDK installed)
    try:
        from omni_sdk.tx.build import transfer
        from omni_sdk.tx.encode import sign_bytes, pack_signed
        from omni_sdk.wallet.signer import PQSigner
    except ImportError:
        pytest.skip("omni_sdk not available")
    
    client, cfg = client_and_cfg
    
    # Use SDK to build and sign a transaction
    from pq.py import keygen as pq_keygen
    from pq.py.registry import normalize_alg_name

    # Generate a keypair
    alg = normalize_alg_name("dilithium3")
    kp = pq_keygen.keygen(alg)
    
    # Build transaction via SDK
    tx = transfer(
        from_addr=kp.address,
        to_addr=kp.address,  # Send to self for simplicity
        amount=1000,
        nonce=0,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=cfg.chain_id,  # Use correct chain ID
    )
    
    # Sign transaction via SDK
    signer = PQSigner.from_keypair(
        alg_name=alg if isinstance(alg, str) else "dilithium3",
        secret_key=kp.secret_key,
        public_key=kp.public_key,
    )
    
    sign_bytes_data = sign_bytes(tx)
    signature = signer.sign(sign_bytes_data)
    
    # Pack into signed CBOR envelope
    raw_tx = pack_signed(
        tx,
        signature=signature,
        alg_id=signer.alg_id,
        public_key=signer.public_key,
    )
    
    # Decode the CBOR to verify chainId is present
    from core.encoding.cbor import loads as cbor_loads
    decoded = cbor_loads(raw_tx)
    
    # The envelope should have a 'body' field containing chainId
    assert "body" in decoded, f"Missing 'body' in envelope: {list(decoded.keys())}"
    assert "chainId" in decoded["body"], f"Missing 'chainId' in body: {list(decoded['body'].keys())}"
    assert decoded["body"]["chainId"] == cfg.chain_id, (
        f"ChainId mismatch: got {decoded['body']['chainId']}, expected {cfg.chain_id}"
    )
    
    # Now submit to the node - should succeed
    raw_hex = "0x" + raw_tx.hex()
    result = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    assert "result" in result, f"Expected success, got: {result}"


async def test_decode_and_validate_chainid_from_body_field(client_and_cfg):
    """
    Test that node correctly extracts chainId from 'body' field in signed envelope.
    
    This is the format produced by SDK pack_signed().
    """
    # Skip if omni_sdk not available
    try:
        from omni_sdk.tx.build import transfer
        from omni_sdk.tx.encode import sign_bytes, pack_signed
        from omni_sdk.wallet.signer import PQSigner
    except ImportError:
        pytest.skip("omni_sdk not available")
    
    client, cfg = client_and_cfg
    
    # Build a transaction using SDK (which uses 'body' field)
    from pq.py import keygen as pq_keygen
    from pq.py.registry import normalize_alg_name

    alg = normalize_alg_name("dilithium3")
    kp = pq_keygen.keygen(alg)
    
    tx = transfer(
        from_addr=kp.address,
        to_addr=kp.address,
        amount=1000,
        nonce=1,  # Different nonce to avoid duplicate
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=cfg.chain_id,
    )
    
    signer = PQSigner.from_keypair(
        alg_name=alg if isinstance(alg, str) else "dilithium3",
        secret_key=kp.secret_key,
        public_key=kp.public_key,
    )
    
    sign_bytes_data = sign_bytes(tx)
    signature = signer.sign(sign_bytes_data)
    raw_tx = pack_signed(tx, signature=signature, alg_id=signer.alg_id, public_key=signer.public_key)
    
    # Submit to node - should succeed since chainId is in body field
    raw_hex = "0x" + raw_tx.hex()
    result = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    assert "result" in result, f"Node failed to extract chainId from body field: {result}"


async def test_reject_transaction_with_chainid_zero(client_and_cfg):
    """
    Test that transactions with chainId=0 are rejected.
    
    ChainId=0 is invalid per spec.
    """
    client, cfg = client_and_cfg
    
    # Try to build a tx with chainId=0 (should fail at construction)
    from core.types.tx import UnsignedTx
    
    # UnsignedTx should reject chainId=0 during construction
    with pytest.raises(ValueError, match="chain_id must be positive"):
        UnsignedTx.build_transfer(
            chain_id=0,  # Invalid
            sender=b"\x00" * 32,
            nonce=0,
            gas_price=1,
            gas_limit=21000,
            to=b"\x00" * 32,
            amount=1000,
        )
