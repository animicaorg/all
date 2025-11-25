# -*- coding: utf-8 -*-
"""
Multisig — threshold & replay protection tests.

We embed a tiny, deterministic multisig contract tailored for property-style tests:
- 3 fixed owners (O1/O2/O3) set at init, adjustable threshold ∈ {1..3}
- Stateless approvals are provided as a blob of concatenated 32-byte owner ids
- Execution marks a (subject, nonce) tuple as "used" to prevent replay
- Uniqueness, ownership, and threshold checks are strictly enforced

These tests exercise:
  • happy path (2-of-3, unique owners) → executed, nonce consumed
  • replay protection (same nonce) → revert
  • duplicate approver in the set → revert
  • non-owner approver present → revert
  • too few approvals (< threshold) → revert
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest


# ----------------------- Inline minimal multisig -----------------------

MULTISIG_SRC = r'''
from stdlib.storage import get, set
from stdlib.events import emit
from stdlib.abi import require
from stdlib.hash import sha3_256

# Fixed owners for this test-only multisig (32-byte ids)
O1 = b"\x11" * 32
O2 = b"\x22" * 32
O3 = b"\x33" * 32

K_THRESH = b"ms:thresh"
K_NONCE_USED = b"ms:nonce:"  # suffixed with big-endian decimal nonce as ascii

def _is_owner(addr: bytes) -> bool:
    return addr in (O1, O2, O3)

def _parse_approvals(blob: bytes, n: int) -> list:
    require(isinstance(blob, (bytes, bytearray)), "approvals blob must be bytes")
    require(n >= 0, "n must be non-negative")
    require(len(blob) == 32 * n, "bad approvals blob length")
    # return list of 32-byte chunks
    out = []
    off = 0
    for _ in range(n):
        out.append(blob[off:off+32])
        off += 32
    return out

def _nonce_key(nonce: int) -> bytes:
    require(isinstance(nonce, int) and nonce >= 0, "nonce must be >= 0")
    return K_NONCE_USED + str(nonce).encode("ascii")

def init(threshold: int = 2) -> None:
    require(1 <= threshold <= 3, "threshold out of range")
    set(K_THRESH, str(threshold).encode("ascii"))
    # No need to persist owners since fixed for this test-only contract

def get_threshold() -> int:
    tb = get(K_THRESH) or b"0"
    try:
        return int(tb.decode("ascii"))
    except Exception:
        return 0

def used(nonce: int) -> bool:
    return get(_nonce_key(nonce)) == b"1"

def execute(subject: bytes, nonce: int, approvals_blob: bytes, n: int) -> None:
    """
    Execute an action identified by (subject, nonce) if at least 'threshold'
    distinct owners approve. Prevent replay of the same (subject, nonce).
    For this test stub, 'subject' is arbitrary bytes (e.g., encoded target+data).
    """
    require(isinstance(subject, (bytes, bytearray)) and len(subject) > 0, "bad subject")
    require(not used(nonce), "nonce already used (replay)")

    threshold = get_threshold()
    require(threshold >= 1, "threshold not initialized")

    approvers = _parse_approvals(approvals_blob, n)

    # Ownership & uniqueness
    seen = set()
    for a in approvers:
        require(_is_owner(a), "non-owner in approvals")
        require(a not in seen, "duplicate approver")
        seen.add(a)

    require(len(seen) >= threshold, "insufficient approvals")

    # Mark nonce used first (commit), then emit the log (effects visible)
    set(_nonce_key(nonce), b"1")

    # The "action hash" is included only as diagnostics (not needed for checks here)
    action_hash = sha3_256(subject + str(nonce).encode("ascii"))
    emit(b"Executed", {
        "subject_hash": action_hash,
        "nonce": nonce,
        "approvals": len(seen),
        "threshold": threshold,
    })
'''

# Contract constants (must mirror inline contract)
O1 = b"\x11" * 32
O2 = b"\x22" * 32
O3 = b"\x33" * 32
NON_OWNER = b"\x44" * 32  # not in {O1, O2, O3}


def _write_contract(tmp_path: Path) -> Path:
    p = tmp_path / "multisig_inline.py"
    p.write_text(MULTISIG_SRC, encoding="utf-8")
    return p


def _pack_approvals(*owners: bytes) -> bytes:
    """Concatenate 32-byte owner ids into a single approvals blob."""
    return b"".join(owners)


# -------------------------------- tests --------------------------------

def test_happy_path_2_of_3_executes_and_consumes_nonce(tmp_path: Path, compile_contract):
    c = compile_contract(_write_contract(tmp_path))
    c.call("init", 2)  # 2-of-3
    assert c.call("get_threshold") == 2

    subject = b"transfer:alice:100"
    nonce = 1
    approvals_blob = _pack_approvals(O1, O2)
    c.call("execute", subject, nonce, approvals_blob, 2)

    # Nonce is now consumed
    assert c.call("used", nonce) is True

    # Event reflects approvals & threshold
    assert len(c.events) == 1
    ev = c.events[0]
    assert ev["name"] == b"Executed"
    assert ev["args"]["nonce"] == nonce
    assert ev["args"]["approvals"] == 2
    assert ev["args"]["threshold"] == 2


def test_replay_same_nonce_rejected(tmp_path: Path, compile_contract):
    c = compile_contract(_write_contract(tmp_path))
    c.call("init", 2)

    subject = b"op:1"
    nonce = 7
    c.call("execute", subject, nonce, _pack_approvals(O2, O3), 2)
    assert c.call("used", nonce) is True

    with pytest.raises(Exception):
        # Same nonce again, even with different subject, is disallowed in this stub
        c.call("execute", b"op:1:again", nonce, _pack_approvals(O1, O2), 2)


def test_duplicate_approver_is_rejected_even_if_meeting_threshold(tmp_path: Path, compile_contract):
    c = compile_contract(_write_contract(tmp_path))
    c.call("init", 2)

    # Approvals blob attempts to "double count" O1
    subject = b"dup:check"
    nonce = 2
    approvals_blob = _pack_approvals(O1, O1)  # duplicate
    with pytest.raises(Exception):
        c.call("execute", subject, nonce, approvals_blob, 2)

    # Ensure nonce remains unused
    assert c.call("used", nonce) is False
    assert len(c.events) == 0


def test_non_owner_in_approvals_rejected(tmp_path: Path, compile_contract):
    c = compile_contract(_write_contract(tmp_path))
    c.call("init", 2)

    subject = b"non-owner"
    nonce = 3
    approvals_blob = _pack_approvals(O1, NON_OWNER)
    with pytest.raises(Exception):
        c.call("execute", subject, nonce, approvals_blob, 2)

    assert c.call("used", nonce) is False
    assert len(c.events) == 0


@pytest.mark.parametrize("threshold", [1, 2, 3])
def test_insufficient_approvals_for_threshold(tmp_path: Path, compile_contract, threshold: int):
    c = compile_contract(_write_contract(tmp_path))
    c.call("init", threshold)

    subject = b"insufficient"
    nonce = 4 + threshold  # vary by threshold for clarity

    # Build an approvals set that is one short of the threshold (or zero when threshold=1)
    owners_by_t = {
        1: [],
        2: [O1],         # need 2 but only 1 provided
        3: [O1, O2],     # need 3 but only 2 provided
    }
    chosen = owners_by_t[threshold]
    approvals_blob = _pack_approvals(*chosen)

    with pytest.raises(Exception):
        c.call("execute", subject, nonce, approvals_blob, len(chosen))

    # No state changes on failure
    assert c.call("used", nonce) is False
    assert len(c.events) == 0


def test_all_three_owners_3_of_3_succeeds(tmp_path: Path, compile_contract):
    c = compile_contract(_write_contract(tmp_path))
    c.call("init", 3)  # require unanimous

    subject = b"unanimous"
    nonce = 99
    approvals_blob = _pack_approvals(O1, O2, O3)
    c.call("execute", subject, nonce, approvals_blob, 3)

    assert c.call("used", nonce) is True
    assert len(c.events) == 1
    assert c.events[0]["args"]["approvals"] == 3
    assert c.events[0]["args"]["threshold"] == 3
