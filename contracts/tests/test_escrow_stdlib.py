# -*- coding: utf-8 -*-
"""
Escrow stdlib-style contract tests: deposit → dispute → refund, plus happy release path.

This file compiles a tiny escrow contract inline for determinism and runs a few
behavioral tests against the local VM test harness provided by conftest.py.

Contract rules (kept intentionally simple for tests):
- init(depositor, beneficiary)
- deposit(sender, amount): only depositor can deposit; increases locked amount
- dispute(sender): only depositor; flips a dispute flag; prevents release()
- release(sender): only depositor; only if not disputed; moves locked -> paid_to_beneficiary
- refund(sender): only depositor; only if disputed; moves locked -> refunded_to_depositor
- state(): "open" | "disputed" | "released" | "refunded"
- locked(), paid_to_beneficiary(), refunded_to_depositor(): integer views

Events:
- Deposit{depositor, amount}
- Dispute{depositor}
- Release{to, amount}
- Refund{to, amount}
"""
from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------- inline escrow contract ---------------------------

CONTRACT_SOURCE = r"""
from stdlib.storage import get, set
from stdlib.events import emit
from stdlib.abi import require, revert

def _load_u(key: bytes) -> int:
    v = get(key)
    return int(v.decode("utf-8")) if v is not None else 0

def _store_u(key: bytes, n: int) -> None:
    set(key, str(int(n)).encode("utf-8"))

def _load_b(key: bytes) -> bool:
    v = get(key)
    if v is None:
        return False
    s = v.decode("utf-8")
    return True if s == "1" else False

def _store_b(key: bytes, b: bool) -> None:
    set(key, b"1" if b else b"0")

# keys
K_DEP = b"depositor"
K_BEN = b"beneficiary"
K_LOCK = b"locked"
K_PAID = b"paid_to_beneficiary"
K_REF = b"refunded_to_depositor"
K_DISP = b"disputed"
K_SEALED = b"sealed"  # any terminal state reached

def _load_s(key: bytes) -> str | None:
    v = get(key)
    return v.decode("utf-8") if v is not None else None

def _store_s(key: bytes, s: str) -> None:
    set(key, s.encode("utf-8"))

def init(depositor: str, beneficiary: str) -> None:
    require(_load_s(K_DEP) is None and _load_s(K_BEN) is None, "already inited")
    _store_s(K_DEP, depositor)
    _store_s(K_BEN, beneficiary)
    _store_u(K_LOCK, 0)
    _store_u(K_PAID, 0)
    _store_u(K_REF, 0)
    _store_b(K_DISP, False)
    _store_b(K_SEALED, False)

def depositor() -> str:
    return _load_s(K_DEP) or ""

def beneficiary() -> str:
    return _load_s(K_BEN) or ""

def locked() -> int:
    return _load_u(K_LOCK)

def paid_to_beneficiary() -> int:
    return _load_u(K_PAID)

def refunded_to_depositor() -> int:
    return _load_u(K_REF)

def disputed() -> bool:
    return _load_b(K_DISP)

def sealed() -> bool:
    return _load_b(K_SEALED)

def state() -> str:
    if sealed():
        if paid_to_beneficiary() > 0:
            return "released"
        if refunded_to_depositor() > 0:
            return "refunded"
    return "disputed" if disputed() else "open"

def _only_depositor(sender: str) -> None:
    require(sender == depositor(), "only depositor")

def deposit(sender: str, amount: int) -> None:
    _only_depositor(sender)
    require(not sealed(), "sealed")
    require(amount >= 0, "bad amount")
    _store_u(K_LOCK, locked() + amount)
    emit(b"Deposit", {"depositor": sender, "amount": amount})

def dispute(sender: str) -> None:
    _only_depositor(sender)
    require(not sealed(), "sealed")
    require(not disputed(), "already disputed")
    _store_b(K_DISP, True)
    emit(b"Dispute", {"depositor": sender})

def release(sender: str) -> None:
    _only_depositor(sender)
    require(not sealed(), "sealed")
    require(not disputed(), "disputed")
    amt = locked()
    require(amt > 0, "nothing locked")
    _store_u(K_LOCK, 0)
    _store_u(K_PAID, paid_to_beneficiary() + amt)
    _store_b(K_SEALED, True)
    emit(b"Release", {"to": beneficiary(), "amount": amt})

def refund(sender: str) -> None:
    _only_depositor(sender)
    require(not sealed(), "sealed")
    require(disputed(), "not disputed")
    amt = locked()
    require(amt > 0, "nothing locked")
    _store_u(K_LOCK, 0)
    _store_u(K_REF, refunded_to_depositor() + amt)
    _store_b(K_SEALED, True)
    emit(b"Refund", {"to": depositor(), "amount": amt})
"""


