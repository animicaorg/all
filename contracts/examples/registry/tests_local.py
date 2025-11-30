# -*- coding: utf-8 -*-
"""
Local unit tests for the NameRegistry example contract.

These tests execute the Python contract via the vm_py runtime in-process,
so they do not require a running node. They verify basic CRUD behavior,
event emission (if the engine exposes an events sink), input validation,
and determinism idempotence for pure getters.

Assumptions:
- The contract lives next to this test as contract.py with manifest.json.
- The vm_py runtime loader can compile+link the contract from manifest+source.
- Address ABI accepts raw 32-byte values (for local tests we avoid bech32).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

# vm_py runtime imports (kept minimal to remain stable across refactors)
try:
    from vm_py.runtime import loader as vm_loader
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "vm_py.runtime.loader not available; ensure vm_py is installed"
    ) from exc


HERE = Path(__file__).parent.resolve()
MANIFEST = HERE / "manifest.json"
SOURCE = HERE / "contract.py"


# ---------- helpers -----------------------------------------------------------


def h256(data: bytes) -> bytes:
    """sha3_256 -> 32 bytes."""
    return hashlib.sha3_256(data).digest()


def name32(label: str) -> bytes:
    """Derive a deterministic 32-byte name from ASCII label."""
    return h256(label.encode("utf-8"))  # already 32 bytes


def addr32(tag: str) -> bytes:
    """Derive a deterministic 32-byte address (for local tests)."""
    return h256(("addr:" + tag).encode("utf-8"))


def zero_addr() -> bytes:
    return b"\x00" * 32


class ContractHandle:
    """
    Light wrapper around the loader-returned handle to make tests resilient
    across tiny API variations (some builds expose .call; others .invoke).
    Also exposes an optional event sink if present.
    """

    def __init__(self, handle: Any):
        self._h = handle

    def call(self, fn: str, *args: Any) -> Any:
        # Preferred: .call(fn, args)
        if hasattr(self._h, "call"):
            return self._h.call(fn, *args)
        # Fallback: .invoke(fn, args)
        if hasattr(self._h, "invoke"):
            return self._h.invoke(fn, *args)
        # Very old shape: .abi.call(fn, args)
        if hasattr(self._h, "abi") and hasattr(self._h.abi, "call"):
            return self._h.abi.call(fn, *args)
        raise AttributeError("Contract handle does not expose a callable interface")

    def events(self) -> List[Tuple[str, Dict[str, Any]]]:
        # Common shapes: .events (list), .pop_events(), or .event_sink.flush()
        if hasattr(self._h, "events") and isinstance(self._h.events, list):
            return list(self._h.events)
        if hasattr(self._h, "pop_events"):
            try:
                return list(self._h.pop_events())
            except TypeError:
                return list(self._h.pop_events)  # property
        if hasattr(self._h, "event_sink") and hasattr(self._h.event_sink, "flush"):
            return list(self._h.event_sink.flush())
        # Unknown/unsupported: return empty to avoid hard-coupling tests
        return []


def load_contract() -> ContractHandle:
    """
    Compile & link the contract from manifest+source and return a handle.
    """
    if not MANIFEST.is_file():
        raise FileNotFoundError(f"manifest.json missing at {MANIFEST}")
    if not SOURCE.is_file():
        raise FileNotFoundError(f"contract.py missing at {SOURCE}")
    # Support either positional or keyword args across loader versions.
    try:
        handle = vm_loader.load(manifest_path=str(MANIFEST), source_path=str(SOURCE))
    except TypeError:
        handle = vm_loader.load(str(MANIFEST))
    return ContractHandle(handle)


# ---------- fixtures ----------------------------------------------------------


@pytest.fixture()
def reg() -> ContractHandle:
    return load_contract()


# ---------- tests: core behavior ---------------------------------------------


def test_set_get_has_remove_roundtrip(reg: ContractHandle):
    alice = name32("alice")
    bob = name32("bob")
    a1 = addr32("alice-primary")
    a2 = addr32("bob-primary")

    # initially false
    assert reg.call("has", alice) is False
    assert reg.call("has", bob) is False

    # set -> NameSet event
    reg.call("set", alice, a1)
    reg.call("set", bob, a2)

    assert reg.call("has", alice) is True
    assert reg.call("has", bob) is True
    assert reg.call("get", alice) == a1
    assert reg.call("get", bob) == a2

    ev = reg.events()
    if ev:
        # Allow either tuple ("NameSet", {...}) or dict entries with name/args
        names = [e[0] if isinstance(e, tuple) else e.get("name") for e in ev]
        assert "NameSet" in names

    # remove -> NameRemoved event
    reg.call("remove", alice)
    assert reg.call("has", alice) is False
    # Unset "get" should return empty/zero address (contract-defined)
    got = reg.call("get", alice)
    assert isinstance(got, (bytes, bytearray))
    assert got == b"" or got == zero_addr()

    ev2 = reg.events()
    if ev2:
        names2 = [e[0] if isinstance(e, tuple) else e.get("name") for e in ev2]
        assert "NameRemoved" in names2


def test_set_overwrite_idempotent_value(reg: ContractHandle):
    name = name32("service")
    addr = addr32("svc-v1")

    reg.call("set", name, addr)
    assert reg.call("get", name) == addr
    # setting same value should be idempotent (no semantic change)
    reg.call("set", name, addr)
    assert reg.call("get", name) == addr


def test_change_value_updates(reg: ContractHandle):
    name = name32("rotating")
    a1 = addr32("v1")
    a2 = addr32("v2")
    reg.call("set", name, a1)
    assert reg.call("get", name) == a1
    reg.call("set", name, a2)
    assert reg.call("get", name) == a2


# ---------- tests: validation & errors ---------------------------------------


def test_reject_bad_name_length(reg: ContractHandle):
    # 31-byte (too short) or 33-byte (too long)
    bad_short = b"x" * 31
    bad_long = b"y" * 33
    with pytest.raises(Exception):
        reg.call("set", bad_short, addr32("short"))
    with pytest.raises(Exception):
        reg.call("set", bad_long, addr32("long"))
    with pytest.raises(Exception):
        reg.call("get", bad_short)
    with pytest.raises(Exception):
        reg.call("has", bad_long)


def test_reject_zero_address(reg: ContractHandle):
    nm = name32("zero")
    with pytest.raises(Exception):
        reg.call("set", nm, zero_addr())


def test_remove_unset_raises(reg: ContractHandle):
    nm = name32("nobody")
    assert reg.call("has", nm) is False
    with pytest.raises(Exception):
        reg.call("remove", nm)


# ---------- tests: determinism & stability -----------------------------------


def test_get_determinism_and_idempotence(reg: ContractHandle):
    nm = name32("determinism")
    ad = addr32("stable")
    reg.call("set", nm, ad)
    for _ in range(10):
        assert reg.call("get", nm) == ad
        assert reg.call("has", nm) is True


def test_multi_keys_isolation(reg: ContractHandle):
    k1, k2, k3 = name32("k1"), name32("k2"), name32("k3")
    a1, a2, a3 = addr32("a1"), addr32("a2"), addr32("a3")

    reg.call("set", k1, a1)
    reg.call("set", k2, a2)
    reg.call("set", k3, a3)

    reg.call("remove", k2)

    assert reg.call("get", k1) == a1 and reg.call("has", k1) is True
    assert reg.call("get", k2) in (b"", zero_addr()) and reg.call("has", k2) is False
    assert reg.call("get", k3) == a3 and reg.call("has", k3) is True


# ---------- optional: event payload shapes -----------------------------------


def test_event_payload_shape_if_available(reg: ContractHandle):
    nm = name32("events")
    ad = addr32("payload")
    reg.call("set", nm, ad)
    reg.call("remove", nm)
    events = reg.events()
    if not events:
        pytest.skip("event sink not exposed by runtime handle")

    # Accept either ("NameSet", {"name":..., "addr":...}) or dict-like records.
    def _norm(e: Any) -> Tuple[str, Dict[str, Any]]:
        if isinstance(e, tuple) and len(e) == 2 and isinstance(e[1], dict):
            return e[0], e[1]
        if isinstance(e, dict) and "name" in e and "args" in e:
            return e["name"], e["args"]
        # last-ditch guess: event object with attributes
        ename = getattr(e, "name", None)
        eargs = getattr(e, "args", None)
        return ename, eargs

    normed = [_norm(e) for e in events]
    names = [n for (n, _) in normed]
    assert "NameSet" in names and "NameRemoved" in names

    # Check one payload has expected keys and types
    for n, args in normed:
        if n == "NameSet":
            assert isinstance(args.get("name"), (bytes, bytearray))
            assert isinstance(args.get("addr"), (bytes, bytearray))
            break
    else:
        pytest.fail("NameSet payload not found")
