"""Deterministic tests for the EVM-compat facade (mock native registry)."""

from __future__ import annotations

import pytest

from rpc.methods.evm_compat import formatters as F


def test_quantity_encoding():
    assert F.q(0) == "0x0"
    assert F.q(255) == "0xff"
    assert F.q("0x10") == "0x10"
    assert F.q(1_000_000_000) == "0x3b9aca00"
    assert F.from_q("0xff") == 255
    assert F.from_q("0x0") == 0


def test_data_encoding():
    assert F.d("0x") == "0x"
    assert F.d("abc") == "0x0abc"   # even-padded
    assert F.d("0xabcd") == "0xabcd"


def test_keccak_and_alias():
    # keccak256("") well-known digest
    assert F.keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    from rpc.methods.evm_compat.bridge import anim_to_evm, evm_to_anim
    anim = "anim1zqpf6a5hvup7kggxdutt3tgswz4aa9rwlpsz8ywf73m4l5cmzhfk7pcnh6kgt"
    alias = anim_to_evm(anim)
    assert alias.startswith("0x") and len(alias) == 42
    # deterministic
    assert anim_to_evm(anim) == alias
    # reverse resolves (in-process cache populated on derive)
    assert evm_to_anim(alias) == anim


@pytest.fixture
def mock_registry(monkeypatch):
    HEAD = {"height": 27300, "number": 27300,
            "hash": "0x0000002090e8d3b6e4badff2b4db9a6b9a99a57de33aa08b006ea95dc007a899",
            "chainId": 1, "thetaMicro": 18647668, "timestamp": 1782318774,
            "nonce": 12345, "mixSeed": "0x" + "ab" * 32, "roots": {"txsRoot": "0x" + "00" * 32}}
    BLK = {"number": 5, "height": 5, "hash": "0x" + "11" * 32, "parentHash": "0x" + "22" * 32,
           "timestamp": 1776433971, "thetaMicro": 900000, "nonce": 7,
           "roots": {"txsRoot": "0x" + "00" * 32}, "transactions": []}
    data = {
        "chain.getHead": lambda *a, **k: HEAD,
        "chain.getChainId": lambda *a, **k: 1,
        "chain.getBlockByHeight": lambda h, *a, **k: (BLK if int(h) == 5 else (HEAD if int(h) == 27300 else None)),
        "chain.getBlockByHash": lambda *a, **k: BLK,
        "state.getBalance": lambda addr, *a, **k: "0x3b9aca00",  # 1e9 nANM
        "state.getNonce": lambda addr, *a, **k: 7,
        "node.health": lambda *a, **k: {"version": "0.1.0-dev", "peers_connected": 3,
                                        "chain_id": 1, "syncing": False, "head_height": 27300},
        "tx.getTransactionByHash": lambda txid, *a, **k: None,
    }

    class Spec:
        def __init__(self, fn):
            self.func = fn

    monkeypatch.setattr(F, "_REG", {k: Spec(v) for k, v in data.items()})
    return data


def test_eth_chainid_blocknumber(mock_registry, monkeypatch):
    monkeypatch.delenv("ANIMICA_EVM_CHAIN_ID", raising=False)
    from rpc.methods.evm_compat import eth_methods as E
    # Dedicated EVM-facade chain id (149 = 0x95), decoupled from the native
    # chain id (1 = Ethereum mainnet). The chain itself is never reset for EVM.
    assert E.eth_chainId() == "0x95"
    assert E.eth_blockNumber() == "0x6aa4"  # 27300
    # env override wins
    monkeypatch.setenv("ANIMICA_EVM_CHAIN_ID", "424242")
    assert E.eth_chainId() == F.q(424242)
    monkeypatch.delenv("ANIMICA_EVM_CHAIN_ID", raising=False)


def test_net_web3(mock_registry):
    from rpc.methods.evm_compat import net_web3 as N
    assert N.net_version() == "149"
    assert N.net_listening() is True
    assert N.net_peerCount() == "0x3"
    assert N.web3_clientVersion().startswith("Animica/")
    assert N.web3_sha3("0x") == "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"


def test_eth_getblock_shape(mock_registry):
    from rpc.methods.evm_compat import eth_methods as E
    b = E.eth_getBlockByNumber("0x5", False)
    assert b["number"] == "0x5"
    assert b["hash"] == "0x" + "11" * 32
    assert b["parentHash"] == "0x" + "22" * 32
    assert b["gasLimit"] == F.q(F.GAS_LIMIT)
    assert b["transactions"] == []
    assert b["difficulty"] == F.q(900000)


def test_eth_getbalance_via_alias(mock_registry):
    from rpc.methods.evm_compat import eth_methods as E
    from rpc.methods.evm_compat.bridge import anim_to_evm
    anim = "anim1zqpf6a5hvup7kggxdutt3tgswz4aa9rwlpsz8ywf73m4l5cmzhfk7pcnh6kgt"
    alias = anim_to_evm(anim)
    # by anim1 directly
    assert E.eth_getBalance(anim) == "0x3b9aca00"
    # by 0x alias (resolves through the bridge)
    assert E.eth_getBalance(alias) == "0x3b9aca00"
    # unknown 0x -> 0x0
    assert E.eth_getBalance("0x" + "de" * 20) == "0x0"
    assert E.eth_getTransactionCount(anim) == "0x7"


def test_eth_sendrawtransaction_unsupported(mock_registry):
    from rpc.methods.evm_compat import eth_tx as T
    from rpc.methods.evm_compat.errors_evm import RPC_METHOD_NOT_SUPPORTED
    with pytest.raises(Exception) as ei:
        T.eth_sendRawTransaction("0x02f8...")
    assert getattr(ei.value, "code", None) == RPC_METHOD_NOT_SUPPORTED


def test_gas_estimate_call(mock_registry):
    from rpc.methods.evm_compat import eth_tx as T
    assert T.eth_gasPrice() == "0x1"
    assert T.eth_estimateGas({}) == F.q(21000)
    assert T.eth_call({"to": "0x" + "11" * 20, "data": "0x06fdde03"}) == "0x"
    assert T.eth_getLogs({}) == []
