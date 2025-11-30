# -*- coding: utf-8 -*-
"""
Local unit tests for the Oracle example contract using the Python VM.

These tests are written to be resilient to small API differences across
vm_py versions by trying a few loader/invocation shapes. If the VM package
is not available in the current environment, the whole file skips cleanly.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import pytest

# ---- helpers: tolerant VM loader & invoker ----------------------------------

_HERE = Path(__file__).resolve().parent
_MANIFEST = _HERE / "manifest.json"
_SOURCE = _HERE / "contract.py"

_VM_LOADERS: Tuple[str, ...] = (
    # prefer explicit manifest loaders first
    "vm_py.runtime.loader.load_manifest",
    "vm_py.runtime.loader.load_from_manifest",
    "vm_py.runtime.loader.load_contract",
    # some versions just expose a generic 'load' that accepts a manifest path
    "vm_py.runtime.loader.load",
)

# Different vm_py builds expose different invocation styles. We'll try, in order:
# 1) handle.call(fn, **kwargs, sender=?)
# 2) handle.invoke(fn, **kwargs, sender=?)
# 3) runtime.abi.call(handle, fn, kwargs, sender=?)
# 4) runtime.abi.dispatch(handle, fn, kwargs, ctx={'sender': ?})
_INVOCATION_METHODS: Tuple[Tuple[str, Optional[str]], ...] = (
    ("call", None),
    ("invoke", None),
    ("execute", None),
    (None, "call"),
    (None, "dispatch"),
)


def _import_by_name(name: str):
    import importlib

    module_name, _, attr_name = name.rpartition(".")
    mod = importlib.import_module(module_name)
    return getattr(mod, attr_name)


def _first_working_loader() -> Optional[Callable[..., Any]]:
    for dotted in _VM_LOADERS:
        try:
            fn = _import_by_name(dotted)
            if callable(fn):
                return fn
        except Exception:
            continue
    return None


def _invoke(
    handle: Any, fn_name: str, *, sender: Optional[bytes] = None, **kwargs
) -> Any:
    """
    Try multiple invocation conventions to call a contract function.
    """
    last_exc: Optional[BaseException] = None

    # Strategy 1: methods on the handle (call/invoke/execute)
    for meth_name, _ in _INVOCATION_METHODS:
        if meth_name is None:
            break
        meth = getattr(handle, meth_name, None)
        if callable(meth):
            try:
                if sender is None:
                    return meth(fn_name, **kwargs)
                # try named sender
                try:
                    return meth(fn_name, sender=sender, **kwargs)
                except TypeError:
                    # some builds expect a ctx dict
                    ctx = {"sender": sender}
                    return meth(fn_name, ctx=ctx, **kwargs)
            except BaseException as exc:  # keep trying other strategies
                last_exc = exc

    # Strategy 2: vm_py.runtime.abi.* helpers
    try:
        from vm_py.runtime import abi as _abi  # type: ignore

        for _, abi_call in _INVOCATION_METHODS:
            if abi_call is None:
                continue
            fn = getattr(_abi, abi_call, None)
            if callable(fn):
                try:
                    if abi_call == "dispatch":
                        ctx = {"sender": sender} if sender is not None else {}
                        return fn(handle, fn_name, kwargs, ctx=ctx)
                    if sender is None:
                        return fn(handle, fn_name, kwargs)
                    return fn(handle, fn_name, kwargs, sender=sender)
                except BaseException as exc:
                    last_exc = exc
    except Exception as exc:
        last_exc = exc

    raise RuntimeError(
        f"Unable to invoke contract function '{fn_name}' via known strategies: {last_exc}"
    )


def _addr(byte: int) -> bytes:
    """
    Build a 20-byte address like 0x{byte repeated}… for simple tests.
    """
    return bytes([byte]) * 20


def _pair(key: str) -> bytes:
    """
    Encode a pair label into a 32-byte key (right-padded with NULs).
    """
    b = key.encode("ascii")
    if len(b) > 32:
        raise ValueError("pair key too long")
    return b + b"\x00" * (32 - len(b))


def _b32(fill: int) -> bytes:
    return bytes([fill]) * 32


# ---- fixtures ---------------------------------------------------------------


@pytest.fixture(scope="module")
def vm_handle():
    """
    Attempts to load/compile the contract from the manifest using vm_py.
    If vm_py isn't available, skip the whole module.
    """
    loader = _first_working_loader()
    if loader is None:
        pytest.skip("vm_py not available (no compatible loader found)")

    if not _MANIFEST.is_file() or not _SOURCE.is_file():
        raise AssertionError("Example contract files are missing next to this test")

    # Many loaders accept just the manifest path; some accept (manifest_path,) or (**kwargs).
    try:
        handle = loader(str(_MANIFEST))
    except TypeError:
        # Try common alt signatures
        try:
            handle = loader(manifest_path=str(_MANIFEST))
        except Exception as exc:
            raise AssertionError(f"Failed to load manifest via vm_py: {exc}") from exc

    # Basic sanity: handle should be an object with some behavior
    for attr in ("call", "invoke", "execute"):
        if hasattr(handle, attr):
            break
    else:
        # it's fine if we will go through runtime.abi.* later
        pass

    return handle


# ---- tests ------------------------------------------------------------------


def test_init_and_permissions(vm_handle):
    owner = _addr(0x11)
    feeder = _addr(0x22)
    rand = _addr(0x33)

    # init(owner)
    _invoke(vm_handle, "init", sender=owner, owner=owner)

    # owner() must return owner
    got_owner = _invoke(vm_handle, "owner")
    assert isinstance(got_owner, (bytes, bytearray)) and bytes(got_owner) == owner

    # set_feeder by non-owner should fail
    with pytest.raises(Exception):
        _invoke(vm_handle, "set_feeder", sender=rand, addr=feeder, allowed=True)

    # owner grants feeder
    _invoke(vm_handle, "set_feeder", sender=owner, addr=feeder, allowed=True)

    # transfer_ownership to rand
    _invoke(vm_handle, "transfer_ownership", sender=owner, new_owner=rand)
    got_owner2 = _invoke(vm_handle, "owner")
    assert bytes(got_owner2) == rand

    # previous owner can no longer set_feeder
    with pytest.raises(Exception):
        _invoke(vm_handle, "set_feeder", sender=owner, addr=_addr(0x44), allowed=True)

    # new owner can update feeder
    _invoke(vm_handle, "set_feeder", sender=rand, addr=feeder, allowed=True)


def test_pair_config_and_submit_roundtrip(vm_handle):
    # Re-init using the current owner (from previous test)
    # Figure out current owner
    current_owner = _invoke(vm_handle, "owner")
    assert isinstance(current_owner, (bytes, bytearray))
    owner = bytes(current_owner)

    feeder = _addr(0x55)
    _invoke(vm_handle, "set_feeder", sender=owner, addr=feeder, allowed=True)

    pair = _pair("ETH/USD")

    # Not configured yet
    exists = _invoke(vm_handle, "has_pair", pair=pair)
    assert exists is False

    # Configure decimals
    _invoke(vm_handle, "set_pair_decimals", sender=owner, pair=pair, decimals=8)
    assert _invoke(vm_handle, "has_pair", pair=pair) is True
    assert _invoke(vm_handle, "get_decimals", pair=pair) == 8

    # Prepare an update
    now = int(time.time())
    v1 = 3250_12345678  # 8 decimals
    source = b"COINBASE".ljust(32, b"\x00")
    commit = _b32(0xAA)

    # Unauthorized submit must fail
    with pytest.raises(Exception):
        _invoke(
            vm_handle,
            "submit",
            sender=_addr(0x66),
            pair=pair,
            value=v1,
            ts=now,
            source=source,
            commitment=commit,
        )

    # Feeder submits successfully
    round_id = _invoke(
        vm_handle,
        "submit",
        sender=feeder,
        pair=pair,
        value=v1,
        ts=now,
        source=source,
        commitment=commit,
    )
    assert isinstance(round_id, int) and round_id >= 1

    # Latest matches
    latest = _invoke(vm_handle, "get_latest", pair=pair)
    # Expect tuple: (value, decimals, ts, round_id, source, commitment)
    assert isinstance(latest, (tuple, list)) and len(latest) == 6
    lv, ld, lts, lr, lsrc, lcom = latest
    assert lv == v1
    assert ld == 8
    assert isinstance(lts, int) and lts == now
    assert lr == round_id
    assert bytes(lsrc) == source
    assert bytes(lcom) == commit

    # Historic read
    hist = _invoke(vm_handle, "get_round", pair=pair, round_id=round_id)
    assert isinstance(hist, (tuple, list)) and len(hist) == 5
    hv, hd, hts, hsrc, hcom = hist
    assert hv == v1 and hd == 8 and hts == now
    assert bytes(hsrc) == source and bytes(hcom) == commit

    # Submit a newer round; round_id must increase by 1
    v2 = 3251_00000000
    round_id2 = _invoke(
        vm_handle,
        "submit",
        sender=feeder,
        pair=pair,
        value=v2,
        ts=now + 30,
        source=source,
        commitment=_b32(0xAB),
    )
    assert round_id2 == round_id + 1

    latest2 = _invoke(vm_handle, "get_latest", pair=pair)
    assert latest2[0] == v2 and latest2[3] == round_id2


def test_input_validation(vm_handle):
    owner = _invoke(vm_handle, "owner")
    owner_b = bytes(owner)
    feeder = _addr(0x77)
    _invoke(vm_handle, "set_feeder", sender=owner_b, addr=feeder, allowed=True)

    pair = _pair("BTC/USD")
    _invoke(vm_handle, "set_pair_decimals", sender=owner_b, pair=pair, decimals=8)

    # Negative timestamps should be rejected
    with pytest.raises(Exception):
        _invoke(
            vm_handle,
            "submit",
            sender=feeder,
            pair=pair,
            value=42,
            ts=-1,
            source=_b32(0x01),
            commitment=_b32(0x02),
        )

    # Oversize pair key (failure occurs before VM call in our helper)
    with pytest.raises(ValueError):
        _pair("THIS-PAIR-NAME-IS-DELIBERATELY-LONGER-THAN-32-BYTES")

    # Decimals must be a small integer (contract should enforce; expect failure)
    with pytest.raises(Exception):
        _invoke(vm_handle, "set_pair_decimals", sender=owner_b, pair=pair, decimals=255)
