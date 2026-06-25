"""Tests for the native-ANM ERC-20 facade (always) and the real EVM execution
lane (skipped when py-evm isn't installed).

The facade is gate-independent and read-only. The EVM lane is gated by
ANIMICA_EVM_EXECUTION and needs py-evm; the deploy/call/log test uses a tiny
compiled Solidity contract embedded as bytecode so the suite needs no solc.
"""

from __future__ import annotations

import importlib

import pytest

import eth_abi
from eth_account import Account
from eth_utils import function_signature_to_4byte_selector as sel4
from eth_utils import to_checksum_address as cs


# --------------------------------------------------------------------------- #
# native-ANM ERC-20 facade (gate-independent, read-only)
# --------------------------------------------------------------------------- #
def test_facade_metadata_and_readonly():
    from rpc.methods.evm_compat import erc20_native as T
    assert T.is_token(T.ANM_TOKEN_ADDRESS) and not T.is_token("0x" + "11" * 20)
    assert eth_abi.decode(["string"], bytes.fromhex(T.handle_call("0x06fdde03")[2:]))[0] == "Animica"
    assert eth_abi.decode(["string"], bytes.fromhex(T.handle_call("0x95d89b41")[2:]))[0] == "ANM"
    assert eth_abi.decode(["uint8"], bytes.fromhex(T.handle_call("0x313ce567")[2:]))[0] == 9
    # balanceOf(unknown) -> 0
    bo = "0x70a08231" + eth_abi.encode(["address"], ["0x" + "de" * 20]).hex()
    assert eth_abi.decode(["uint256"], bytes.fromhex(T.handle_call(bo)[2:]))[0] == 0
    # transfer reverts (read-only)
    with pytest.raises(T.FacadeRevert):
        T.handle_call("0xa9059cbb" + eth_abi.encode(["address", "uint256"], ["0x" + "de" * 20, 1]).hex())


def test_facade_via_eth_call_gate_independent():
    from rpc import methods
    methods.ensure_loaded()
    from rpc.methods.evm_compat import erc20_native as T
    out = methods.get_methods()["eth_call"].func({"to": T.ANM_TOKEN_ADDRESS, "data": "0x95d89b41"})
    assert eth_abi.decode(["string"], bytes.fromhex(out[2:]))[0] == "ANM"
    # getCode returns the stub so wallets treat the token address as a contract
    assert methods.get_methods()["eth_getCode"].func(T.ANM_TOKEN_ADDRESS) == T.STUB_CODE


# --------------------------------------------------------------------------- #
# EVM execution lane (needs py-evm)
# --------------------------------------------------------------------------- #
pyevm = pytest.importorskip("eth", reason="py-evm not installed")

# Mini { uint256 public stored; event Set(address indexed who, uint256 value);
#        function set(uint256 v){ stored=v; emit Set(msg.sender,v); } }  (solc 0.8.26)
MINI_BIN = (
    "6080604052348015600e575f80fd5b5061018e8061001c5f395ff3fe608060405234801561000f575f80fd5b50"
    "60043610610034575f3560e01c806360fe47b114610038578063e582dd3114610054575b5f80fd5b6100526004"
    "80360381019061004d9190610105565b610072565b005b61005c6100c9565b60405161006991906101"
    "3f565b60405180910390f35b805f819055503373ffffffffffffffffffffffffffffffffffffffff167ffd28ec"
    "3ec2555238d8ad6f9faf3e4cd10e574ce7e7ef28b73caa53f9512f65b9826040516100be919061013f565b6040"
    "5180910390a250565b5f5481565b5f80fd5b5f819050919050565b6100e4816100d2565b81146100ee575f80fd"
    "5b50565b5f813590506100ff816100db565b92915050565b5f6020828403121561011a576101196100ce565b5b"
    "5f610127848285016100f1565b91505092915050565b610139816100d2565b82525050565b5f60208201905061"
    "01525f830184610130565b9291505056fea264697066735822122045bf846d08e46974ba33aaa960452b64b479"
    "c7698b3b782be2471b9d2eeac5d364736f6c634300081a0033"
)


