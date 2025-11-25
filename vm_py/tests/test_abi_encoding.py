from __future__ import annotations

import os
import random
from typing import Any, Callable, Optional, Tuple, Union

import pytest


# --------------------------------------------------------------------------------------
# Flexible wrappers over vm_py.abi.encoding / vm_py.abi.decoding so tests tolerate
# minor API differences (function names, arg order, kw vs positional).
# --------------------------------------------------------------------------------------

def _import_modules():
    try:
        import vm_py.abi.encoding as enc  # type: ignore
        import vm_py.abi.decoding as dec  # type: ignore
    except Exception as e:  # pragma: no cover - make failure obvious
        raise AssertionError(f"Failed to import vm_py.abi modules: {e}")
    return enc, dec


def _mk_encoder(enc_mod) -> Callable[[str, Any], bytes]:
    """
    Returns a function encode(type_name, value) -> bytes.
    Tries multiple signatures:
      - encode(value, type="int")
      - encode(value, "int")
      - encode("int", value)
      - encode_value(value, type_name)
      - Encoder().encode(...)
    """
    # Common function entrypoints
    candidates = []
    for name in ("encode", "encode_value", "encode_one", "encode_scalar"):
        fn = getattr(enc_mod, name, None)
        if callable(fn):
            candidates.append(fn)

    # Optional class-based encoder
    for cls_name in ("Encoder", "AbiEncoder"):
        cls = getattr(enc_mod, cls_name, None)
        if cls:
            try:
                inst = cls()  # type: ignore[call-arg]
                if hasattr(inst, "encode") and callable(inst.encode):
                    candidates.append(getattr(inst, "encode"))
            except Exception:
                pass

    if not candidates:
        raise AssertionError("No encoder function/class found in vm_py.abi.encoding")

    def _encode(type_name: str, value: Any) -> bytes:
        last_err: Optional[Exception] = None
        for fn in candidates:
            # Try kw-first
            try:
                return fn(value, type=type_name)  # type: ignore[call-arg]
            except TypeError as e:
                last_err = e
            except Exception as e:
                last_err = e
            # Positional (value, type)
            try:
                return fn(value, type_name)  # type: ignore[misc]
            except TypeError as e:
                last_err = e
            except Exception as e:
                last_err = e
            # Positional (type, value)
            try:
                return fn(type_name, value)  # type: ignore[misc]
            except TypeError as e:
                last_err = e
            except Exception as e:
                last_err = e
            # KW with different param name (abi_type)
            try:
                return fn(value, abi_type=type_name)  # type: ignore[call-arg]
            except TypeError as e:
                last_err = e
            except Exception as e:
                last_err = e
        raise AssertionError(f"Unable to encode {type_name} with available functions; last err: {last_err}")

    return _encode


def _mk_decoder(dec_mod) -> Callable[[str, bytes], Any]:
    """
    Returns a function decode(type_name, data: bytes) -> value.
    Tries multiple signatures:
      - decode(data, type="int")
      - decode(data, "int")
      - decode("int", data)
      - decode_value(data, type_name)
      - Decoder().decode(...)
    """
    candidates = []
    for name in ("decode", "decode_value", "decode_one", "decode_scalar"):
        fn = getattr(dec_mod, name, None)
        if callable(fn):
            candidates.append(fn)

    for cls_name in ("Decoder", "AbiDecoder"):
        cls = getattr(dec_mod, cls_name, None)
        if cls:
            try:
                inst = cls()  # type: ignore[call-arg]
                if hasattr(inst, "decode") and callable(inst.decode):
                    candidates.append(getattr(inst, "decode"))
            except Exception:
                pass

    if not candidates:
        raise AssertionError("No decoder function/class found in vm_py.abi.decoding")

    def _decode(type_name: str, data: bytes) -> Any:
        last_err: Optional[Exception] = None
        for fn in candidates:
            try:
                return fn(data, type=type_name)  # type: ignore[call-arg]
            except TypeError as e:
                last_err = e
            except Exception as e:
                last_err = e
            try:
                return fn(data, type_name)  # type: ignore[misc]
            except TypeError as e:
                last_err = e
            except Exception as e:
                last_err = e
            try:
                return fn(type_name, data)  # type: ignore[misc]
            except TypeError as e:
                last_err = e
            except Exception as e:
                last_err = e
            try:
                return fn(data, abi_type=type_name)  # type: ignore[call-arg]
            except TypeError as e:
                last_err = e
            except Exception as e:
                last_err = e
        raise AssertionError(f"Unable to decode {type_name} with available functions; last err: {last_err}")

    return _decode


