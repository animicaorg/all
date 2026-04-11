from __future__ import annotations

from types import SimpleNamespace

import pytest

from execution.errors import ExecError
from execution.runtime.contracts import apply_deploy
from execution.types.status import TxStatus


class _State:
    def __init__(self, balances: dict[bytes, int], nonces: dict[bytes, int] | None = None):
        self._balances = dict(balances)
        self._nonces = dict(nonces or {})

    def get_balance(self, addr: bytes) -> int:
        return int(self._balances.get(addr, 0))

    def set_balance(self, addr: bytes, value: int) -> None:
        self._balances[addr] = int(value)

    def get_nonce(self, addr: bytes) -> int:
        return int(self._nonces.get(addr, 0))

    def set_nonce(self, addr: bytes, value: int) -> None:
        self._nonces[addr] = int(value)


def test_apply_deploy_charges_32_byte_sender_and_increments_nonce() -> None:
    sender = b"\x01" * 32
    state = _State({sender: 1_000_000}, {sender: 0})
    block_env = SimpleNamespace(height=7, coinbase=b"\x02" * 32, treasury=b"\x03" * 32)
    tx_env = SimpleNamespace(sender=sender, gas_price=1, base_price=0)
    tx = {"gas_limit": 53_000, "nonce": 0, "hash": "0x" + "aa" * 32}

    result = apply_deploy(tx=tx, state=state, block_env=block_env, tx_env=tx_env)

    assert result.status == TxStatus.REVERT
    assert state.get_balance(sender) == 1_000_000 - 53_000
    assert state.get_nonce(sender) == 1


def test_apply_deploy_nonce_mismatch_raises_exec_error() -> None:
    sender = b"\x11" * 32
    state = _State({sender: 1_000_000}, {sender: 3})
    block_env = SimpleNamespace(height=7, coinbase=b"\x22" * 32, treasury=b"\x33" * 32)
    tx_env = SimpleNamespace(sender=sender, gas_price=1, base_price=0)
    tx = {"gas_limit": 53_000, "nonce": 2}

    with pytest.raises(ExecError) as exc:
        apply_deploy(tx=tx, state=state, block_env=block_env, tx_env=tx_env)
    assert getattr(exc.value, "code", None) == "NONCE_MISMATCH"
