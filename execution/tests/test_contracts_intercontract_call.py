from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import cbor2

from execution.runtime.contracts import apply_call, simulate_call
from execution.types.status import TxStatus
from omni_sdk.types.abi import decode_return, encode_call


_CALLEE_SOURCE = """
from stdlib import abi, storage


def ping(v: int) -> int:
    storage.set(b"sender", abi.caller())
    storage.set(b"value", int(abi.value()).to_bytes(8, "big"))
    return int(v) + 1


def fail() -> None:
    abi.revert(b"callee_fail")


def last_sender() -> bytes:
    return storage.get(b"sender", b"")


def last_value() -> int:
    raw = storage.get(b"value", b"")
    if raw == b"":
        return 0
    return int.from_bytes(raw, "big")
""".strip()


_CALLER_SOURCE = """
from stdlib import abi, storage


def call_ping(target: bytes, v: int) -> int:
    out = abi.call_contract(target, "ping", [int(v)])
    storage.set(b"last", int(out).to_bytes(8, "big"))
    return int(out)


def call_ping_with_value(target: bytes, v: int, amount: int) -> int:
    out = abi.call_contract(target, "ping", [int(v)], int(amount))
    storage.set(b"last", int(out).to_bytes(8, "big"))
    return int(out)


def call_fail(target: bytes) -> int:
    abi.call_contract(target, "fail", [])
    return 1
""".strip()


class _State:
    def __init__(self) -> None:
        self._balances: dict[bytes, int] = {}
        self._nonces: dict[bytes, int] = {}
        self._code: dict[bytes, bytes] = {}
        self._storage: dict[tuple[bytes, bytes], bytes] = {}

    def ensure_account(self, addr: bytes) -> None:
        self._balances.setdefault(addr, 0)
        self._nonces.setdefault(addr, 0)

    def get_balance(self, addr: bytes) -> int:
        return int(self._balances.get(addr, 0))

    def set_balance(self, addr: bytes, value: int) -> None:
        self._balances[addr] = int(value)

    def get_nonce(self, addr: bytes) -> int:
        return int(self._nonces.get(addr, 0))

    def set_nonce(self, addr: bytes, value: int) -> None:
        self._nonces[addr] = int(value)

    def get_code(self, addr: bytes) -> bytes | None:
        return self._code.get(addr)

    def set_code(self, addr: bytes, code: bytes) -> None:
        self._code[addr] = bytes(code)

    def get_storage(self, addr: bytes, key: bytes) -> bytes | None:
        return self._storage.get((addr, key))

    def set_storage(self, addr: bytes, key: bytes, value: bytes) -> None:
        self._storage[(addr, key)] = bytes(value)

    def delete_storage(self, addr: bytes, key: bytes) -> None:
        self._storage.pop((addr, key), None)

    def snapshot(self) -> dict[str, Any]:
        return {
            "balances": deepcopy(self._balances),
            "nonces": deepcopy(self._nonces),
            "code": deepcopy(self._code),
            "storage": deepcopy(self._storage),
        }

    def revert(self, snap: dict[str, Any]) -> None:
        self._balances = deepcopy(snap["balances"])
        self._nonces = deepcopy(snap["nonces"])
        self._code = deepcopy(snap["code"])
        self._storage = deepcopy(snap["storage"])


def _manifest_for(source: str, abi_entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": "TestContract",
        "source": source,
        "abi": {
            "functions": [
                {
                    "name": entry["name"],
                    "inputs": entry.get("inputs", []),
                    "outputs": entry.get("outputs", []),
                }
                for entry in abi_entries
                if entry.get("type") == "function"
            ],
            "events": [],
        },
    }


def _package_blob(manifest: dict[str, Any], source: str) -> bytes:
    return cbor2.dumps(
        {
            "manifest": manifest,
            "code": source.encode("utf-8"),
        },
        canonical=True,
    )


def _callee_abi() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "ping",
            "inputs": [{"type": "int"}],
            "outputs": [{"type": "int"}],
        },
        {
            "type": "function",
            "name": "fail",
            "inputs": [],
            "outputs": [],
        },
        {
            "type": "function",
            "name": "last_sender",
            "inputs": [],
            "outputs": [{"type": "bytes"}],
        },
        {
            "type": "function",
            "name": "last_value",
            "inputs": [],
            "outputs": [{"type": "int"}],
        },
    ]