@pytest.fixture
def evm_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_EVM_EXECUTION", "1")
    monkeypatch.setenv("ANIMICA_EVM_RELAYER", "1")
    monkeypatch.setenv("ANIMICA_EVM_RELAYER_KEK", "%064x" % 0xEEEE)
    monkeypatch.setenv("ANIMICA_EVM_DIR", str(tmp_path))
    import rpc.methods.evm_compat.relayer as R
    import rpc.methods.evm_compat.evm_runtime as RT
    importlib.reload(R)
    importlib.reload(RT)
    R._CONN = None
    R._KEK = None
    R._KEK_RESOLVED = False
    R._AEAD = None
    RT.reset_for_tests()
    RT._UNAVAILABLE = None
    from rpc import methods
    methods.ensure_loaded()
    return methods, R, RT


def _signed(acct, tx):
    return "0x" + acct.sign_transaction(tx).raw_transaction.hex()


def test_evm_deploy_call_logs(evm_env):
    methods, R, RT = evm_env
    reg = methods.get_methods()
    E = Account.from_key("0x" + "ab" * 31 + "07")
    R.get_or_create_account(E.address.lower())

    # deploy
    raw = _signed(E, {"to": None, "data": "0x" + MINI_BIN, "value": 0, "nonce": 0, "gas": 1_000_000,
                      "maxFeePerGas": 10 ** 9, "maxPriorityFeePerGas": 10 ** 9, "chainId": 149, "type": 2})
    ehash = reg["eth_sendRawTransaction"].func(raw)
    rcpt = reg["eth_getTransactionReceipt"].func(ehash)
    assert rcpt["status"] == "0x1"
    caddr = cs(rcpt["contractAddress"])
    assert len(reg["eth_getCode"].func(caddr)) > 2          # real code stored

    # call set(99) -> emits Set(E, 99)
    data = "0x" + (sel4("set(uint256)") + eth_abi.encode(["uint256"], [99])).hex()
    raw2 = _signed(E, {"to": caddr, "data": data, "value": 0, "nonce": 1, "gas": 100_000,
                       "maxFeePerGas": 10 ** 9, "maxPriorityFeePerGas": 10 ** 9, "chainId": 149, "type": 2})
    r2 = reg["eth_getTransactionReceipt"].func(reg["eth_sendRawTransaction"].func(raw2))
    assert r2["status"] == "0x1" and len(r2["logs"]) == 1

    # eth_call stored() -> 99
    out = reg["eth_call"].func({"to": caddr, "data": "0x" + sel4("stored()").hex(), "from": E.address})
    assert eth_abi.decode(["uint256"], bytes.fromhex(out[2:]))[0] == 99

    # eth_getLogs by the Set topic
    from eth_utils import keccak
    topic = "0x" + keccak(text="Set(address,uint256)").hex()
    logs = reg["eth_getLogs"].func({"fromBlock": "earliest", "address": caddr, "topics": [topic]})
    assert len(logs) == 1


def test_evm_rejects_value_and_requires_provisioned(evm_env):
    methods, R, RT = evm_env
    reg = methods.get_methods()
    # a contract call to a deployed address, but unprovisioned sender -> rejected
    E = Account.from_key("0x" + "cd" * 31 + "07")
    R.get_or_create_account(E.address.lower())
    raw = _signed(E, {"to": None, "data": "0x" + MINI_BIN, "value": 0, "nonce": 0, "gas": 1_000_000,
                      "maxFeePerGas": 10 ** 9, "maxPriorityFeePerGas": 10 ** 9, "chainId": 149, "type": 2})
    caddr = cs(reg["eth_getTransactionReceipt"].func(reg["eth_sendRawTransaction"].func(raw))["contractAddress"])
    # value > 0 to the contract -> rejected (v1 no payable forwarding)
    stranger = Account.from_key("0x" + "ef" * 31 + "07")
    R.get_or_create_account(stranger.address.lower())
    data = "0x" + (sel4("set(uint256)") + eth_abi.encode(["uint256"], [1])).hex()
    raw_val = _signed(stranger, {"to": caddr, "data": data, "value": 10 ** 18, "nonce": 0, "gas": 100_000,
                                 "maxFeePerGas": 10 ** 9, "maxPriorityFeePerGas": 10 ** 9, "chainId": 149, "type": 2})
    with pytest.raises(Exception):
        reg["eth_sendRawTransaction"].func(raw_val)
