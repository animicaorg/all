import os
import typing as t

import pytest

from rpc.tests import new_test_client, rpc_call

pytestmark = pytest.mark.anyio


def _choose_working_sig_alg():
    from pq.py import keygen as pq_keygen
    from pq.py import sign as pq_sign
    from pq.py import verify as pq_verify
    from pq.py.address import address_from_pubkey
    from pq.py.registry import normalize_alg_name

    candidates = ["dilithium3", "sphincs_shake_128s"]
    forced = os.getenv("ANIMICA_TEST_SIG_ALG")
    if forced:
        candidates = [forced]

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


def _build_signed_transfer_cbor(
    chain_id: int, from_nonce: int = 0
) -> tuple[bytes, str, str]:
    from core.encoding.canonical import tx_sign_bytes
    from core.encoding.cbor import dumps as cbor_dumps
    from core.types.tx import Sig, Tx
    from pq.py.utils.hash import sha3_256

    alg, kp, sign_fn, verify_fn, addr_from_pubkey_fn = _choose_working_sig_alg()

    sender = kp.address
    to_pub_digest = sha3_256(b"recipient")
    to_pubkey = to_pub_digest + b"\x00" * max(0, len(kp.public_key) - len(to_pub_digest))
    from pq.py.address import address_from_pubkey
    from pq.py.registry import ALG_ID
    alg_id = ALG_ID[alg] if isinstance(alg, str) else alg
    to_addr = address_from_pubkey(to_pubkey, alg_id)

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

    sb = tx_sign_bytes(tx)
    sig_bytes = sign_fn(alg, kp.secret_key, sb)
    sig_env = Sig(
        alg=alg,
        pub=kp.public_key,
        sig=sig_bytes,
    )
    from dataclasses import replace
    tx_signed = replace(tx, sigs=(sig_env,))

    cbor_tx = tx_signed.to_cbor()
    tx_hash_hex = "0x" + tx_signed.txid().hex()
    return cbor_tx, tx_hash_hex, sender


def _build_signed_transfer_for(
    *,
    chain_id: int,
    kp: t.Any,
    to_addr: str,
    nonce: int,
    alg: str,
    sign_fn: t.Callable[[t.Any, bytes, bytes], bytes],
    value: int = 123456789,
) -> tuple[bytes, str]:
    from core.encoding.canonical import tx_sign_bytes
    from core.types.tx import Sig, Tx
    from pq.py.registry import ALG_ID

    tx = Tx.transfer(
        chain_id=chain_id,
        nonce=nonce,
        from_addr=kp.address,
        to_addr=to_addr,
        value=value,
        gas_limit=21000,
        gas_price=1,
        data=b"",
        access_list=[],
    )

    sb = tx_sign_bytes(tx)
    sig_bytes = sign_fn(alg, kp.secret_key, sb)
    sig_env = Sig(
        alg=ALG_ID[alg] if isinstance(alg, str) else alg,
        pub=kp.public_key,
        sig=sig_bytes,
    )
    from dataclasses import replace
    tx_signed = replace(tx, sigs=(sig_env,))
    cbor_tx = tx_signed.to_cbor()
    tx_hash_hex = "0x" + tx_signed.txid().hex()
    return cbor_tx, tx_hash_hex


@pytest.fixture(scope="function")
def client_and_cfg():
    client, cfg, app = new_test_client()
    return client, cfg


async def test_tx_send_appears_in_mempool_immediately(client_and_cfg):
    client, cfg = client_and_cfg
    cbor_tx, exp_tx_hash, sender = _build_signed_transfer_cbor(
        cfg.chain_id, from_nonce=0
    )
    raw_hex = "0x" + cbor_tx.hex()

    submit_res = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    assert submit_res["jsonrpc"] == "2.0"
    got_hash = submit_res["result"]
    assert isinstance(got_hash, str) and got_hash.startswith("0x")
    assert got_hash == exp_tx_hash

    pending_res = rpc_call(client, "mempool.getPending", params={})
    assert pending_res["jsonrpc"] == "2.0"
    pending_hashes = pending_res["result"]
    assert isinstance(pending_hashes, list), f"Expected list, got {type(pending_hashes)}"
    assert got_hash in pending_hashes, (
        f"CRITICAL BUG: tx {got_hash} was submitted successfully but NOT in mempool.getPending. "
        f"Pending hashes: {pending_hashes}"
    )


