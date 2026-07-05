from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cbor2

from execution.runtime.contracts import apply_call, simulate_call
from execution.types.status import TxStatus
from omni_sdk.types.abi import decode_return, encode_call


ROOT = Path(__file__).resolve().parents[2]
STANDARDS = ROOT / "contracts" / "standards"


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


def _envs(sender: bytes) -> tuple[SimpleNamespace, SimpleNamespace]:
    block_env = SimpleNamespace(
        height=100,
        timestamp=1_700_000_000,
        chain_id=1337,
        coinbase=b"\x00" * 32,
        treasury=b"\xff" * 32,
    )
    tx_env = SimpleNamespace(sender=sender, gas_price=0, base_price=0, value=0)
    return block_env, tx_env


def _manifest_for(source: str, abi_entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": "TestPackage",
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
    return cbor2.dumps({"manifest": manifest, "code": source.encode("utf-8")}, canonical=True)


def _abi_entries(manifest_path: Path) -> list[dict[str, Any]]:
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    funcs = raw["abi"]["functions"]
    return [
        {
            "type": "function",
            "name": fn["name"],
            "inputs": fn.get("inputs", []),
            "outputs": fn.get("outputs", []),
        }
        for fn in funcs
    ]


def _deploy(state: _State, contract_addr: bytes, *, source_path: Path, manifest_path: Path) -> list[dict[str, Any]]:
    source = source_path.read_text(encoding="utf-8")
    abi_entries = _abi_entries(manifest_path)
    state.set_code(contract_addr, _package_blob(_manifest_for(source, abi_entries), source))
    return abi_entries


def _send_call(
    state: _State,
    *,
    sender: bytes,
    to: bytes,
    abi_entries: list[dict[str, Any]],
    fn: str,
    args: list[Any],
    value: int = 0,
) -> None:
    calldata = bytes(encode_call(abi_entries, fn, args))
    tx_payload: dict[str, Any] = {"to": to, "data": calldata}
    if value:
        tx_payload["value"] = int(value)
    tx = {
        "nonce": state.get_nonce(sender),
        "gas_limit": 5_000_000,
        "payload": {
            "t": 2,
            "v": tx_payload,
        },
    }
    block_env, tx_env = _envs(sender)
    res = apply_call(tx=tx, state=state, block_env=block_env, tx_env=tx_env)
    assert res.status == TxStatus.SUCCESS, res.error if hasattr(res, "error") else res


def _view(
    state: _State,
    *,
    sender: bytes,
    contract_addr: bytes,
    abi_entries: list[dict[str, Any]],
    fn: str,
    args: list[Any],
) -> Any:
    raw = simulate_call(
        state=state,
        contract_addr=contract_addr,
        calldata=encode_call(abi_entries, fn, args),
        sender=sender,
    )
    return decode_return(abi_entries, fn, raw)


def test_router_pair_lp_and_remove_flow_uses_user_as_owner(monkeypatch) -> None:
    # ANM-C05/C06: the raw-exec contract path is fail-closed by default and
    # REVERTs unless an operator explicitly opts into the unsafe developer path.
    # This integration test drives real contract source through that path, so it
    # opts in for the duration of the test (never set on consensus/mainnet nodes).
    monkeypatch.setenv("ANIMICA_VM_ALLOW_UNSAFE_EXEC", "1")

    user = b"\x10" * 32
    token_a = b"\xa1" * 32
    token_b = b"\xb2" * 32
    factory = b"\xf1" * 32
    router = b"\xf2" * 32
    pair = b"\xf3" * 32

    state = _State()
    state.set_balance(user, 10_000_000)

    token_abi = _deploy(
        state,
        token_a,
        source_path=STANDARDS / "animica_token" / "contract.py",
        manifest_path=STANDARDS / "animica_token" / "manifest.json",
    )
    _deploy(
        state,
        token_b,
        source_path=STANDARDS / "animica_token" / "contract.py",
        manifest_path=STANDARDS / "animica_token" / "manifest.json",
    )
    factory_abi = _deploy(
        state,
        factory,
        source_path=STANDARDS / "animica_dex_factory" / "contract.py",
        manifest_path=STANDARDS / "animica_dex_factory" / "manifest.json",
    )
    router_abi = _deploy(
        state,
        router,
        source_path=STANDARDS / "animica_dex_router" / "contract.py",
        manifest_path=STANDARDS / "animica_dex_router" / "manifest.json",
    )
    pair_abi = _deploy(
        state,
        pair,
        source_path=STANDARDS / "animica_dex_pair" / "contract.py",
        manifest_path=STANDARDS / "animica_dex_pair" / "manifest.json",
    )

    # Init two fungible tokens owned by the user.
    _send_call(
        state,
        sender=user,
        to=token_a,
        abi_entries=token_abi,
        fn="init",
        args=[b"TokenA", b"TKA", 18, user, 1_000_000, 1_000_000, False, b"", b""],
    )
    _send_call(
        state,
        sender=user,
        to=token_b,
        abi_entries=token_abi,
        fn="init",
        args=[b"TokenB", b"TKB", 18, user, 1_000_000, 1_000_000, False, b"", b""],
    )

    # Init DEX stack and create a pair through router/factory.
    _send_call(
        state,
        sender=user,
        to=factory,
        abi_entries=factory_abi,
        fn="init",
        args=[user, router, 30, 0, user],
    )
    _send_call(
        state,
        sender=user,
        to=router,
        abi_entries=router_abi,
        fn="init",
        args=[user, factory],
    )
    _send_call(
        state,
        sender=user,
        to=router,
        abi_entries=router_abi,
        fn="create_pair",
        args=[pair, token_a, token_b, 30, b""],
    )

    # Pair pulls tokens via transfer_from, so user approves pair as spender.
    _send_call(
        state,
        sender=user,
        to=token_a,
        abi_entries=token_abi,
        fn="approve",
        args=[pair, 200_000],
    )
    _send_call(
        state,
        sender=user,
        to=token_b,
        abi_entries=token_abi,
        fn="approve",
        args=[pair, 300_000],
    )

    # Add liquidity using reversed token order through router.
    _send_call(
        state,
        sender=user,
        to=router,
        abi_entries=router_abi,
        fn="add_liquidity",
        args=[token_b, token_a, 200_000, 100_000, 0, 0],
    )

    reserve0, reserve1 = _view(state, sender=user, contract_addr=pair, abi_entries=pair_abi, fn="reserves", args=[])
    assert reserve0 == 100_000
    assert reserve1 == 200_000

    user_lp = _view(state, sender=user, contract_addr=pair, abi_entries=pair_abi, fn="lp_balance_of", args=[user])
    router_lp = _view(state, sender=user, contract_addr=pair, abi_entries=pair_abi, fn="lp_balance_of", args=[router])
    assert user_lp > 0
    assert router_lp == 0

    # Remove half LP with reversed order and strict min amounts to verify min-mapping.
    burn_lp = user_lp // 2
    _send_call(
        state,
        sender=user,
        to=router,
        abi_entries=router_abi,
        fn="remove_liquidity",
        args=[token_b, token_a, burn_lp, 90_000, 45_000, 0],
    )

    user_lp_after = _view(state, sender=user, contract_addr=pair, abi_entries=pair_abi, fn="lp_balance_of", args=[user])
    assert user_lp_after == user_lp - burn_lp

    bal_a = _view(state, sender=user, contract_addr=token_a, abi_entries=token_abi, fn="balance_of", args=[user])
    bal_b = _view(state, sender=user, contract_addr=token_b, abi_entries=token_abi, fn="balance_of", args=[user])
    assert bal_a == 949_999
    assert bal_b == 899_999
