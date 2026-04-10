import pytest

from rpc.methods import tx as tx_methods


def _mk_body():
    return {
        "chainId": 1,
        "from": b"\x01" * 32,
        "to": b"\x02" * 32,
        "value": 10,
        "gasLimit": 21000,
        "maxFee": 100,
        "data": b"",
        "validAfter": 1,
        "validUntil": 100,
        "salt": b"\x03" * 32,
    }


def test_tx_debug_sign_hash_guard(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tx_methods, "_DEBUG_RPC", False)
    with pytest.raises(Exception):
        tx_methods.tx_debug_sign_hash(_mk_body())


def test_tx_debug_sign_hash(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tx_methods, "_DEBUG_RPC", True)

    class MockDeps:
        def get_chain_identity(self):
            return {"genesisHash": "0x" + "11" * 32, "network": "mainnet"}

    monkeypatch.setattr(tx_methods, "deps", MockDeps())
    out = tx_methods.tx_debug_sign_hash(_mk_body())
    assert out["chain_id"] == 1
    assert out["domain"] == "tx"
    assert out["prehash"] == "sha3-512"
    assert out["sign_hash_hex"].startswith("0x")
    assert len(out["sign_hash_hex"]) == 130
