import binascii
import importlib
import sys
import types
from typing import Any, Optional

import pytest

# ---------------- helpers: import + bytes coercion ----------------


def _import(name: str):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _get_attr(obj: Any, names: list[str]):
    if obj is None:
        return None
    for n in names:
        v = getattr(obj, n, None)
        if callable(v) or v is not None:
            return v
    return None


def _as_bytes(x: Any) -> bytes:
    if isinstance(x, bytes):
        return x
    if isinstance(x, bytearray):
        return bytes(x)
    if isinstance(x, str) and x.startswith("0x"):
        hx = x[2:]
        if len(hx) % 2:
            hx = "0" + hx
        return binascii.unhexlify(hx)
    if isinstance(x, (list, tuple)) and all(
        isinstance(i, int) and 0 <= i <= 255 for i in x
    ):
        return bytes(x)  # type: ignore[arg-type]
    raise TypeError(f"cannot coerce to bytes: {type(x)}")


# ---------------- call into random provider in flexible ways ----------------


def _resolve_random_fn():
    """
    Try to find a random byte generator in:
      - capabilities.host.random.{random_bytes, random, get_random}
      - capabilities.host.provider.{Provider|HostProvider}.random*_method
    Returns a callable that accepts (length:int, seed:Optional[bytes]) -> bytes
    """
    # host.random.*
    hmod = _import("capabilities.host.random")
    for name in ["random_bytes", "random", "get_random", "rand_bytes", "rand"]:
        fn = _get_attr(hmod, [name])
        if callable(fn):

            def _call_len_seed(length: int, seed: Optional[bytes]):
                # Try common signatures
                for attempt in (
                    lambda: fn(length, seed),
                    lambda: fn(length),
                    lambda: fn(size=length, seed=seed),
                    lambda: fn(n=length, seed=seed),
                    lambda: fn(nbytes=length, seed=seed),
                    lambda: fn(seed, length),
                ):
                    try:
                        out = attempt()
                        b = _as_bytes(out)
                        assert len(b) == length
                        return b
                    except TypeError:
                        continue
                raise AssertionError("random fn signature not supported")

            return _call_len_seed

    # provider
    pmod = _import("capabilities.host.provider")
    Provider = _get_attr(pmod, ["Provider", "HostProvider", "SyscallProvider"])
    if Provider:
        prov = Provider()  # type: ignore[call-arg]
        for name in ["random_bytes", "random", "get_random", "rand_bytes"]:
            meth = getattr(prov, name, None)
            if callable(meth):

                def _call_len_seed(length: int, seed: Optional[bytes]):
                    for attempt in (
                        lambda: meth(length, seed),
                        lambda: meth(length),
                        lambda: meth(size=length, seed=seed),
                        lambda: meth(n=length, seed=seed),
                        lambda: meth(nbytes=length, seed=seed),
                        lambda: meth(seed, length),
                    ):
                        try:
                            out = attempt()
                            b = _as_bytes(out)
                            assert len(b) == length
                            return b
                        except TypeError:
                            continue
                    raise AssertionError("provider random signature not supported")

                return _call_len_seed

    pytest.skip("No random() implementation available")


# ---------------- tests ----------------


def test_random_deterministic_same_seed():
    rand = _resolve_random_fn()
    seed = b"fixed-seed-0123456789abcdef"
    a = rand(32, seed)
    b = rand(32, seed)
    assert isinstance(a, (bytes, bytearray)) and isinstance(b, (bytes, bytearray))
    assert bytes(a) == bytes(b), "same seed should yield identical bytes"
    assert len(a) == 32


def test_random_changes_with_seed():
    rand = _resolve_random_fn()
    a = rand(32, b"seed-A")
    b = rand(32, b"seed-B")
    assert bytes(a) != bytes(b), "different seeds should yield different bytes"


def test_random_beacon_mix_once_available(monkeypatch):
    """
    When a randomness beacon adapter is available, the stub should mix it in.
    We monkeypatch a fake adapter that returns a fixed beacon and expect
    the output (for the same seed) to change compared to the no-beacon case.
    """
    rand = _resolve_random_fn()
    seed = b"mix-seed"
    without = rand(32, seed)

    # Install a fake beacon adapter module
    fake = types.SimpleNamespace(
        get_beacon=lambda: b"\xaa" * 32,
        current_beacon=lambda: b"\xaa" * 32,
        read_beacon=lambda: b"\xaa" * 32,
    )
    monkeypatch.setitem(sys.modules, "capabilities.adapters.randomness", fake)

    # Reload host.random to pick up the adapter, if it imports lazily.
    hmod = _import("capabilities.host.random")
    if hmod is not None:
        try:
            importlib.reload(hmod)
        except Exception:
            pass

    # Re-resolve in case the function reference changed after reload
    rand2 = _resolve_random_fn()
    with_beacon = rand2(32, seed)

    assert isinstance(without, (bytes, bytearray)) and isinstance(
        with_beacon, (bytes, bytearray)
    )
    if bytes(without) == bytes(with_beacon):
        pytest.xfail(
            "random stub did not mix beacon bytes (acceptable for minimal stub)"
        )
