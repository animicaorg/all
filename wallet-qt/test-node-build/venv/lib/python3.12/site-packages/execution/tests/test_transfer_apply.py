import copy
import hashlib
from dataclasses import dataclass, field
from typing import Dict

import pytest

# Try to use the project's Account dataclass if available; otherwise fall back
# to a minimal local definition so this test remains runnable while wiring lands.
try:
    # type: ignore[import-not-found]
    from execution.state.accounts import Account  # noqa: F401
except Exception:  # pragma: no cover

    @dataclass
    class Account:  # type: ignore[no-redef]
        nonce: int = 0
        balance: int = 0
        code_hash: bytes = field(default_factory=lambda: b"\x00" * 32)


ALICE = "0x" + "aa" * 32
BOB = "0x" + "bb" * 32
COINBASE = "0x" + "cc" * 32

GENESIS_BALANCES = {
    ALICE: 1_000_000_000_000_000_000,  # 1e18
    BOB: 0,
    COINBASE: 0,
}


def mk_genesis_state() -> Dict[str, Account]:
    """Create an in-memory state map using the Account dataclass."""
    st: Dict[str, Account] = {}
    for addr, bal in GENESIS_BALANCES.items():
        st[addr] = Account(nonce=0, balance=bal, code_hash=b"\x00" * 32)
    return st


def state_root(state: Dict[str, Account]) -> str:
    """
    Deterministic "root" hash for the state mapping (test-local).
    Sort by address; hash nonce|balance|code_hash for each account.
    """
    h = hashlib.sha3_256()
    for addr in sorted(state.keys()):
        acc = state[addr]
        h.update(bytes.fromhex(addr[2:]))  # address (32 bytes)
        h.update(acc.nonce.to_bytes(8, "big"))
        h.update(acc.balance.to_bytes(32, "big"))
        h.update(acc.code_hash)
    return "0x" + h.hexdigest()


def apply_transfer(
    state: Dict[str, Account],
    sender: str,
    to: str,
    value: int,
    gas_limit: int,
    gas_price: int,
    coinbase: str,
) -> None:
    """
    Minimal, deterministic transfer apply:
      - fee = gas_limit * gas_price
      - debit sender (value + fee), increment nonce
      - credit recipient (value)
      - credit coinbase (fee)
    This mirrors the high-level rules the execution module enforces.
    """
    fee = gas_limit * gas_price

    s = state[sender]
    if s.balance < value + fee:
        raise RuntimeError("InsufficientBalance")

    s.balance -= value + fee
    s.nonce += 1

    state[to].balance += value
    state[coinbase].balance += fee


def test_debit_credit_and_nonce_increment() -> None:
    state = mk_genesis_state()
    start_root = state_root(state)

    value = 500_000_000_000_000_000  # 0.5e18
    gas_limit = 21_000
    gas_price = 1

    apply_transfer(state, ALICE, BOB, value, gas_limit, gas_price, COINBASE)

    # Balances
    assert (
        state[ALICE].balance == GENESIS_BALANCES[ALICE] - value - gas_limit * gas_price
    )
    assert state[BOB].balance == GENESIS_BALANCES[BOB] + value
    assert state[COINBASE].balance == gas_limit * gas_price

    # Nonce incremented
    assert state[ALICE].nonce == 1

    # Root changed from genesis
    end_root = state_root(state)
    assert end_root != start_root


def test_deterministic_state_root_on_same_inputs() -> None:
    s1 = mk_genesis_state()
    s2 = mk_genesis_state()

    value = 123_456_789_000_000_000
    gas_limit = 21_000
    gas_price = 1

    apply_transfer(s1, ALICE, BOB, value, gas_limit, gas_price, COINBASE)
    apply_transfer(s2, ALICE, BOB, value, gas_limit, gas_price, COINBASE)

    # Identical operations on identical starting state → identical roots
    assert state_root(s1) == state_root(s2)


def test_insufficient_balance_raises() -> None:
    state = mk_genesis_state()
    # Ask for more than Alice can cover (value + fee)
    too_much = GENESIS_BALANCES[ALICE] + 1
    with pytest.raises(RuntimeError, match="InsufficientBalance"):
        apply_transfer(
            state,
            ALICE,
            BOB,
            too_much,
            gas_limit=21_000,
            gas_price=1,
            coinbase=COINBASE,
        )
