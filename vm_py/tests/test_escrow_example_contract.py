from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import pytest

from vm_py.errors import VmError
from vm_py.examples.escrow import contract as escrow


@dataclass
class StubTreasury:
    """Simple in-memory treasury used to drive the escrow state machine in tests."""

    _balance: int = 0
    transfers: List[Tuple[bytes, int]] = None

    def __post_init__(self) -> None:
        if self.transfers is None:
            self.transfers = []

    # API that the contract expects -----------------------------------------

    def balance(self) -> int:
        return int(self._balance)

    def transfer(self, to: bytes, amount: int) -> None:
        amount = int(amount)
        if amount < 0:
            raise ValueError("negative transfer in StubTreasury")
        if self._balance < amount:
            # This should never happen if the contract's own checks are correct.
            raise ValueError("insufficient stub treasury balance")
        self._balance -= amount
        self.transfers.append((bytes(to), amount))

    # Helpers for tests ------------------------------------------------------

    def set_balance(self, value: int) -> None:
        self._balance = int(value)

    def reset(self) -> None:
        self._balance = 0
        self.transfers.clear()


def _reset_storage() -> None:
    """Reset contract storage keys to a clean state."""
    for key in (
        escrow._KEY_STATE,
        escrow._KEY_DEPOSITOR,
        escrow._KEY_BENEFICIARY,
        escrow._KEY_AMOUNT,
    ):
        # The helpers interpret empty bytes as "unset" / zero.
        escrow.storage.set(key, b"")


@pytest.fixture
def escrow_env(monkeypatch):
    """
    Provide a fresh escrow + stubbed dependencies per test.

    We patch:
      * escrow.treasury.balance/transfer   → StubTreasury
      * escrow.events.emit                → in-memory event log
    """

    stub_treasury = StubTreasury()
    events: List[Dict[str, Any]] = []

    def emit(name: bytes, args: Dict[bytes, Any]) -> None:
        events.append({"name": name, "args": args})

    # Patch dependencies used inside the contract
    monkeypatch.setattr(escrow, "treasury", stub_treasury)
    monkeypatch.setattr(escrow.events, "emit", emit)

    # Fresh storage for every test
    _reset_storage()

    yield stub_treasury, events

    # Best-effort cleanup
    stub_treasury.reset()
    _reset_storage()
    events.clear()


# ---------------------------------------------------------------------------
# Happy path: full lifecycle
# ---------------------------------------------------------------------------


def test_setup_initializes_state_and_emits_setup(escrow_env):
    treasury, events = escrow_env
    amount = 1_000
    depositor = b"alice"
    beneficiary = b"bob"

    escrow.setup(depositor, beneficiary, amount)

    # Internal state helpers
    assert escrow._get_depositor() == depositor
    assert escrow._get_beneficiary() == beneficiary
    assert escrow._get_amount() == amount
    assert escrow.status() == escrow.STATE_INIT

    # Event: Escrow.Setup
    assert events, "expected at least one event"
    first = events[0]
    assert first["name"] == b"Escrow.Setup"
    assert first["args"][b"amount"] == amount


def test_setup_cannot_be_called_twice(escrow_env):
    treasury, events = escrow_env
    escrow.setup(b"a", b"b", 10)

    with pytest.raises(VmError) as exc:
        escrow.setup(b"a2", b"b2", 20)

    # The exact message is part of the contract's public surface.
    assert "escrow: already inited" in str(exc.value)


def test_ready_reflects_treasury_balance(escrow_env):
    treasury, events = escrow_env
    amount = 500
    escrow.setup(b"a", b"b", amount)

    treasury.set_balance(amount - 1)
    assert escrow.ready() is False

    treasury.set_balance(amount)
    assert escrow.ready() is True


def test_deposit_check_marks_funded_and_emits_event(escrow_env):
    treasury, events = escrow_env
    amount = 2_500
    escrow.setup(b"a", b"b", amount)

    treasury.set_balance(amount)
    escrow.deposit_check()

    assert escrow.status() == escrow.STATE_FUNDED

    # We should see a Funded event after the initial Setup
    assert len(events) >= 2
    last = events[-1]
    assert last["name"] == b"Escrow.Funded"
    assert last["args"][b"amount"] == amount


def test_deposit_check_does_not_fund_if_balance_insufficient(escrow_env):
    treasury, events = escrow_env
    amount = 7_000
    escrow.setup(b"a", b"b", amount)

    treasury.set_balance(amount - 1)
    escrow.deposit_check()

    # Still INIT, and only the Setup event should exist
    assert escrow.status() == escrow.STATE_INIT
    # No Escrow.Funded event
    assert all(e["name"] != b"Escrow.Funded" for e in events)