async def test_tx_send_appears_in_block_template(client_and_cfg):
    client, cfg = client_and_cfg
    cbor_tx, exp_tx_hash, sender = _build_signed_transfer_cbor(
        cfg.chain_id, from_nonce=0
    )
    raw_hex = "0x" + cbor_tx.hex()

    submit_res = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    got_hash = submit_res["result"]
    assert got_hash == exp_tx_hash

    template_res = rpc_call(
        client,
        "miner.getBlockTemplate",
        params={"address": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y"}
    )
    assert template_res["jsonrpc"] == "2.0"
    template = template_res["result"]
    assert isinstance(template, dict), f"Expected dict, got {type(template)}"
    
    mempool_total = template.get("mempoolTotal", 0)
    assert mempool_total > 0, (
        f"CRITICAL BUG: tx {got_hash} submitted but miner.getBlockTemplate shows mempoolTotal=0. "
        f"Template: {template}"
    )
    
    template_txs = template.get("transactions", [])
    assert len(template_txs) > 0, (
        f"CRITICAL BUG: mempoolTotal={mempool_total} but template has 0 transactions. "
        f"Template: {template}"
    )


async def test_tx_invalid_signature_returns_error_not_success(client_and_cfg):
    client, cfg = client_and_cfg
    from core.types.tx import Tx
    from pq.py.registry import normalize_alg_name

    bad_alg = normalize_alg_name("dilithium3")
    tx_unsigned = Tx.transfer(
        chain_id=cfg.chain_id,
        nonce=0,
        from_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        to_addr="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        value=1,
        gas_limit=21000,
        gas_price=1,
        data=b"",
        access_list=[],
    )
    from dataclasses import replace
    tx = replace(tx_unsigned, sigs=(Tx.Sig(alg=bad_alg, pub=b"\x01\x02", sig=b"\x03\x04"),))
    raw_hex = "0x" + tx.to_cbor().hex()

    res = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex}, expect_error=True)
    err = res["error"]
    assert isinstance(err.get("code"), int), (
        "Invalid tx MUST return JSON-RPC error, not success. "
        f"Got: {res}"
    )
    assert any(
        s in (err.get("message") or "").lower() for s in ("sig", "verify", "invalid")
    ), f"Error message should mention signature issue: {err}"

    pending_res = rpc_call(client, "mempool.getPending", params={})
    pending_hashes = pending_res["result"]
    assert len(pending_hashes) == 0, (
        "Invalid tx should NOT be in mempool after rejection. "
        f"Pending: {pending_hashes}"
    )


async def test_pending_nonce_advances_for_back_to_back_sends(client_and_cfg):
    client, cfg = client_and_cfg
    from pq.py import keygen as pq_keygen
    from pq.py.registry import normalize_alg_name

    alg, _kp_unused, sign_fn, _verify_fn, _address_from_pubkey = _choose_working_sig_alg()
    alg = normalize_alg_name(alg)
    sender_kp = pq_keygen.keygen(alg)
    receiver_kp = pq_keygen.keygen(alg)

    pending_nonce_0 = rpc_call(client, "state.getPendingNonce", params=[sender_kp.address])["result"]

    cbor_tx1, tx_hash1 = _build_signed_transfer_for(
        chain_id=cfg.chain_id,
        kp=sender_kp,
        to_addr=receiver_kp.address,
        nonce=int(pending_nonce_0),
        alg=alg,
        sign_fn=sign_fn,
        value=1,
    )
    raw_hex1 = "0x" + cbor_tx1.hex()
    submit1 = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex1})
    assert submit1["result"] == tx_hash1

    pending_nonce_1 = rpc_call(client, "state.getPendingNonce", params=[sender_kp.address])["result"]
    assert int(pending_nonce_1) == int(pending_nonce_0) + 1

    cbor_tx2, tx_hash2 = _build_signed_transfer_for(
        chain_id=cfg.chain_id,
        kp=sender_kp,
        to_addr=receiver_kp.address,
        nonce=int(pending_nonce_1),
        alg=alg,
        sign_fn=sign_fn,
        value=1,
    )
    raw_hex2 = "0x" + cbor_tx2.hex()
    submit2 = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex2})
    assert submit2["result"] == tx_hash2
    assert tx_hash2 != tx_hash1
