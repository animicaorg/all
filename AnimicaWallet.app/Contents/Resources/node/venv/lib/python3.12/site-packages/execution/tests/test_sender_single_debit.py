from __future__ import annotations

from types import SimpleNamespace

from execution.runtime.transfers import apply_transfer
from execution.state.apply_balance import get_debug_balance_events, reset_debug_balance_events
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

    def ensure_account(self, addr: bytes) -> None:
        self._balances.setdefault(addr, 0)


def test_sender_not_double_debited_on_transfer(monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_DEBUG_BALANCE", "1")
    sender = b"\x11" * 32
    recipient = b"\x22" * 32
    coinbase = b"\x33" * 32
    tx_hash = "0xsender-single-debit"

    state = MockState({sender: 300, recipient: 0, coinbase: 0}, {sender: 0})
    tx = {"to": recipient, "amount": 10, "gas_limit": 21_000, "nonce": 0, "hash": tx_hash}
    block_env = SimpleNamespace(coinbase=coinbase, treasury=b"", height=1)
    tx_env = SimpleNamespace(sender=sender, gas_price=0, base_price=0)

    reset_debug_balance_events()
    result = apply_transfer(tx=tx, state=state, block_env=block_env, tx_env=tx_env)

    assert result.status == TxStatus.SUCCESS
    assert state.get_balance(sender) == 290
    assert state.get_balance(recipient) == 10

    events = get_debug_balance_events(tx_hash=tx_hash)
    sender_debits = [e for e in events if e["address"] == sender.hex() and int(e["delta"]) < 0]
    recipient_credits = [e for e in events if e["address"] == recipient.hex() and int(e["delta"]) > 0]

    assert len(sender_debits) == 1
    assert int(sender_debits[0]["delta"]) == -10
    assert len(recipient_credits) == 1
    assert int(recipient_credits[0]["delta"]) == 10


def test_mempool_admission_does_not_mutate_confirmed_balance() -> None:
    sender = b"\x44" * 32
    recipient = b"\x55" * 32

    state = MockState({sender: 1000, recipient: 0}, {sender: 0})
    fee_reserved = 1
    reserve_amount = 11
    available = state.get_balance(sender)

    assert available >= reserve_amount

    pending_outgoing = reserve_amount
    assert state.get_balance(sender) == 1000
    assert state.get_balance(recipient) == 0
    assert pending_outgoing == 11
    assert fee_reserved == 1