def _caller_abi() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "call_ping",
            "inputs": [{"type": "bytes"}, {"type": "int"}],
            "outputs": [{"type": "int"}],
        },
        {
            "type": "function",
            "name": "call_ping_with_value",
            "inputs": [{"type": "bytes"}, {"type": "int"}, {"type": "int"}],
            "outputs": [{"type": "int"}],
        },
        {
            "type": "function",
            "name": "call_fail",
            "inputs": [{"type": "bytes"}],
            "outputs": [{"type": "int"}],
        },
    ]


def _envs(sender: bytes):
    block_env = SimpleNamespace(
        height=1,
        timestamp=1_700_000_000,
        chain_id=1,
        coinbase=b"\x00" * 32,
        treasury=b"\x00" * 32,
    )
    tx_env = SimpleNamespace(sender=sender, gas_price=0, base_price=0, value=0)
    return block_env, tx_env


def test_inter_contract_call_propagates_caller_and_value(monkeypatch) -> None:
    # ANM-C05/C06: the raw-source exec() contract path is fail-closed by default.
    # This test exercises inter-contract dispatch semantics (caller/value
    # propagation), not the exec-disable security policy, so opt into the
    # documented dev/test path. Mainnet never sets this flag.
    monkeypatch.setenv("ANIMICA_VM_ALLOW_UNSAFE_EXEC", "1")
    user = b"\x10" * 32
    caller_addr = b"\x22" * 32
    callee_addr = b"\x33" * 32

    state = _State()
    state.set_balance(user, 100)
    state.set_balance(caller_addr, 1_000)
    state.set_code(callee_addr, _package_blob(_manifest_for(_CALLEE_SOURCE, _callee_abi()), _CALLEE_SOURCE))
    state.set_code(caller_addr, _package_blob(_manifest_for(_CALLER_SOURCE, _caller_abi()), _CALLER_SOURCE))

    caller_abi = _caller_abi()
    calldata = encode_call(caller_abi, "call_ping_with_value", [callee_addr, 7, 25])

    block_env, tx_env = _envs(user)
    tx = {
        "nonce": 0,
        "gas_limit": 200_000,
        "payload": {
            "t": 2,
            "v": {
                "to": caller_addr,
                "data": bytes(calldata),
            },
        },
    }

    res = apply_call(tx=tx, state=state, block_env=block_env, tx_env=tx_env)
    assert res.status == TxStatus.SUCCESS

    callee_abi = _callee_abi()
    raw_sender = simulate_call(
        state=state,
        contract_addr=callee_addr,
        calldata=encode_call(callee_abi, "last_sender", []),
        sender=user,
    )
    assert decode_return(callee_abi, "last_sender", raw_sender) == caller_addr

    raw_value = simulate_call(
        state=state,
        contract_addr=callee_addr,
        calldata=encode_call(callee_abi, "last_value", []),
        sender=user,
    )
    assert decode_return(callee_abi, "last_value", raw_value) == 25
    assert state.get_balance(caller_addr) == 1_000 - 25
    assert state.get_balance(callee_addr) == 25


def test_inter_contract_revert_rolls_back_state(monkeypatch) -> None:
    # ANM-C05/C06: opt into the documented dev/test exec() path so the callee's
    # abi.revert() (not the disabled-exec fail-closed) drives the REVERT and the
    # nested-state rollback assertions below.
    monkeypatch.setenv("ANIMICA_VM_ALLOW_UNSAFE_EXEC", "1")
    user = b"\x41" * 32
    caller_addr = b"\x42" * 32
    callee_addr = b"\x43" * 32

    state = _State()
    state.set_balance(user, 100)
    state.set_balance(caller_addr, 500)
    state.set_code(callee_addr, _package_blob(_manifest_for(_CALLEE_SOURCE, _callee_abi()), _CALLEE_SOURCE))
    state.set_code(caller_addr, _package_blob(_manifest_for(_CALLER_SOURCE, _caller_abi()), _CALLER_SOURCE))

    caller_abi = _caller_abi()
    tx = {
        "nonce": 0,
        "gas_limit": 200_000,
        "payload": {
            "t": 2,
            "v": {
                "to": caller_addr,
                "data": bytes(encode_call(caller_abi, "call_fail", [callee_addr])),
            },
        },
    }

    block_env, tx_env = _envs(user)
    res = apply_call(tx=tx, state=state, block_env=block_env, tx_env=tx_env)
    assert res.status == TxStatus.REVERT

    # Caller nonce is incremented by tx processing, but contract state changes
    # from nested calls are rolled back.
    assert state.get_nonce(user) == 1
    assert state.get_balance(caller_addr) == 500
    assert state.get_balance(callee_addr) == 0
