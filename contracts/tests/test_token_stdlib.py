# -*- coding: utf-8 -*-
"""
tests for a tiny fungible token that uses the stdlib (storage/events/hash/abi)

We build a minimal contract *inline* (to a temp file) that:
- tracks balances and allowances in storage
- supports transfer, approve, transfer_from
- implements a simple, deterministic "permit" that checks a SHA3-256 digest
  of the message (this is a test-only stand-in for PQ signature verification)

The tests exercise:
- balances after init & transfers
- allowance via approve() and transfer_from()
- permit() flow that sets allowance and increments a per-owner nonce
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict

import pytest


# ---------------------------- helpers -----------------------------------------

CONTRACT_SOURCE = r'''
# minimal token for tests — deterministic, no external deps
from stdlib.storage import get, set
from stdlib.events import emit
from stdlib.abi import require, revert
from stdlib.hash import sha3_256

# storage layout (simple ascii encodings for ints):
#   b"total"                       -> ascii(int)
#   b"bal|" + addr                 -> ascii(int)
#   b"allow|" + owner + b"|" + sp  -> ascii(int)
#   b"nonce|" + owner              -> ascii(int)

def _load_u(key: bytes) -> int:
    v = get(key)
    return int(v.decode("utf-8")) if v is not None else 0

def _store_u(key: bytes, n: int) -> None:
    set(key, str(int(n)).encode("utf-8"))

def _k_bal(addr: str) -> bytes:
    return b"bal|" + addr.encode("utf-8")

def _k_allow(owner: str, spender: str) -> bytes:
    return b"allow|" + owner.encode("utf-8") + b"|" + spender.encode("utf-8")

def _k_nonce(owner: str) -> bytes:
    return b"nonce|" + owner.encode("utf-8")

def total_supply() -> int:
    return _load_u(b"total")

def balance_of(addr: str) -> int:
    return _load_u(_k_bal(addr))

def allowance(owner: str, spender: str) -> int:
    return _load_u(_k_allow(owner, spender))

def nonces(owner: str) -> int:
    return _load_u(_k_nonce(owner))

def _mint(to: str, amount: int) -> None:
    require(amount >= 0, "bad mint")
    _store_u(b"total", total_supply() + amount)
    _store_u(_k_bal(to), balance_of(to) + amount)
    emit(b"Transfer", {"from": None, "to": to, "amount": amount})

def init(initial_owner: str, initial_supply: int) -> None:
    require(total_supply() == 0, "already inited")
    _mint(initial_owner, initial_supply)

def transfer(sender: str, to: str, amount: int) -> None:
    require(amount >= 0, "bad amount")
    sbal = balance_of(sender)
    require(sbal >= amount, "insufficient")
    _store_u(_k_bal(sender), sbal - amount)
    _store_u(_k_bal(to), balance_of(to) + amount)
    emit(b"Transfer", {"from": sender, "to": to, "amount": amount})

def approve(owner: str, spender: str, amount: int) -> None:
    require(amount >= 0, "bad amount")
    _store_u(_k_allow(owner, spender), amount)
    emit(b"Approval", {"owner": owner, "spender": spender, "amount": amount})

def transfer_from(spender: str, owner: str, to: str, amount: int) -> None:
    require(amount >= 0, "bad amount")
    akey = _k_allow(owner, spender)
    allowed = _load_u(akey)
    require(allowed >= amount, "allowance too low")
    # debit owner
    obal = balance_of(owner)
    require(obal >= amount, "insufficient")
    _store_u(_k_bal(owner), obal - amount)
    # credit to
    _store_u(_k_bal(to), balance_of(to) + amount)
    # burn allowance
    _store_u(akey, allowed - amount)
    emit(b"Transfer", {"from": owner, "to": to, "amount": amount})
    emit(b"AllowanceUsed", {"owner": owner, "spender": spender, "used": amount})

def _permit_message(owner: str, spender: str, amount: int, nonce: int, deadline: int, alg_id: str) -> bytes:
    # Deterministic test-only message (ASCII join)
    raw = "|".join([owner, spender, str(amount), str(nonce), str(deadline), alg_id]).encode("utf-8")
    return sha3_256(b"PERMIT|" + raw)

def permit(owner: str, spender: str, amount: int, deadline: int, alg_id: str, signature: bytes) -> None:
    # Test semantics: accept iff signature equals the sha3_256 of the canonical message
    now = 0  # no clock in test VM; treat 0 as "before all deadlines"
    require(now <= deadline, "deadline passed")
    nonce = nonces(owner)
    must = _permit_message(owner, spender, amount, nonce, deadline, alg_id)
    require(isinstance(signature, (bytes, bytearray)), "bad sig type")
    require(bytes(signature) == must, "bad signature")
    # Set allowance and bump nonce
    _store_u(_k_allow(owner, spender), amount)
    _store_u(_k_nonce(owner), nonce + 1)
    emit(b"Permit", {"owner": owner, "spender": spender, "amount": amount, "nonce": nonce})
'''

def _write_contract(tmp_path: Path) -> Path:
    p = tmp_path / "token_test_contract.py"
    p.write_text(CONTRACT_SOURCE, encoding="utf-8")
    return p


def _permit_signature(owner: str, spender: str, amount: int, nonce: int, deadline: int, alg_id: str) -> bytes:
    m = hashlib.sha3_256()
    raw = "|".join([owner, spender, str(amount), str(nonce), str(deadline), alg_id]).encode("utf-8")
    m.update(b"PERMIT|" + raw)
    return m.digest()


# ------------------------------- tests ----------------------------------------

def test_balances_and_transfer(tmp_path: Path, compile_contract, funded_accounts: Dict[str, Dict]):
    src = _write_contract(tmp_path)
    c = compile_contract(src)

    alice = funded_accounts["alice"]["address"]
    bob = funded_accounts["bob"]["address"]

    # init supply to Alice
    c.call("init", alice, 1_000_000)
    assert c.call("total_supply") == 1_000_000
    assert c.call("balance_of", alice) == 1_000_000
    assert c.call("balance_of", bob) == 0

    # transfer 123 from Alice -> Bob
    c.call("transfer", alice, bob, 123)
    assert c.call("balance_of", alice) == 1_000_000 - 123
    assert c.call("balance_of", bob) == 123

    # event sanity
    names = [e["name"] for e in c.events]
    assert names.count(b"Transfer") >= 2  # mint + transfer


def test_allowance_approve_and_transfer_from(tmp_path: Path, compile_contract, funded_accounts: Dict[str, Dict]):
    src = _write_contract(tmp_path)
    c = compile_contract(src)

    alice = funded_accounts["alice"]["address"]
    bob = funded_accounts["bob"]["address"]

    c.call("init", alice, 10_000)
    # approve 500 for Bob to spend from Alice
    c.call("approve", alice, bob, 500)
    assert c.call("allowance", alice, bob) == 500

    # Bob pulls 200 from Alice -> Bob
    c.call("transfer_from", bob, alice, bob, 200)
    assert c.call("balance_of", alice) == 9_800
    assert c.call("balance_of", bob) == 200
    assert c.call("allowance", alice, bob) == 300

    # Can't exceed remaining allowance
    with pytest.raises(Exception):
        c.call("transfer_from", bob, alice, bob, 400)


def test_permit_sets_allowance_and_increments_nonce(tmp_path: Path, compile_contract, funded_accounts: Dict[str, Dict]):
    src = _write_contract(tmp_path)
    c = compile_contract(src)

    owner = funded_accounts["alice"]["address"]
    spender = funded_accounts["bob"]["address"]

    c.call("init", owner, 5_000)

    # Pre-state
    assert c.call("nonces", owner) == 0
    assert c.call("allowance", owner, spender) == 0

    # Build a valid test "signature"
    amount = 777
    deadline = 999_999
    alg_id = "dilithium3"
    sig = _permit_signature(owner, spender, amount, 0, deadline, alg_id)

    # Permit should set allowance and bump nonce
    c.call("permit", owner, spender, amount, deadline, alg_id, sig)
    assert c.call("allowance", owner, spender) == amount
    assert c.call("nonces", owner) == 1

    # Spender uses allowance
    c.call("transfer_from", spender, owner, spender, 111)
    assert c.call("balance_of", owner) == 5_000 - 111
    assert c.call("balance_of", spender) == 111
    assert c.call("allowance", owner, spender) == amount - 111

    # Reusing the same signature (stale nonce) must fail
    with pytest.raises(Exception):
        c.call("permit", owner, spender, amount, deadline, alg_id, sig)

    # New signature with updated nonce works
    sig2 = _permit_signature(owner, spender, 222, 1, deadline, alg_id)
    c.call("permit", owner, spender, 222, deadline, alg_id, sig2)
    assert c.call("allowance", owner, spender) == 222
    assert c.call("nonces", owner) == 2


def test_permit_rejects_bad_sig(tmp_path: Path, compile_contract, funded_accounts: Dict[str, Dict]):
    src = _write_contract(tmp_path)
    c = compile_contract(src)

    owner = funded_accounts["alice"]["address"]
    spender = funded_accounts["bob"]["address"]

    c.call("init", owner, 1)

    bad_sig = b"\x00" * 32
    with pytest.raises(Exception):
        c.call("permit", owner, spender, 1, 42, "sphincs_shake_128s", bad_sig)
    assert c.call("allowance", owner, spender) == 0
    assert c.call("nonces", owner) == 0