def _write_contract(tmp_path: Path) -> Path:
    p = tmp_path / "escrow_test_contract.py"
    p.write_text(CONTRACT_SOURCE, encoding="utf-8")
    return p


# ---------------------------------- tests -------------------------------------


def test_deposit_dispute_refund(tmp_path: Path, compile_contract, funded_accounts):
    src = _write_contract(tmp_path)
    c = compile_contract(src)

    dep = funded_accounts["alice"]["address"]
    ben = funded_accounts["bob"]["address"]

    c.call("init", dep, ben)
    assert c.call("state") == "open"
    assert c.call("depositor") == dep
    assert c.call("beneficiary") == ben
    assert c.call("locked") == 0

    # deposit 1_000 units
    c.call("deposit", dep, 1_000)
    assert c.call("locked") == 1_000

    # raise dispute
    c.call("dispute", dep)
    assert c.call("disputed") is True
    assert c.call("state") == "disputed"

    # cannot release while disputed
    with pytest.raises(Exception):
        c.call("release", dep)

    # refund back to depositor
    c.call("refund", dep)
    assert c.call("locked") == 0
    assert c.call("refunded_to_depositor") == 1_000
    assert c.call("paid_to_beneficiary") == 0
    assert c.call("state") == "refunded"

    # check event sequence
    names = [e["name"] for e in c.events]
    assert names == [b"Deposit", b"Dispute", b"Refund"]


def test_deposit_then_release_happy_path(
    tmp_path: Path, compile_contract, funded_accounts
):
    src = _write_contract(tmp_path)
    c = compile_contract(src)

    dep = funded_accounts["alice"]["address"]
    ben = funded_accounts["bob"]["address"]

    c.call("init", dep, ben)
    c.call("deposit", dep, 250)
    assert c.call("locked") == 250
    assert c.call("state") == "open"

    # release (no dispute)
    c.call("release", dep)
    assert c.call("locked") == 0
    assert c.call("paid_to_beneficiary") == 250
    assert c.call("refunded_to_depositor") == 0
    assert c.call("state") == "released"

    # idempotence: further actions sealed
    with pytest.raises(Exception):
        c.call("deposit", dep, 1)
    with pytest.raises(Exception):
        c.call("refund", dep)
    with pytest.raises(Exception):
        c.call("release", dep)


def test_unauthorized_and_edge_cases(tmp_path: Path, compile_contract, funded_accounts):
    src = _write_contract(tmp_path)
    c = compile_contract(src)

    dep = funded_accounts["alice"]["address"]
    ben = funded_accounts["bob"]["address"]
    eve = funded_accounts["carol"]["address"]

    c.call("init", dep, ben)

    # non-depositor cannot deposit, dispute, release, refund
    with pytest.raises(Exception):
        c.call("deposit", eve, 10)
    with pytest.raises(Exception):
        c.call("dispute", eve)
    with pytest.raises(Exception):
        c.call("release", eve)
    with pytest.raises(Exception):
        c.call("refund", eve)

    # cannot dispute twice
    c.call("deposit", dep, 5)
    c.call("dispute", dep)
    with pytest.raises(Exception):
        c.call("dispute", dep)

    # cannot refund if nothing locked
    # (seal by refunding, then try refund again)
    c.call("refund", dep)
    with pytest.raises(Exception):
        c.call("refund", dep)