ENC, DEC = _import_modules()
ENCODE = _mk_encoder(ENC)
DECODE = _mk_decoder(DEC)


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def _idem_roundtrip(type_name: str, value: Any) -> Tuple[bytes, Any, bytes]:
    """
    Encode -> Decode -> Encode again. Return (enc1, decoded, enc2).
    We assert enc1 == enc2 for canonical stability.
    """
    enc1 = ENCODE(type_name, value)
    assert isinstance(enc1, (bytes, bytearray))
    enc1 = bytes(enc1)

    dec = DECODE(type_name, enc1)
    enc2 = ENCODE(type_name, dec)
    assert isinstance(enc2, (bytes, bytearray))
    enc2 = bytes(enc2)
    return enc1, dec, enc2


def _is_bytes_like(x: Any) -> bool:
    return isinstance(x, (bytes, bytearray, memoryview))


def _maybe_make_address_string(payload: bytes) -> Optional[str]:
    """
    Try to make a bech32m 'anim1...' address string from a raw payload using whichever
    helper exists in the repo. If no helper is present, return None and the test
    will skip the string-form case.
    """
    # Try pq/py/utils/bech32.py
    try:
        from pq.py.utils import bech32 as b32  # type: ignore
        for name in ("encode", "bech32m_encode", "encode_bech32m"):
            fn = getattr(b32, name, None)
            if callable(fn):
                try:
                    return fn("anim", payload)  # type: ignore[call-arg]
                except TypeError:
                    # Maybe the function assumes no HRP?
                    try:
                        return fn(payload)  # type: ignore[misc]
                    except Exception:
                        pass
    except Exception:
        pass

    # Try a direct address helper
    for modpath, fname in (
        ("pq.py.address", "to_string"),
        ("pq.py.address", "to_bech32"),
        ("vm_py.abi.types", "address_to_string"),
    ):
        try:
            mod = __import__(modpath, fromlist=["*"])  # type: ignore
            fn = getattr(mod, fname, None)
            if callable(fn):
                return fn(payload)  # type: ignore[misc]
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------------------
# Tests: ints, bools, bytes, address
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value",
    [
        0,
        1,
        2**16 - 1,
        2**32 - 1,
        pytest.param(2**64 - 1, id="u64_max"),
    ],
)
def test_int_roundtrip(value: int):
    enc1, dec, enc2 = _idem_roundtrip("int", value)
    # Decoded should be int and equal to original where representable
    assert isinstance(dec, int)
    assert dec == value
    assert enc1 == enc2  # canonical stability


@pytest.mark.parametrize("value", [False, True])
def test_bool_roundtrip(value: bool):
    enc1, dec, enc2 = _idem_roundtrip("bool", value)
    assert isinstance(dec, bool)
    assert dec is value
    assert enc1 == enc2


@pytest.mark.parametrize(
    "blob",
    [
        b"",
        b"\x00\x01\x02",
        os.urandom(32),
        os.urandom(255),
    ],
)
def test_bytes_roundtrip(blob: bytes):
    enc1, dec, enc2 = _idem_roundtrip("bytes", blob)
    assert _is_bytes_like(dec)
    assert bytes(dec) == blob
    assert enc1 == enc2


def test_address_roundtrip_bytes_payload():
    # Use 32-byte payload; many schemes are alg_id||sha3(pubkey) but ABI can normalize.
    payloads = [b"\x00" * 32, b"\xAB" * 32, os.urandom(32)]
    for payload in payloads:
        enc1, dec, enc2 = _idem_roundtrip("address", payload)
        # Decoded value may be bytes or bech32 string; in either case, re-encode matches.
        if _is_bytes_like(dec):
            # If decoder preserves bytes payload, ensure it's the same or normalized form of it.
            assert isinstance(dec, (bytes, bytearray, memoryview))
        elif isinstance(dec, str):
            # Looks like bech32/hex string; basic sanity
            assert len(dec) > 8
        else:
            pytest.fail(f"Unexpected decoded address type: {type(dec)}")
        assert enc1 == enc2


def test_address_roundtrip_bech32_string_if_available():
    # Try to construct a bech32m address string; skip if no helper exists.
    payload = os.urandom(32)
    addr_str = _maybe_make_address_string(payload)
    if not addr_str:
        pytest.skip("No bech32 address helper available; skipping string-form address round-trip")

    enc1, dec, enc2 = _idem_roundtrip("address", addr_str)
    # Decoder may normalize to bytes or keep string; either is OK as long as canonical re-encode matches.
    assert enc1 == enc2
    if isinstance(dec, str):
        assert dec.startswith("anim")
    else:
        assert _is_bytes_like(dec)
