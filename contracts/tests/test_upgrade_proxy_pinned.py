# -*- coding: utf-8 -*-
"""
Upgrade Proxy (code-hash pinned) — tests

We exercise a minimal upgrade proxy that *pins* an implementation code hash.
Setting the implementation requires presenting a code hash that exactly matches
the pinned value. Mismatches are rejected. This checks the enforcement logic
only (no cross-contract delegatecall needed in this VM milestone).

Contract surface (inline for test determinism):
- init(pin_hash: bytes) -> None
- get_pin() -> bytes
- get_impl() -> bytes
- set_impl(addr: bytes, code_hash: bytes) -> None  # requires code_hash == pin_hash

Signals:
- Emits b"ImplSet" with {"addr": ..., "code_hash": ...} on success
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


# ----------------------- inline proxy contract -----------------------

PROXY_SRC = r'''
from stdlib.storage import get, set
from stdlib.events import emit
from stdlib.abi import require

K_PIN  = b"proxy:pin"
K_IMPL = b"proxy:impl"

def init(pin_hash: bytes) -> None:
    require(isinstance(pin_hash, (bytes, bytearray)) and len(pin_hash) == 32, "pin must be 32 bytes")
    set(K_PIN, pin_hash)
    set(K_IMPL, b"")

def get_pin() -> bytes:
    v = get(K_PIN)
    return v if v is not None else b""

def get_impl() -> bytes:
    v = get(K_IMPL)
    return v if v is not None else b""

def set_impl(addr: bytes, code_hash: bytes) -> None:
    """
    Accept setting the implementation address *only* if code_hash matches the pinned hash.
    """
    require(isinstance(addr, (bytes, bytearray)) and 1 <= len(addr) <= 64, "bad addr")
    require(isinstance(code_hash, (bytes, bytearray)) and len(code_hash) == 32, "bad code hash")

    pin = get(K_PIN)
    require(pin is not None and len(pin) == 32, "pin not set")
    require(bytes(code_hash) == bytes(pin), "code hash mismatch")

    set(K_IMPL, addr)
    emit(b"ImplSet", {"addr": addr, "code_hash": code_hash})
'''


def _write_contract(tmp_path: Path) -> Path:
    p = tmp_path / "proxy_pinned_inline.py"
    p.write_text(PROXY_SRC, encoding="utf-8")
    return p


def _sha3_256(data: bytes) -> bytes:
    return hashlib.sha3_256(data).digest()


# -------------------------------- tests --------------------------------

def test_set_impl_requires_matching_pin(tmp_path: Path, compile_contract):
    """
    With the correct code hash for the pinned value, set_impl succeeds and stores the address.
    """
    c = compile_contract(_write_contract(tmp_path))

    impl_src_v1 = b"impl_v1_unique_bytes"
    pin = _sha3_256(impl_src_v1)
    c.call("init", pin)

    addr_v1 = b"\x11" * 20
    # happy path
    c.call("set_impl", addr_v1, pin)

    assert c.call("get_impl") == addr_v1
    assert c.call("get_pin") == pin

    # event surfaced
    assert len(c.events) == 1
    ev = c.events[0]
    assert ev["name"] == b"ImplSet"
    assert ev["args"]["addr"] == addr_v1
    assert ev["args"]["code_hash"] == pin


def test_mismatched_code_hash_is_rejected(tmp_path: Path, compile_contract):
    """
    If the presented code hash doesn't match the pinned one, the call reverts.
    """
    c = compile_contract(_write_contract(tmp_path))

    impl_src_v1 = b"impl_v1_unique_bytes"
    impl_src_v2 = b"impl_v2_different_bytes"
    pin_v1 = _sha3_256(impl_src_v1)
    bad_v2 = _sha3_256(impl_src_v2)

    c.call("init", pin_v1)
    addr_attempt = b"\x22" * 20

    with pytest.raises(Exception):
        c.call("set_impl", addr_attempt, bad_v2)

    # nothing changed
    assert c.call("get_impl") == b""
    assert c.call("get_pin") == pin_v1
    assert len(c.events) == 0


def test_idempotent_set_with_same_hash(tmp_path: Path, compile_contract):
    """
    Setting the same implementation again with the same (correct) hash keeps state consistent
    and appends another event (append-only log).
    """
    c = compile_contract(_write_contract(tmp_path))

    impl_src_v1 = b"impl_v1_unique_bytes"
    pin = _sha3_256(impl_src_v1)
    c.call("init", pin)

    addr = b"\x33" * 20
    c.call("set_impl", addr, pin)
    first_events = len(c.events)

    # Repeat with same inputs — allowed, should not violate invariants.
    c.call("set_impl", addr, pin)
    assert c.call("get_impl") == addr
    assert c.call("get_pin") == pin
    assert len(c.events) == first_events + 1


def test_wrong_hash_then_right_hash_sequence(tmp_path: Path, compile_contract):
    """
    A failed attempt with a wrong hash leaves state unchanged; a subsequent correct attempt succeeds.
    """
    c = compile_contract(_write_contract(tmp_path))

    impl_src_v1 = b"impl_v1_unique_bytes"
    impl_src_wrong = b"totally_wrong_impl"
    pin = _sha3_256(impl_src_v1)
    wrong = _sha3_256(impl_src_wrong)

    c.call("init", pin)

    addr = b"\x44" * 20

    # First: wrong hash → revert
    with pytest.raises(Exception):
        c.call("set_impl", addr, wrong)

    assert c.call("get_impl") == b""
    assert len(c.events) == 0

    # Second: correct hash → success
    c.call("set_impl", addr, pin)
    assert c.call("get_impl") == addr
    assert len(c.events) == 1
    assert c.events[0]["name"] == b"ImplSet"
