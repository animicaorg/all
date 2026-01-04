import pytest
from types import SimpleNamespace

from execution.errors import ExecError
from execution.runtime.transfers import apply_transfer
from execution.types.status import TxStatus


class MockState:
    def __init__(self, balances: dict[bytes, int]) -> None:
        self._balances = dict(balances)
        self._nonces: dict[bytes, int] = {}

    def get_balance(self, addr: bytes) -> int:
        return int(self._balances.get(addr, 0))

    def set_balance(self, addr: bytes, value: int) -> None:
        self._balances[addr] = int(value)

    def get_nonce(self, addr: bytes) -> int:
        return int(self._nonces.get(addr, 0))

    def set_nonce(self, addr: bytes, value: int) -> None:
        self._nonces[addr] = int(value)


def test_transfer_debits_sender_credits_recipient_and_fees() -> None:
    sender = b"\x01" * 32
    recipient = b"\x02" * 32
    coinbase = b"\x03" * 32
    treasury = b"\x04" * 32

    state = MockState(
        {
            sender: 1_000_000,
            recipient: 50,
            coinbase: 0,
            treasury: 0,
        }
    )

    tx = {"to": recipient, "amount": 100, "gas_limit": 21_000}
    block_env = SimpleNamespace(coinbase=coinbase, treasury=treasury)
    tx_env = SimpleNamespace(sender=sender, gas_price=3, base_price=1)

    result = apply_transfer(tx=tx, state=state, block_env=block_env, tx_env=tx_env)
    assert result.status == TxStatus.SUCCESS

    gas_used = result.gas_used
    total_fee = gas_used * 3
    base_fee = gas_used * 1
    tip_fee = gas_used * 2

    assert state.get_balance(sender) == 1_000_000 - 100 - total_fee
    assert state.get_balance(recipient) == 50 + 100
    assert state.get_balance(coinbase) == tip_fee
    assert state.get_balance(treasury) == base_fee
    assert state.get_nonce(sender) == 1


def test_transfer_rejects_underpriced_fee() -> None:
    sender = b"\x05" * 32
    recipient = b"\x06" * 32

    state = MockState({sender: 1_000_000, recipient: 0})

    tx = {"to": recipient, "amount": 10, "gas_limit": 21_000}
    block_env = SimpleNamespace(coinbase=b"\x07" * 32, treasury=b"\x08" * 32)
    tx_env = SimpleNamespace(sender=sender, gas_price=1, base_price=2)

    with pytest.raises(ExecError) as exc:
        apply_transfer(tx=tx, state=state, block_env=block_env, tx_env=tx_env)

    assert exc.value.code == "FEE_TOO_LOW"
