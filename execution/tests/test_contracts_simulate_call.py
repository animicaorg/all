from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import cbor2
import pytest

from execution.runtime.contracts import (
    ContractNotFoundError,
    InvalidCalldataError,
    apply_call,
    apply_deploy,
    simulate_call,
)
from execution.types.status import TxStatus
from omni_sdk.types.abi import decode_return, encode_call


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


def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _package_blob(manifest: dict[str, Any], code_bytes: bytes) -> bytes:
    return cbor2.dumps({"manifest": manifest, "code": bytes(code_bytes)}, canonical=True)


def test_apply_deploy_apply_call_and_simulate_get_counter_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ANM-C05/C06: raw contract exec() is disabled by default in 6.0.0.
    # This test exercises the controlled deploy/call/simulate path, so opt in
    # to the dev/test-only unsafe-exec flag (never enabled on real nodes).
    monkeypatch.setenv("ANIMICA_VM_ALLOW_UNSAFE_EXEC", "1")
    sender = b"\x11" * 32
    state = _State()
    state.set_balance(sender, 1_000_000)
    manifest = _counter_manifest()
    abi_entries = _counter_abi_entries()

    deploy_tx = {
        "nonce": 0,
        "gas_limit": 200_000,
        "payload": {
            "t": 1,
            "v": {
                "code": _COUNTER_SOURCE.encode("utf-8"),
                "manifest": _manifest_bytes(manifest),
            },
        },
    }
    block_env = SimpleNamespace(
        height=1,
        chain_id=1,
        coinbase=b"\x00" * 32,
        treasury=b"\x00" * 32,
    )
    tx_env = SimpleNamespace(sender=sender, gas_price=0, base_price=0)

    deploy_res = apply_deploy(tx=deploy_tx, state=state, block_env=block_env, tx_env=tx_env)
    assert deploy_res.status == TxStatus.SUCCESS
    contract_hex = deploy_res.receipt["contractAddress"]
    contract_addr = bytes.fromhex(contract_hex[2:])

    inc_data = encode_call(abi_entries, "inc", [])
    call_tx = {
        "nonce": 1,
        "gas_limit": 200_000,
        "payload": {
            "t": 2,
            "v": {
                "to": contract_addr,
                "data": bytes(inc_data),
            },
        },
    }

    call_res = apply_call(tx=call_tx, state=state, block_env=block_env, tx_env=tx_env)
    assert call_res.status == TxStatus.SUCCESS

    get_data = encode_call(abi_entries, "get", [])
    raw_get = simulate_call(state=state, contract_addr=contract_addr, calldata=get_data)
    assert decode_return(abi_entries, "get", raw_get) == 1


def test_simulate_call_is_read_only_and_does_not_mutate_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ANM-C05/C06: raw contract exec() is disabled by default in 6.0.0.
    # simulate_call runs the contract method, so opt in to the dev/test-only
    # unsafe-exec flag (never enabled on real nodes).
    monkeypatch.setenv("ANIMICA_VM_ALLOW_UNSAFE_EXEC", "1")
    sender = b"\x22" * 32
    contract_addr = b"\x33" * 32
    state = _State()
    state.set_balance(sender, 99)
    state.set_nonce(sender, 7)

    manifest = _counter_manifest()
    abi_entries = _counter_abi_entries()
    state.set_code(contract_addr, _package_blob(manifest, _COUNTER_SOURCE.encode("utf-8")))
    state.set_storage(contract_addr, b"counter", b"\x05")

    inc_data = encode_call(abi_entries, "inc", [])
    raw_a = simulate_call(
        state=state,
        contract_addr=contract_addr,
        calldata=inc_data,
        sender=sender,
    )
    raw_b = simulate_call(
        state=state,
        contract_addr=contract_addr,
        calldata=inc_data,
        sender=sender,
    )

    assert decode_return(abi_entries, "inc", raw_a) == 6
    assert decode_return(abi_entries, "inc", raw_b) == 6
    assert state.get_storage(contract_addr, b"counter") == b"\x05"
    assert state.get_balance(sender) == 99
    assert state.get_nonce(sender) == 7


def test_simulate_call_errors_for_missing_contract_and_invalid_calldata() -> None:
    contract_addr = b"\x44" * 32
    state = _State()

    with pytest.raises(ContractNotFoundError):
        simulate_call(state=state, contract_addr=contract_addr, calldata=b"\x00" * 8)

    manifest = _counter_manifest()
    state.set_code(contract_addr, _package_blob(manifest, _COUNTER_SOURCE.encode("utf-8")))

    with pytest.raises(InvalidCalldataError):
        simulate_call(state=state, contract_addr=contract_addr, calldata=b"\x01\x02")

    with pytest.raises(InvalidCalldataError):
        simulate_call(state=state, contract_addr=contract_addr, calldata=b"\xff" * 8)
