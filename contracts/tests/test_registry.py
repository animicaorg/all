# -*- coding: utf-8 -*-
"""
Name Registry contract tests
- Basic set/get round-trips
- Reverse mapping address → name
- Overwrite updates both forward & reverse maps
- Input validation (reject empty keys)
These tests compile a tiny inline registry contract and exercise it via the
local VM fixture provided in contracts/tests/conftest.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ----------------------- inline registry contract (deterministic) -----------------------

CONTRACT_SRC = r'''
from stdlib.storage import get, set
from stdlib.events import emit
from stdlib.abi import require

# Keys are namespaced so they won't collide with other storage in a larger app.
P_NAME = b"n:"
P_ADDR = b"a:"

def _k_name(name: bytes) -> bytes:
    require(isinstance(name, (bytes, bytearray)) and len(name) > 0 and len(name) <= 64, "bad name")
    return P_NAME + bytes(name)

def _k_addr(addr: bytes) -> bytes:
    require(isinstance(addr, (bytes, bytearray)) and len(addr) > 0 and len(addr) <= 64, "bad addr")
    return P_ADDR + bytes(addr)

def init() -> None:
    # nothing to initialize; storage starts empty
    pass

def set_name(name: bytes, addr: bytes) -> None:
    """
    Set forward (name→addr) and reverse (addr→name) records.
    If the name previously pointed to a different address, we null out the old reverse link
    by writing an empty value under that address key (readers treat empty as "unset").
    """
    kf = _k_name(name)
    old = get(kf)
    set(kf, addr)

    # Clear reverse for old address (if any)
    if old is not None and len(old) > 0:
        set(_k_addr(old), b"")

    # Write reverse mapping for new address
    set(_k_addr(addr), name)

    emit(b"NameSet", {"name": name, "addr": addr})

def get_addr(name: bytes) -> bytes:
    v = get(_k_name(name))
    return v if v is not None else b""

def get_name(addr: bytes) -> bytes:
    v = get(_k_addr(addr))
    return v if v is not None else b""
'''


def _write_contract(tmp_path: Path) -> Path:
    p = tmp_path / "name_registry_inline.py"
    p.write_text(CONTRACT_SRC, encoding="utf-8")
    return p


# ------------------------------------------- tests -------------------------------------------


def test_set_get_roundtrip(tmp_path: Path, compile_contract):
    c = compile_contract(_write_contract(tmp_path))
    c.call("init")

    name = b"example.anim"
    addr = b"\x01" * 32  # treat as opaque address bytes

    # Initially unset
    assert c.call("get_addr", name) == b""
    assert c.call("get_name", addr) == b""

    # Set
    c.call("set_name", name, addr)

    # Forward & reverse reads return what we set
    assert c.call("get_addr", name) == addr
    assert c.call("get_name", addr) == name

    # Event surfaced
    assert len(c.events) == 1
    assert c.events[0]["name"] == b"NameSet"
    assert c.events[0]["args"]["name"] == name
    assert c.events[0]["args"]["addr"] == addr


def test_overwrite_updates_both_maps(tmp_path: Path, compile_contract):
    c = compile_contract(_write_contract(tmp_path))
    c.call("init")

    name = b"service.api"
    addr1 = b"\xaa" * 32
    addr2 = b"\xbb" * 32

    # First set
    c.call("set_name", name, addr1)
    assert c.call("get_addr", name) == addr1
    assert c.call("get_name", addr1) == name

    # Overwrite to new address
    c.call("set_name", name, addr2)

    # Forward points to new, reverse new points to name
    assert c.call("get_addr", name) == addr2
    assert c.call("get_name", addr2) == name

    # Old reverse should be cleared (reader sees empty)
    assert c.call("get_name", addr1) == b""

    # We should have two NameSet events (append-only log)
    assert [e["name"] for e in c.events] == [b"NameSet", b"NameSet"]


@pytest.mark.parametrize(
    "bad_name,bad_addr,expect_which",
    [
        (b"", b"\x01" * 32, "name"),
        (b"x" * 65, b"\x01" * 32, "name"),  # too long
        (b"ok", b"", "addr"),
        (b"ok", b"y" * 65, "addr"),  # too long
    ],
)
def test_input_validation(
    tmp_path: Path, compile_contract, bad_name, bad_addr, expect_which
):
    c = compile_contract(_write_contract(tmp_path))
    c.call("init")

    with pytest.raises(Exception):
        c.call("set_name", bad_name, bad_addr)


def test_multiple_names_can_point_to_distinct_addrs(tmp_path: Path, compile_contract):
    c = compile_contract(_write_contract(tmp_path))
    c.call("init")

    n1, a1 = b"a.anim", b"\x10" * 20
    n2, a2 = b"b.anim", b"\x20" * 20
    c.call("set_name", n1, a1)
    c.call("set_name", n2, a2)

    assert c.call("get_addr", n1) == a1
    assert c.call("get_addr", n2) == a2
    assert c.call("get_name", a1) == n1
    assert c.call("get_name", a2) == n2


def test_repeated_set_same_value_is_idempotent(tmp_path: Path, compile_contract):
    c = compile_contract(_write_contract(tmp_path))
    c.call("init")

    name, addr = b"idempotent.anim", b"\x33" * 32
    c.call("set_name", name, addr)
    first_events = len(c.events)

    # Setting the exact same value again should leave lookups identical
    c.call("set_name", name, addr)

    assert c.call("get_addr", name) == addr
    assert c.call("get_name", addr) == name
    # We still emit a second event (append-only log), but state remains consistent
    assert len(c.events) == first_events + 1
