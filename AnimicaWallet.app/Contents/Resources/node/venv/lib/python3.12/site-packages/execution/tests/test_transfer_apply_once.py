from types import SimpleNamespace

import pytest

from execution.errors import ExecError
from execution.runtime.transfers import apply_transfer
from execution.types.status import TxStatus


class MockState:
    def __init__(self, balances: dict[bytes, int], nonces: dict[bytes, int] | None = None) -> None:
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


def test_same_tx_cannot_be_applied_twice_nonce_guard() -> None:
    sender = b"\x11" * 32
    recipient = b"\x22" * 32

    state = MockState({sender: 1000, recipient: 0}, {sender: 0})

    tx = {"to": recipient, "amount": 10, "gas_limit": 21_000, "nonce": 0}
    block_env = SimpleNamespace(coinbase=b"\x33" * 32, treasury=b"\x44" * 32)
    tx_env = SimpleNamespace(sender=sender, gas_price=0, base_price=0)

    first = apply_transfer(tx=tx, state=state, block_env=block_env, tx_env=tx_env)
    assert first.status == TxStatus.SUCCESS
    assert state.get_balance(sender) == 990
    assert state.get_balance(recipient) == 10
    assert state.get_nonce(sender) == 1

    with pytest.raises(ExecError) as exc:
        apply_transfer(tx=tx, state=state, block_env=block_env, tx_env=tx_env)

    assert exc.value.code == "NONCE_MISMATCH"
    assert state.get_balance(sender) == 990
    assert state.get_balance(recipient) == 10
    assert state.get_nonce(sender) == 1
