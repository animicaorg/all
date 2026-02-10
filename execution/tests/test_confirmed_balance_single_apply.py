from __future__ import annotations

from types import SimpleNamespace

from execution.runtime.transfers import apply_transfer
from execution.state.apply_balance import assert_block_apply_deltas, begin_apply_block, end_apply_block


class _State:
    def __init__(self, balances: dict[bytes, int]) -> None:
        self._balances = dict(balances)
        self._nonces: dict[bytes, int] = {}
        self._applied: set[bytes] = set()
        self.reservations: dict[str, int] = {}

    def get_balance(self, addr: bytes) -> int:
        return int(self._balances.get(addr, 0))

    def set_balance(self, addr: bytes, value: int) -> None:
        self._balances[addr] = int(value)

    def get_nonce(self, addr: bytes) -> int:
        return int(self._nonces.get(addr, 0))

    def set_nonce(self, addr: bytes, value: int) -> None:
        self._nonces[addr] = int(value)

    def ensure_account(self, addr: bytes) -> None:
        self._balances.setdefault(addr, 0)

    def has_applied_tx(self, tx_hash: bytes) -> bool:
        return tx_hash in self._applied

    def mark_tx_applied(self, tx_hash: bytes, _height: int) -> None:
        self._applied.add(tx_hash)


def _mk_tx(
    sender: bytes, recipient: bytes, tx_hash: str, amount: int, fee_per_gas: int
) -> tuple[dict, SimpleNamespace, SimpleNamespace]:
    tx = {
        "to": recipient,
        "amount": amount,
        "gas_limit": 21_000,
        "nonce": 0,
        "hash": tx_hash,
    }
    block_env = SimpleNamespace(coinbase=b"\x03" * 32, treasury=b"\x04" * 32, height=1)
    tx_env = SimpleNamespace(sender=sender, gas_price=fee_per_gas, base_price=0)
    return tx, block_env, tx_env


def test_transfer_single_debit_single_credit() -> None:
    sender = b"\x01" * 32
    receiver = b"\x02" * 32
    state = _State({sender: 300_000, receiver: 0})
    tx, block_env, tx_env = _mk_tx(sender, receiver, "0x" + "ab" * 32, 10, 1)

    begin_apply_block(1, "0x" + "ff" * 32)
    result = apply_transfer(tx=tx, state=state, block_env=block_env, tx_env=tx_env)
    events = end_apply_block()

    expected_fee = result.gas_used * tx_env.gas_price
    assert state.get_balance(sender) == 300_000 - (10 + expected_fee)
    assert state.get_balance(receiver) == 10

    assert_block_apply_deltas(
        tx_expectations=[
            {
                "tx_hash": tx["hash"],
                "sender": sender.hex(),
                "recipient": receiver.hex(),
                "sender_delta": -(10 + expected_fee),
                "recipient_delta": 10,
            }
        ],
        events=events,
    )


def test_mempool_reservation_does_not_touch_confirmed() -> None:
    sender = b"\x01" * 32
    receiver = b"\x02" * 32
    state = _State({sender: 300_000, receiver: 0})
    tx, block_env, tx_env = _mk_tx(sender, receiver, "0x" + "cd" * 32, 10, 1)

    reserve = tx["amount"] + tx_env.gas_price
    state.reservations[tx["hash"]] = reserve

    assert state.get_balance(sender) == 300_000
    assert state.get_balance(receiver) == 0
    assert state.reservations[tx["hash"]] == 11

    result = apply_transfer(tx=tx, state=state, block_env=block_env, tx_env=tx_env)
    expected_fee = result.gas_used * tx_env.gas_price
    assert state.get_balance(sender) == 300_000 - (10 + expected_fee)
    assert state.get_balance(receiver) == 10


def test_idempotent_apply_same_tx_twice() -> None:
    sender = b"\x01" * 32
    receiver = b"\x02" * 32
    state = _State({sender: 300_000, receiver: 0})
    tx, block_env, tx_env = _mk_tx(sender, receiver, "0x" + "ef" * 32, 10, 1)

    first = apply_transfer(tx=tx, state=state, block_env=block_env, tx_env=tx_env)
    apply_transfer(tx=tx, state=state, block_env=block_env, tx_env=tx_env)

    expected_fee = first.gas_used * tx_env.gas_price
    assert state.get_balance(sender) == 300_000 - (10 + expected_fee)
    assert state.get_balance(receiver) == 10
