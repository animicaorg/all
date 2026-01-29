"""
Integration test: pending mempool tx is mined and balances update.
"""

from __future__ import annotations

import pytest

from rpc.tests import new_test_client, rpc_call


def _parse_int(result: dict) -> int:
    value = result.get("result", 0)
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return int(value)


def _build_signed_transfer(cfg, sender_kp, recipient_hex: str, nonce: int, value: int):
    from core.encoding.canonical import tx_sign_bytes
    from core.types.tx import PqSignature, Tx, TxKind, TxTransfer, UnsignedTx
    from core.genesis.loader import compute_chain_identity
    from pq.py import sign
    from pq.py.address import decode_address
    from pq.py.registry import ALG_ID

    sender_record = decode_address(sender_kp.address)
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    recipient_bytes = bytes.fromhex(recipient_hex[2:] if recipient_hex.startswith("0x") else recipient_hex)

    unsigned = UnsignedTx(
        chain_id=cfg.chain_id,
        nonce=nonce,
        gas_price=1,
        gas_limit=21000,
        sender=sender_bytes,
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=recipient_bytes, amount=value, data=b""),
        access_list=(),
    )
    sign_bytes = tx_sign_bytes(unsigned.to_obj())
    fork_id = compute_chain_identity(None, chain_id=cfg.chain_id).fork_id
    sig_env = sign.sign_detached(
        sign_bytes,
        "dilithium3",
        sender_kp.secret_key,
        domain="tx",
        chain_id=cfg.chain_id,
        fork_id=fork_id,
    )
    sig = PqSignature(
        alg_id=ALG_ID["dilithium3"],
        pubkey=sender_kp.public_key,
        sig=sig_env.sig,
    )
    tx = Tx(unsigned=unsigned, sigs=(sig,))
    return "0x" + tx.to_cbor().hex(), "0x" + tx.txid().hex()


def test_mempool_tx_is_mined_and_balances_update() -> None:
    client, cfg, _ = new_test_client()

    try:
        from pq.py.keygen import keygen_sig
    except Exception:
        pytest.skip("PQ keygen not available")
        return

    sender_kp = keygen_sig("dilithium3")
    receiver_kp = keygen_sig("dilithium3")

    from pq.py.address import decode_address

    sender_record = decode_address(sender_kp.address)
    sender_hex = "0x" + bytes(sender_record.digest)[:32].ljust(32, b"\x00").hex()

    receiver_record = decode_address(receiver_kp.address)
    receiver_hex = "0x" + bytes(receiver_record.digest)[:32].ljust(32, b"\x00").hex()

    mine_fund = rpc_call(client, "miner.mine", {"count": 1, "address": sender_kp.address})["result"]
    assert mine_fund["mined"] == 1

    raw_hex, tx_hash = _build_signed_transfer(cfg, sender_kp, receiver_hex, nonce=0, value=1_000_000_000)

    send_resp = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    assert send_resp.get("result") == tx_hash

    pending = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash in pending

    mine_resp = rpc_call(client, "miner.mine", {"count": 1, "address": sender_kp.address})["result"]
    assert mine_resp["mined"] == 1
    height = mine_resp["height"]

    block = rpc_call(client, "chain.getBlockByNumber", [height, True])["result"]
    assert block is not None
    txs = block.get("transactions", [])
    tx_hashes = [tx.get("hash") if isinstance(tx, dict) else tx for tx in txs]
    assert tx_hash in tx_hashes

    pending_after = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash not in pending_after

    receiver_balance = _parse_int(rpc_call(client, "state.getBalance", [receiver_hex]))
    assert receiver_balance == 1_000_000_000
