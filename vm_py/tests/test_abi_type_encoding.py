from __future__ import annotations

from typing import Any, List, Tuple

import pytest

from vm_py.abi.decoding import (decode_args, decode_bool, decode_bytes,
                                decode_int, decode_uint, decode_value)
from vm_py.abi.encoding import (encode_args, encode_bool, encode_bytes,
                                encode_int, encode_uint, encode_value)
from vm_py.abi.types import ABITypeError, ValidationError, parse_type


def _roundtrip_scalar(py_val: Any, type_spec: str) -> Tuple[Any, bytes]:
    """
    Helper: encode_value + decode_value roundtrip for a single scalar.
    Asserts that we consume exactly the encoded length and that encoding
    is deterministic.
    """
    buf = encode_value(py_val, type_spec)
    out, offset = decode_value(buf, type_spec)
    assert offset == len(buf)
    # determinism: re-encoding is identical
    assert buf == encode_value(py_val, type_spec)
    return out, buf


# ---------------------------------------------------------------------------
# Bool
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("val", [False, True])
def test_bool_roundtrip_and_canonical_bytes(val: bool) -> None:
    out, buf = _roundtrip_scalar(val, "bool")
    assert out is val

    # encode_bool uses canonical 0x00/0x01 single byte
    direct = encode_bool(val)
    assert buf == direct
    assert buf in (b"\x00", b"\x01")


def test_decode_bool_rejects_invalid_bytes_in_strict_mode() -> None:
    # truncated payload
    with pytest.raises(ValueError):
        decode_bool(b"", 0)

    # invalid tag value
    with pytest.raises(ValueError):
        decode_bool(b"\x02", 0)


# ---------------------------------------------------------------------------
# Unsigned integers (uint / uint256)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "val",
    [
        0,
        1,
        42,
        2**128,
        (1 << 256) - 1,
    ],
)
@pytest.mark.parametrize("type_spec", ["uint", "uint256"])
def test_uint_roundtrip_and_determinism(val: int, type_spec: str) -> None:
    out, buf = _roundtrip_scalar(val, type_spec)
    assert isinstance(out, int)
    assert out == val

    # sanity: for an explicit uint256, decode_uint must agree
    if type_spec == "uint256":
        direct, offset = decode_uint(buf, 0, bits=256)
        assert offset == len(buf)
        assert direct == val


def test_uint_rejects_out_of_range_and_negative() -> None:
    # > 2^256-1 should be rejected
    with pytest.raises(ValidationError):
        encode_value(1 << 256, "uint256")

    # negative should be rejected for unsigned
    with pytest.raises(ValidationError):
        encode_value(-1, "uint256")


# ---------------------------------------------------------------------------
# Signed integers (int / int256)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "val",
    [
        0,
        1,
        -1,
        2**127 - 1,
        -(2**127),
    ],
)
@pytest.mark.parametrize("type_spec", ["int", "int256"])
def test_int_roundtrip_and_determinism(val: int, type_spec: str) -> None:
    out, buf = _roundtrip_scalar(val, type_spec)
    assert isinstance(out, int)
    assert out == val

    if type_spec == "int256":
        direct, offset = decode_int(buf, 0, bits=256)
        assert offset == len(buf)
        assert direct == val


def test_int_rejects_out_of_range() -> None:
    # below -2^255
    with pytest.raises(ValidationError):
        encode_value(-(2**255) - 1, "int256")

    # above 2^255-1
    with pytest.raises(ValidationError):
        encode_value(2**255, "int256")


# ---------------------------------------------------------------------------
# bytes (dynamic) and bytesN (fixed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x00",
        b"hello",
        b"\x00\xff\x10",
        "0x",  # hex-string empty
        "0xdeadbeef",  # hex-string, normalized by coerce_bytes
    ],
)
def test_bytes_dynamic_roundtrip(payload: Any) -> None:
    out, buf = _roundtrip_scalar(payload, "bytes")
    assert isinstance(out, (bytes, bytearray))

    # If original was bytes-like, we expect exact equality.
    if isinstance(payload, (bytes, bytearray)):
        assert out == payload

    # For hex-strings, we only assert we got some bytes value and that
    # encoding is deterministic for the normalized form.
    normalized_buf = encode_value(out, "bytes")
    assert normalized_buf == buf


def test_bytes_fixed_roundtrip_and_length_errors() -> None:
    b4 = parse_type("bytes4")

    good = b"\x01\x02\x03\x04"
    buf = encode_value(good, b4)
    out, offset = decode_value(buf, b4)
    assert offset == len(buf)
    assert out == good

    # wrong lengths should be rejected
    for bad in (b"", b"\x01", b"\x01\x02\x03", b"\x01\x02\x03\x04\x05"):
        with pytest.raises(ValidationError):
            encode_value(bad, b4)


def test_bytes_fixed_decode_uses_exact_length() -> None:
    # When using decode_bytes with fixed_len, it should read exactly N bytes.
    buf = b"\xaa\xbb\xcc\xddextra"
    out, offset = decode_bytes(buf, 0, fixed_len=4)
    assert out == b"\xaa\xbb\xcc\xdd"
    assert offset == 4  # "extra" remains unread


# ---------------------------------------------------------------------------
# encode_args / decode_args – multi-value & shape errors
# ---------------------------------------------------------------------------


def test_encode_decode_args_multi_value_roundtrip() -> None:
    types = ["bool", "uint256", "bytes4", "bytes"]
    values = [True, 1234, b"ABCD", b"xyz"]

    blob = encode_args(types, values)
    # determinism
    assert blob == encode_args(types, values)

    decoded, offset = decode_args(blob, types)
    assert offset == len(blob)

    assert decoded[0] is True
    assert decoded[1] == 1234
    assert decoded[2] == b"ABCD"
    assert decoded[3] == b"xyz"


def test_encode_args_type_length_mismatch_raises() -> None:
    # fewer values than types
    with pytest.raises(ABITypeError):
        encode_args(["bool", "uint256"], [True])

    # more values than types
    with pytest.raises(ABITypeError):
        encode_args(["bool"], [True, 1])


def test_decode_args_truncated_buffer_raises() -> None:
    types = ["bool", "uint256"]
    values = [True, 99]

    blob = encode_args(types, values)
    truncated = blob[:-1]

    with pytest.raises(Exception):
        # We do not rely on the exact exception class (could be ValueError
        # from uvarint_decode or a ValidationError); we only require that
        # truncated input fails instead of silently succeeding.
        decode_args(truncated, types)


# ---------------------------------------------------------------------------
# Error cases for unsupported type strings
# ---------------------------------------------------------------------------


def test_unknown_type_string_rejected() -> None:
    with pytest.raises(ABITypeError):
        parse_type("foo")

    with pytest.raises(ABITypeError):
        encode_value(1, "foo")
