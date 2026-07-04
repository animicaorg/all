from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import cbor2
import pytest

from omni_sdk.types.abi import decode_return, encode_call
from rpc import deps
from rpc import errors as rpc_errors
from rpc import methods as rpc_methods
from rpc.methods import state as state_methods


_COUNTER_SOURCE = """
from stdlib import storage

KEY = b"counter"


def _load() -> int:
    raw = storage.get(KEY, b"")
    if raw == b"":
        return 0
    return int.from_bytes(raw, "big")


def _save(v: int) -> None:
    n = int(v)
    if n <= 0:
        storage.delete(KEY)
        return
    storage.set(KEY, n.to_bytes(max(1, (n.bit_length() + 7) // 8), "big"))


def get() -> int:
    return _load()


def inc() -> int:
    n = _load() + 1
    _save(n)
    return n
""".strip()


class _State:
    def __init__(self) -> None:
        self._balances: dict[bytes, int] = {}
        self._nonces: dict[bytes, int] = {}
        self._code: dict[bytes, bytes] = {}
        self._storage: dict[tuple[bytes, bytes], bytes] = {}

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


def _counter_manifest() -> dict[str, Any]:
    abi_entries = _counter_abi_entries()
    return {
        "name": "Counter",
        "source": _COUNTER_SOURCE,
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


def _counter_abi_entries() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "get",
            "inputs": [],
            "outputs": [{"type": "int"}],
        },
        {
            "type": "function",
            "name": "inc",
            "inputs": [],
            "outputs": [{"type": "int"}],
        },
    ]


def _package_blob(manifest: dict[str, Any]) -> bytes:
    return cbor2.dumps(
        {
            "manifest": manifest,
            "code": _COUNTER_SOURCE.encode("utf-8"),
        },
        canonical=True,
    )


def _dispatch(method: str, params: Any) -> Any:
    return asyncio.run(rpc_methods.dispatch(method, params))


def _as_hex32(value: bytes) -> str:
    return "0x" + value.hex()


def test_state_call_supports_canonical_and_alias_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    # ANM-C05/C06: the raw exec() simulation path is fail-closed by default.
    # state_call simulation is a read-only, non-consensus dev path, so opt into
    # the explicit developer flag for this controlled unit test.
    monkeypatch.setenv("ANIMICA_VM_ALLOW_UNSAFE_EXEC", "1")
    state = _State()
    contract_addr = b"\x11" * 32
    manifest = _counter_manifest()
    abi_entries = _counter_abi_entries()
    state.set_code(contract_addr, _package_blob(manifest))
    state.set_storage(contract_addr, b"counter", b"\x04")

    monkeypatch.setattr(deps, "get_ctx", lambda: SimpleNamespace(state_db=state))
    rpc_methods.ensure_loaded()

    calldata = encode_call(abi_entries, "get", [])
    payload = {"to": _as_hex32(contract_addr), "data": "0x" + calldata.hex()}

    res_state_call = _dispatch("state.call", [payload])
    res_exec_alias = _dispatch("execution.simulateCall", [payload])
    res_vm_alias = _dispatch(
        "vm.simulateCall",
        [_as_hex32(contract_addr), "0x" + calldata.hex(), None, None],
    )

    assert res_state_call == res_exec_alias == res_vm_alias
    raw_bytes = bytes.fromhex(str(res_state_call)[2:])
    assert decode_return(abi_entries, "get", raw_bytes) == 4


def test_state_call_is_read_only_and_does_not_mutate_state(monkeypatch: pytest.MonkeyPatch) -> None:
    # ANM-C05/C06: the raw exec() simulation path is fail-closed by default.
    # state_call simulation is a read-only, non-consensus dev path, so opt into
    # the explicit developer flag for this controlled unit test.
    monkeypatch.setenv("ANIMICA_VM_ALLOW_UNSAFE_EXEC", "1")
    state = _State()
    contract_addr = b"\x22" * 32
    sender = b"\x99" * 32
    manifest = _counter_manifest()
    abi_entries = _counter_abi_entries()
    state.set_code(contract_addr, _package_blob(manifest))
    state.set_storage(contract_addr, b"counter", b"\x05")
    state.set_balance(sender, 123)
    state.set_nonce(sender, 8)

    monkeypatch.setattr(deps, "get_ctx", lambda: SimpleNamespace(state_db=state))
    rpc_methods.ensure_loaded()

    inc_data = encode_call(abi_entries, "inc", [])
    payload = {
        "to": _as_hex32(contract_addr),
        "from": _as_hex32(sender),
        "data": "0x" + inc_data.hex(),
    }

    raw_a = _dispatch("state.call", [payload])
    raw_b = _dispatch("state.call", [payload])

    assert decode_return(abi_entries, "inc", bytes.fromhex(raw_a[2:])) == 6
    assert decode_return(abi_entries, "inc", bytes.fromhex(raw_b[2:])) == 6
    assert state.get_storage(contract_addr, b"counter") == b"\x05"
    assert state.get_balance(sender) == 123
    assert state.get_nonce(sender) == 8


def test_state_call_reports_parameter_and_execution_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _State()
    contract_addr = b"\x33" * 32
    manifest = _counter_manifest()
    state.set_code(contract_addr, _package_blob(manifest))
    monkeypatch.setattr(deps, "get_ctx", lambda: SimpleNamespace(state_db=state))

    with pytest.raises(rpc_errors.InvalidParams):
        state_methods.state_call({"to": "not-an-address", "data": "0x00"})

    with pytest.raises(rpc_errors.InvalidParams):
        state_methods.state_call()

    with pytest.raises(rpc_errors.NotFound):
        state_methods.state_call({"to": _as_hex32(b"\xaa" * 32), "data": "0x" + ("00" * 8)})

    with pytest.raises(rpc_errors.InvalidParams):
        state_methods.state_call({"to": _as_hex32(contract_addr), "data": "0x" + ("ff" * 8)})
