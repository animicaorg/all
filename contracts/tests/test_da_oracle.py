# -*- coding: utf-8 -*-
"""
DA Oracle — commitment update & client read tests

This test uses an inline "oracle" contract that tracks the latest Data Availability
commitment and an associated value payload (e.g., a decoded value or summary).
It verifies:
- happy path: update(commitment, value, mime) then read back via getters
- multiple updates: later updates overwrite prior state; events append
- validation: commitment length & value size guards
- client-side verification: value_hash (sha3_256) matches what the contract emitted
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

# ----------------------- inline oracle contract -----------------------

ORACLE_SRC = r'''
from stdlib.storage import get, set
from stdlib.events import emit
from stdlib.abi import require
from stdlib.hash import sha3_256

K_COMMIT = b"da:oracle:commit"
K_VALUE  = b"da:oracle:value"
K_MIME   = b"da:oracle:mime"
K_SIZE   = b"da:oracle:size"

_MAX_VALUE = 1048576  # 1 MiB sanity cap for example

def init() -> None:
    # Initialize empty state (explicit for clarity)
    set(K_COMMIT, b"")
    set(K_VALUE, b"")
    set(K_MIME, b"")
    set(K_SIZE, b"0")

def update(commitment: bytes, value: bytes, mime: bytes = b"application/octet-stream") -> None:
    """
    Store the latest DA commitment and an associated value buffer.
    - commitment: typically NMT root (32/48/64 bytes depending on scheme)
    - value: arbitrary payload (decoded from DA, oracle-specific)
    - mime: informal hint for consumers
    """
    require(isinstance(commitment, (bytes, bytearray)), "commitment must be bytes")
    require(len(commitment) in (32, 48, 64), "bad commitment length")
    require(isinstance(value, (bytes, bytearray)), "value must be bytes")
    require(len(value) <= _MAX_VALUE, "value too big")
    require(isinstance(mime, (bytes, bytearray)) and 0 < len(mime) <= 64, "bad mime")

    set(K_COMMIT, commitment)
    set(K_VALUE, value)
    set(K_MIME, mime)
    set(K_SIZE, str(len(value)).encode("ascii"))

    emit(b"OracleUpdated", {
        "commitment": commitment,
        "value_hash": sha3_256(value),
        "size": len(value),
        "mime": mime,
    })

def get_commitment() -> bytes:
    v = get(K_COMMIT)
    return v if v is not None else b""

def get_value() -> bytes:
    v = get(K_VALUE)
    return v if v is not None else b""

def get_meta() -> dict:
    mime = get(K_MIME) or b""
    size_b = get(K_SIZE) or b"0"
    try:
        size_i = int(size_b.decode("ascii"))
    except Exception:
        size_i = 0
    return {"mime": mime, "size": size_i}
'''


def _write_contract(tmp_path: Path) -> Path:
    p = tmp_path / "da_oracle_inline.py"
    p.write_text(ORACLE_SRC, encoding="utf-8")
    return p


def _sha3_256(b: bytes) -> bytes:
    return hashlib.sha3_256(b).digest()


# -------------------------------- tests --------------------------------


def test_update_then_read_back(tmp_path: Path, compile_contract):
    """
    Basic flow: init → update → read via getters; event includes correct value_hash & size.
    """
    c = compile_contract(_write_contract(tmp_path))
    c.call("init")

    # Simulate DA commitment (e.g., NMT root) and a decoded value
    commitment = bytes.fromhex("ab" * 32)  # 32 bytes
    value = b"hello, world"
    mime = b"text/plain"

    c.call("update", commitment, value, mime)

    # Getters reflect latest state
    assert c.call("get_commitment") == commitment
    assert c.call("get_value") == value
    meta = c.call("get_meta")
    assert meta["mime"] == mime
    assert meta["size"] == len(value)

    # Event emitted with correct hash and metadata
    assert len(c.events) == 1
    ev = c.events[0]
    assert ev["name"] == b"OracleUpdated"
    assert ev["args"]["commitment"] == commitment
    assert ev["args"]["size"] == len(value)
    assert ev["args"]["mime"] == mime
    assert ev["args"]["value_hash"] == _sha3_256(value)


def test_multiple_updates_overwrite_and_append_events(tmp_path: Path, compile_contract):
    """
    Second update replaces state (commitment/value/mime). Events are append-only logs.
    """
    c = compile_contract(_write_contract(tmp_path))
    c.call("init")

    c1 = bytes.fromhex("01" * 32)
    v1 = b"first"
    m1 = b"text/plain"
    c.call("update", c1, v1, m1)
    assert c.call("get_commitment") == c1
    assert c.call("get_value") == v1
    assert c.call("get_meta")["mime"] == m1
    assert len(c.events) == 1

    c2 = bytes.fromhex("02" * 32)
    v2 = b"second and newer value"
    m2 = b"text/custom"
    c.call("update", c2, v2, m2)

    # Latest wins in storage
    assert c.call("get_commitment") == c2
    assert c.call("get_value") == v2
    meta = c.call("get_meta")
    assert meta["mime"] == m2
    assert meta["size"] == len(v2)

    # Two events total; last one corresponds to second update
    assert len(c.events) == 2
    last = c.events[-1]
    assert last["name"] == b"OracleUpdated"
    assert last["args"]["commitment"] == c2
    assert last["args"]["value_hash"] == _sha3_256(v2)


@pytest.mark.parametrize("bad_len", [0, 16, 31, 33, 40, 65])
def test_commitment_length_guard(tmp_path: Path, compile_contract, bad_len: int):
    """
    Commitment must be one of the accepted lengths (32/48/64). Others revert.
    """
    c = compile_contract(_write_contract(tmp_path))
    c.call("init")

    bad_commit = b"\x00" * bad_len
    with pytest.raises(Exception):
        c.call("update", bad_commit, b"x", b"text/plain")

    # State unchanged; no events
    assert c.call("get_commitment") == b""
    assert len(c.events) == 0


def test_value_size_guard(tmp_path: Path, compile_contract):
    """
    Values exceeding the contract's cap (1 MiB) are rejected.
    """
    c = compile_contract(_write_contract(tmp_path))
    c.call("init")

    commit_ok = bytes.fromhex("cd" * 32)
    oversized = b"a" * (1048576 + 1)  # 1 MiB + 1
    with pytest.raises(Exception):
        c.call("update", commit_ok, oversized, b"application/octet-stream")

    # A smaller follow-up succeeds
    small = b"ok"
    c.call("update", commit_ok, small, b"application/octet-stream")
    assert c.call("get_value") == small
    assert len(c.events) == 1