def test_full_release_happy_path(escrow_env):
    treasury, events = escrow_env
    amount = 42_000
    depositor = b"alice"
    beneficiary = b"bob"

    escrow.setup(depositor, beneficiary, amount)
    treasury.set_balance(amount)
    escrow.deposit_check()

    # Preconditions
    assert escrow.status() == escrow.STATE_FUNDED
    assert treasury.balance() == amount

    escrow.release()

    assert escrow.status() == escrow.STATE_RELEASED
    assert treasury.balance() == 0

    # Exactly one outgoing transfer to the beneficiary
    assert treasury.transfers == [(beneficiary, amount)]

    # Last event should be Escrow.Released with the same amount/to
    assert events, "expected events to be recorded"
    last = events[-1]
    assert last["name"] == b"Escrow.Released"
    assert last["args"][b"amount"] == amount
    assert last["args"][b"to"] == beneficiary


def test_refund_full_happy_path(escrow_env):
    treasury, events = escrow_env
    amount = 5_000
    depositor = b"depositor"
    beneficiary = b"beneficiary"

    escrow.setup(depositor, beneficiary, amount)
    treasury.set_balance(amount)
    escrow.deposit_check()

    escrow.refund()

    assert escrow.status() == escrow.STATE_REFUNDED
    # All funds returned to depositor
    assert treasury.transfers == [(depositor, amount)]
    assert treasury.balance() == 0

    last = events[-1]
    assert last["name"] == b"Escrow.Refunded"
    assert last["args"][b"amount"] == amount
    assert last["args"][b"to"] == depositor


def test_refund_partial_when_balance_less_than_amount(escrow_env):
    treasury, events = escrow_env
    amount = 10_000
    partial = 3_000
    depositor = b"d"

    escrow.setup(depositor, b"b", amount)

    # No need to be FUNDED; refund is allowed from INIT as long as not RELEASED.
    treasury.set_balance(partial)

    escrow.refund()

    assert escrow.status() == escrow.STATE_REFUNDED
    assert treasury.transfers == [(depositor, partial)]
    assert treasury.balance() == 0

    last = events[-1]
    assert last["name"] == b"Escrow.Refunded"
    assert last["args"][b"amount"] == partial
    assert last["args"][b"to"] == depositor


def test_refund_with_zero_balance_marks_refunded_without_transfer(escrow_env):
    treasury, events = escrow_env
    depositor = b"d"

    escrow.setup(depositor, b"b", 1234)
    treasury.set_balance(0)

    escrow.refund()

    assert escrow.status() == escrow.STATE_REFUNDED
    assert treasury.transfers == []

    last = events[-1]
    assert last["name"] == b"Escrow.Refunded"
    assert last["args"][b"amount"] == 0
    assert last["args"][b"to"] == depositor


# ---------------------------------------------------------------------------
# Failure paths / guards
# ---------------------------------------------------------------------------


def test_deposit_check_requires_configured_amount(escrow_env):
    treasury, events = escrow_env

    # We intentionally skip setup() so amount == 0.
    with pytest.raises(VmError) as exc:
        escrow.deposit_check()

    assert "escrow: not configured" in str(exc.value)


def test_deposit_check_after_final_state_raises(escrow_env):
    treasury, events = escrow_env
    amount = 1_000

    escrow.setup(b"a", b"b", amount)
    treasury.set_balance(amount)
    escrow.deposit_check()
    escrow.release()  # move to RELEASED

    with pytest.raises(VmError) as exc:
        escrow.deposit_check()

    assert "escrow: immutable after finish" in str(exc.value)


def test_release_before_funding_rejected(escrow_env):
    treasury, events = escrow_env
    amount = 999

    escrow.setup(b"a", b"b", amount)
    treasury.set_balance(amount)

    with pytest.raises(VmError) as exc:
        escrow.release()

    assert "escrow: not funded" in str(exc.value)


def test_release_with_insufficient_treasury_balance_rejected(escrow_env):
    treasury, events = escrow_env
    amount = 4_000

    escrow.setup(b"a", b"b", amount)
    treasury.set_balance(amount)
    escrow.deposit_check()

    # Now simulate funds being drained externally before release.
    treasury.set_balance(amount - 1)

    with pytest.raises(VmError) as exc:
        escrow.release()

    assert "escrow: insufficient funds" in str(exc.value)


def test_refund_after_release_rejected(escrow_env):
    treasury, events = escrow_env
    amount = 2_000

    escrow.setup(b"a", b"b", amount)
    treasury.set_balance(amount)
    escrow.deposit_check()
    escrow.release()

    with pytest.raises(VmError) as exc:
        escrow.refund()

    assert "escrow: already released" in str(exc.value)
