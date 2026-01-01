import hashlib

from animica.cli import tx as tx_cli


def test_nonce_increment_produces_unique_txid(monkeypatch) -> None:
    tx_cli._NONCE_CACHE.clear()

    def fake_rpc(_url: str, method: str, _params):
        if method == "mempool.getPending":
            return []
        return 7

    monkeypatch.setattr(tx_cli, "_rpc", fake_rpc)

    nonce1 = tx_cli._next_nonce("http://node", "0x" + "11" * 32)
    nonce2 = tx_cli._next_nonce("http://node", "0x" + "11" * 32)

    assert nonce1 == 7
    assert nonce2 == 8

    body1 = tx_cli._build_tx_body(
        chain_id=1337,
        from_addr="0x" + "11" * 32,
        to_addr="0x" + "22" * 32,
        nonce=nonce1,
        value_base_units=1,
        gas_limit=21000,
        max_fee=1,
        data=b"",
    )
    body2 = tx_cli._build_tx_body(
        chain_id=1337,
        from_addr="0x" + "11" * 32,
        to_addr="0x" + "22" * 32,
        nonce=nonce2,
        value_base_units=1,
        gas_limit=21000,
        max_fee=1,
        data=b"",
    )
    raw1 = tx_cli._build_raw_tx(
        body=body1,
        alg_id=1,
        pk=b"\x11" * 32,
        sig=b"\x22" * 64,
        domain="tx",
        prehash="sha3-512",
        chain_id=1337,
    )
    raw2 = tx_cli._build_raw_tx(
        body=body2,
        alg_id=1,
        pk=b"\x11" * 32,
        sig=b"\x22" * 64,
        domain="tx",
        prehash="sha3-512",
        chain_id=1337,
    )

    assert hashlib.sha3_256(raw1).digest() != hashlib.sha3_256(raw2).digest()
