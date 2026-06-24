"""Deterministic tests for the Bitcoin-Core-compat facade (mock native registry).

No live DB needed: we inject canned native responses and assert the Bitcoin
JSON shapes. Run: PYTHONPATH=. pytest rpc/methods/bitcoin_compat/tests -q
"""

from __future__ import annotations

import types

import pytest

from rpc.methods.bitcoin_compat import formatters as F


# ---- formatter units ----------------------------------------------------------
def test_hx_px_roundtrip():
    assert F.hx("0xabc") == "abc"
    assert F.hx("abc") == "abc"
    assert F.px("abc") == "0xabc"
    assert F.px("0xabc") == "0xabc"


def test_unit_conversion():
    assert F.to_coin(1_000_000_000) == 1.0
    assert F.to_coin("0x3b9aca00") == 1.0  # 1e9
    assert F.to_nanos(1.0) == 1_000_000_000
    assert F.to_coin(None) == 0.0


def test_theta_difficulty_monotonic_and_target():
    assert F.theta_to_difficulty(0) == 0.0
    d1 = F.theta_to_difficulty(900_000)
    d2 = F.theta_to_difficulty(18_647_668)
    assert 0 < d1 < d2  # higher theta -> higher difficulty
    tgt = F.difficulty_to_target_hex(d2)
    assert len(tgt) == 64 and all(c in "0123456789abcdef" for c in tgt)


def test_chain_name():
    assert F.chain_name_from_id(1) == "main"
    assert F.chain_name_from_id(1337) == "regtest"
    assert F.chain_name_from_id(99) == "animica-99"


# ---- handler behavior with a mock native registry ----------------------------
@pytest.fixture
def mock_registry(monkeypatch):
    HEAD = {"height": 27255, "number": 27255,
            "hash": "0x0000000747e69bf154a9c9cfff4e0e8e0974a01939225ca43e975876e301a15d",
            "chainId": 1, "thetaMicro": 18647668, "timestamp": 1782317000,
            "roots": {"txsRoot": "0x" + "00" * 32}}
    BLOCK1 = {"number": 1, "height": 1,
              "hash": "0x19af39190f7edaa812cfa818ffe16b686cbe1a7a9187bf5403a1a49d37ba22c7",
              "parentHash": "0xa089" + "00" * 30, "timestamp": 1776433971,
              "chainId": 1, "thetaMicro": 900000, "roots": {"txsRoot": "0x" + "00" * 32}}
    data = {
        "chain.getHead": lambda *a, **k: HEAD,
        "chain.getBlockByHeight": lambda h, *a, **k: (BLOCK1 if int(h) == 1 else None),
        "chain.getBlockByHash": lambda hsh, *a, **k: BLOCK1,
        "chain.getChainIdentity": lambda *a, **k: {"genesisHash": "0xa089" + "00" * 30},
        "mempool.getStats": lambda *a, **k: {"count": 0, "totalBytes": 0, "oldestAgeSec": None},
        "node.health": lambda *a, **k: {"version": "0.1.0-dev", "peers_connected": 3,
                                        "uptime_seconds": 143246, "chain_id": 1,
                                        "network_name": "mainnet"},
        "tx.getTransactionByHash": lambda txid, *a, **k: None,
        "mempool.getRawTx": lambda txid, *a, **k: {},
    }

    class Spec:
        def __init__(self, fn):
            self.func = fn

    monkeypatch.setattr(F, "_REG", {k: Spec(v) for k, v in data.items()})
    return data


def test_getblockcount_and_bestblockhash(mock_registry):
    from rpc.methods.bitcoin_compat import chain_methods as C
    assert C.getblockcount() == 27255
    assert C.getbestblockhash() == "0000000747e69bf154a9c9cfff4e0e8e0974a01939225ca43e975876e301a15d"
    assert "0x" not in C.getbestblockhash()  # bare hex, Bitcoin convention


def test_getblockhash_and_out_of_range(mock_registry):
    from rpc.methods.bitcoin_compat import chain_methods as C
    from rpc.methods.bitcoin_compat.errors_btc import RPC_INVALID_PARAMETER
    assert C.getblockhash(1) == "19af39190f7edaa812cfa818ffe16b686cbe1a7a9187bf5403a1a49d37ba22c7"
    with pytest.raises(Exception) as ei:
        C.getblockhash(999999)
    assert getattr(ei.value, "code", None) == RPC_INVALID_PARAMETER


def test_getblockchaininfo_shape(mock_registry):
    from rpc.methods.bitcoin_compat import chain_methods as C
    info = C.getblockchaininfo()
    assert info["chain"] == "main"
    assert info["blocks"] == 27255
    assert info["bestblockhash"].startswith("0000000747")
    assert info["difficulty"] > 0
    assert info["pruned"] is False
    assert info["chainwork"] == "0" * 64
    assert info["consensus"] == "poies"  # Animica extension


def test_getblockheader_fields(mock_registry):
    from rpc.methods.bitcoin_compat import chain_methods as C
    h = C.getblockheader("19af3919" + "00" * 28, True)
    assert h["height"] == 1
    assert h["confirmations"] == 27255 - 1 + 1
    assert h["versionHex"] == "20000000"
    assert "previousblockhash" in h
    assert "0x" not in h["hash"]


def test_validateaddress():
    from rpc.methods.bitcoin_compat import chain_methods as C
    good = C.validateaddress("anim1zqpf6a5hvup7kggxdutt3tgswz4aa9rwlpsz8ywf73m4l5cmzhfk7pcnh6kgt")
    assert good["isvalid"] is True
    assert good["animica:type"] == "pq-bech32m"
    assert C.validateaddress("notanaddress")["isvalid"] is False


def test_getmininginfo_shape(mock_registry):
    from rpc.methods.bitcoin_compat import mining_methods as M
    info = M.getmininginfo()
    assert info["blocks"] == 27255
    assert info["chain"] == "main"
    assert info["animica:consensus"] == "poies"


def test_help_lists_methods():
    from rpc.methods.bitcoin_compat import control_methods as CTL
    txt = CTL.help()
    assert "getblockcount" in txt and "sendrawtransaction" in txt
    assert CTL.stop() == "Animica server stopping"
