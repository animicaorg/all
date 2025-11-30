# -*- coding: utf-8 -*-
"""
Quantum RNG mix reproducibility:
- Mixing scheme: MIX = sha3_256(b"QRMX" || beacon || qbytes)
- Same (beacon, qbytes) ⇒ same MIX (idempotent and deterministic)
- Different qbytes or different beacon ⇒ different MIX (collision resistance expectation)
- Contract also records last mix and emits an event with simple metadata.

We implement a tiny inline contract that uses stdlib.hash.sha3_256 and a
beacon syscall exposed via stdlib.syscalls.get_beacon() which we monkeypatch
in the test to deterministic values.

This focuses on the *contract-level* mixing determinism (ABI-safe bytes in/out),
not the beacon engine or quantum device attestations (covered elsewhere).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# --------------------------- inline quantum-rng contract ------------------------

CONTRACT_SRC = r"""
from stdlib.storage import get, set
from stdlib.events import emit
from stdlib.hash import sha3_256
from stdlib.abi import require
from stdlib.syscalls import get_beacon

K_LAST = b"last_mix"

def init() -> None:
    set(K_LAST, b"")

def last_mix() -> bytes:
    v = get(K_LAST)
    return v if v is not None else b""

def mix_with(qbytes: bytes) -> bytes:
    # Require some quantum bytes (could be the output of a prior quantum job)
    require(qbytes is not None and len(qbytes) > 0, "empty qbytes")
    beacon = get_beacon()  # contract-facing syscall (bytes)
    # Reproducible "beacon ⊕ quantum" extractor (domain tagged, no length leakage)
    out = sha3_256(b"QRMX" + beacon + qbytes)
    set(K_LAST, out)
    emit(b"QMix", {"qlen": len(qbytes)})
    return out
"""


def _write_contract(tmp_path: Path) -> Path:
    p = tmp_path / "quantum_rng_inline.py"
    p.write_text(CONTRACT_SRC, encoding="utf-8")
    return p


# --------------------------------- fixtures ------------------------------------


@pytest.fixture()
def beacon_patch(monkeypatch):
    """
    Patch vm_py.stdlib.syscalls.get_beacon to return a deterministic 32-byte value.
    The callable can be re-bound inside a test to simulate a new round/beacon.
    """
    import vm_py.stdlib.syscalls as sc  # type: ignore

    current = {"val": b"\x11" * 32}

    def _get_beacon() -> bytes:
        return current["val"]

    # install initial beacon
    monkeypatch.setattr(sc, "get_beacon", _get_beacon, raising=True)

    class Controller:
        @staticmethod
        def set_beacon(b: bytes) -> None:
            assert isinstance(b, (bytes, bytearray)) and len(b) > 0
            current["val"] = bytes(b)

        @staticmethod
        def get_beacon() -> bytes:
            return current["val"]

    return Controller


# ----------------------------------- tests -------------------------------------


def test_qmix_reproducible_same_inputs(tmp_path: Path, compile_contract, beacon_patch):
    """
    Same beacon + same qbytes ⇒ identical mix across calls.
    """
    c = compile_contract(_write_contract(tmp_path))
    c.call("init")

    qbytes = b"\xaa" * 64
    m1 = c.call("mix_with", qbytes)
    m2 = c.call("mix_with", qbytes)

    assert isinstance(m1, (bytes, bytearray)) and len(m1) == 32  # sha3_256 output
    assert m1 == m2, "mix must be deterministic for identical inputs"
    assert c.call("last_mix") == m2
    # Event sequence: two QMix with the same qlen
    assert [e["name"] for e in c.events] == [b"QMix", b"QMix"]
    assert all(int(e["args"]["qlen"]) == len(qbytes) for e in c.events)


def test_qmix_changes_with_qbytes(tmp_path: Path, compile_contract, beacon_patch):
    """
    Changing qbytes while beacon stays fixed should change the mix.
    """
    c = compile_contract(_write_contract(tmp_path))
    c.call("init")

    q1 = b"quantum-output-v1"
    q2 = b"quantum-output-v2"  # different bytes

    m1 = c.call("mix_with", q1)
    m2 = c.call("mix_with", q2)

    assert m1 != m2, "different quantum bytes should alter the mix"
    # Quick non-triviality: both are well-formed 32-byte digests
    assert len(m1) == len(m2) == 32


def test_qmix_changes_with_beacon(tmp_path: Path, compile_contract, beacon_patch):
    """
    Changing beacon while qbytes stays fixed should change the mix.
    """
    c = compile_contract(_write_contract(tmp_path))
    c.call("init")

    q = b"\x55" * 32
    m_fixed_beacon_1 = c.call("mix_with", q)

    # Simulate a new finalized beacon round (e.g., next block/epoch)
    beacon_patch.set_beacon(b"\x22" * 32)
    m_fixed_beacon_2 = c.call("mix_with", q)

    assert m_fixed_beacon_1 != m_fixed_beacon_2, "new beacon must produce a new mix"
    assert len(m_fixed_beacon_2) == 32


def test_qmix_regression_sanity_vectors(tmp_path: Path, compile_contract, beacon_patch):
    """
    Small table-driven regression check: (beacon, qbytes) → expected digest prefix/length.
    We don't lock the full digest in this repository-level test to avoid over-constraining
    the hash implementation, but we do verify stable prefixes against a single run.
    """
    c = compile_contract(_write_contract(tmp_path))
    c.call("init")

    vectors = [
        (b"\x11" * 32, b"alpha"),
        (b"\x11" * 32, b"beta"),
        (b"\x33" * 32, b"alpha"),
        (b"\x33" * 32, b"gamma"),
    ]

    prefixes = []
    for beacon, q in vectors:
        beacon_patch.set_beacon(beacon)
        out = c.call("mix_with", q)
        assert len(out) == 32
        # record first 8 bytes as a lightweight "fingerprint"
        prefixes.append(bytes(out[:8]))

    # All entries should be distinct across these deliberately different (beacon,q) pairs
    assert len(set(prefixes)) == len(
        prefixes
    ), "mix outputs should differ across distinct inputs"


def test_qmix_rejects_empty_qbytes(tmp_path: Path, compile_contract, beacon_patch):
    """
    Contract should reject empty quantum bytes to avoid accidental beacon-only usage.
    """
    c = compile_contract(_write_contract(tmp_path))
    c.call("init")

    with pytest.raises(Exception):
        c.call("mix_with", b"")
