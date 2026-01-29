"""
Test that CLI-style dict envelopes with flat body are accepted by mempool.

This regression test verifies that transactions with the envelope format:
  {"body": {...flat fields...}, "sig": {...}}
are correctly admitted to the mempool.
"""
from __future__ import annotations

import pytest

from rpc.tests import new_test_client, rpc_call

pytestmark = pytest.mark.anyio


def _build_cli_style_envelope(chain_id: int, from_nonce: int = 0) -> tuple[bytes, str, bytes]:
    """
    Build a transaction envelope in CLI format:
      {"body": {"from": bytes, "to": bytes, "nonce": int, ...}, "sig": {...}}
    
    Returns (cbor_bytes, expected_tx_hash_hex, sender_bytes)
    """
    import cbor2
    from pq.py import keygen as pq_keygen
    from pq.py import sign as pq_sign
    from pq.py.address import address_from_pubkey
    from pq.py.registry import normalize_alg_name, ALG_ID
    from pq.py.utils.hash import sha3_256
    from pq.py.sign import build_sign_bytes
    
    # Generate keypair using Dilithium3 (same as CLI default)
    alg = normalize_alg_name("dilithium3")
    kp = pq_keygen.keygen(alg)
    alg_id = ALG_ID[alg] if isinstance(alg, str) else alg
    sender_addr = kp.address
    
    # Generate recipient address
    to_pub_digest = sha3_256(b"recipient_cli_test")
    to_pubkey = to_pub_digest + b"\x00" * max(0, len(kp.public_key) - len(to_pub_digest))
    to_addr = address_from_pubkey(to_pubkey, alg_id)
    
    # Convert addresses to 32-byte format (extract digest from bech32)
    from pq.py.address import decode_address
    sender_rec = decode_address(sender_addr)
    to_rec = decode_address(to_addr)
    
    sender_bytes = bytes(sender_rec.digest)[:32].ljust(32, b"\x00")
    to_bytes = bytes(to_rec.digest)[:32].ljust(32, b"\x00")
    
    # Build body dict (CLI format with flat fields)
    body = {
        "to": to_bytes,
        "from": sender_bytes,
        "value": 17_000_000_000,  # 17 ANM
        "nonce": from_nonce,
        "gasLimit": 21000,
        "maxFee": 1,
        "data": b"",
        "chainId": chain_id,
    }
    
    # CBOR-encode body for signing
    body_bytes = cbor2.dumps(body, canonical=True)
    
    # Sign the body
    sign_bytes = build_sign_bytes(
        body_bytes,
        domain="tx",
        chain_id=chain_id,
        fork_id=None,
        alg_id=alg_id,
        prehash="sha3-512",
    )
    
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
    
    # Build signature envelope (CLI format)
    sig_env = {
        "algId": alg_id,
        "pk": kp.public_key,
        "sig": pq_sig.sig,
        "domain": "tx",
        "prehash": "sha3-512",
        "chainId": chain_id,
    }
    
    # Build final envelope (CLI format)
    envelope = {
        "sig": sig_env,
        "body": body,
    }
    
    # CBOR-encode envelope
    cbor_envelope = cbor2.dumps(envelope, canonical=True)
    
    # Compute expected tx hash
    tx_hash_hex = "0x" + sha3_256(cbor_envelope).hex()
    
    return cbor_envelope, tx_hash_hex, sender_bytes


@pytest.fixture(scope="function")
def client_and_cfg():
    client, cfg, app = new_test_client()
    return client, cfg


async def test_cli_dict_envelope_with_flat_body_is_accepted(client_and_cfg):
    """
    Test that CLI-style dict envelope {"body": {...}, "sig": {...}} is accepted.
    
    This is a regression test for the bug where mempool admission failed with
    "missing sender or nonce" because _sender_bytes() and _tx_nonce() only
    checked dataclass attributes, not dict body fields.
    """
    client, cfg = client_and_cfg
    
    cbor_envelope, expected_tx_hash, sender_bytes = _build_cli_style_envelope(
        cfg.chain_id, from_nonce=0
    )
    raw_hex = "0x" + cbor_envelope.hex()
    
    # Submit transaction via tx.sendRawTransaction
    submit_res = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    
    # Should succeed without "missing sender or nonce" error
    assert submit_res["jsonrpc"] == "2.0"
    assert "error" not in submit_res, f"Expected success, got error: {submit_res.get('error')}"
    
    got_hash = submit_res["result"]
    assert isinstance(got_hash, str) and got_hash.startswith("0x")
    
    # Verify transaction appears in mempool
    pending_res = rpc_call(client, "mempool.getPending", params={})
    assert pending_res["jsonrpc"] == "2.0"
    pending_hashes = pending_res["result"]
    assert isinstance(pending_hashes, list)
    assert got_hash in pending_hashes, (
        f"Transaction {got_hash} was submitted successfully but is NOT in mempool. "
        f"Pending hashes: {pending_hashes}"
    )


async def test_cli_dict_envelope_appears_in_block_template(client_and_cfg):
    """
    Test that CLI-style dict envelope appears in miner block template.
    """
    client, cfg = client_and_cfg
    
    cbor_envelope, expected_tx_hash, sender_bytes = _build_cli_style_envelope(
        cfg.chain_id, from_nonce=0
    )
    raw_hex = "0x" + cbor_envelope.hex()
    
    # Submit transaction
    submit_res = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    assert "error" not in submit_res
    got_hash = submit_res["result"]
    
    # Get block template
    template_res = rpc_call(
        client,
        "miner.getBlockTemplate",
        params={"address": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y"}
    )
    assert template_res["jsonrpc"] == "2.0"
    template = template_res["result"]
    assert isinstance(template, dict)
    
    # Verify mempool count > 0
    mempool_total = template.get("mempoolTotal", 0)
    assert mempool_total > 0, (
        f"Transaction {got_hash} submitted but miner.getBlockTemplate shows mempoolTotal=0"
    )
    
    # Verify template includes transactions
    template_txs = template.get("transactions", [])
    assert len(template_txs) > 0, (
        f"mempoolTotal={mempool_total} but template has 0 transactions"
    )
