# SPDX-License-Identifier: Apache-2.0
"""
Bech32m address tests (wallet ↔ core round-trip)

This suite exercises the bech32m encoder/decoder exposed by omni_sdk.address
and validates end-to-end properties you’d expect between a wallet (which
*encodes* an address string) and core components (which *decode* and validate).

We intentionally avoid depending on PQ signers here; the focus is the bech32m
transport and HRP handling, with property tests for multiple payload sizes.
"""

from __future__ import annotations

# Module under test
import importlib
import os
import random
from typing import Callable, Tuple

import pytest
from hypothesis import given
from hypothesis import strategies as st

addrmod = importlib.import_module("omni_sdk.address")


# -- Small compatibility shim -------------------------------------------------
# We support any of these public APIs (depending on the exact module layout):
# - bech32m_encode(hrp: str, data: bytes) -> str
# - bech32m_decode(s: str) -> Tuple[str, bytes]
# - encode_address(data: bytes, hrp: str = "omni") -> str
# - decode_address(s: str, expected_hrp: str | None = None) -> bytes
# - validate_address(s: str, hrp: str | None = None) -> bool


def _get_encode() -> Callable[[str, bytes], str]:
    if hasattr(addrmod, "bech32m_encode"):
        return lambda hrp, data: addrmod.bech32m_encode(hrp, data)
    if hasattr(addrmod, "encode_address"):
        return lambda hrp, data: addrmod.encode_address(data, hrp=hrp)
    if hasattr(addrmod, "Address") and hasattr(addrmod.Address, "encode"):
        return lambda hrp, data: addrmod.Address.encode(hrp, data)
    pytest.skip("No supported encode function found in omni_sdk.address")


def _get_decode() -> Callable[[str], Tuple[str, bytes]]:
    if hasattr(addrmod, "bech32m_decode"):
        return lambda s: tuple(addrmod.bech32m_decode(s))  # type: ignore
    if hasattr(addrmod, "decode_address"):
        return lambda s: (
            getattr(addrmod, "hrp_of", lambda _s: os.getenv("CHAIN_HRP", "omni"))(s),
            addrmod.decode_address(s),
        )
    if hasattr(addrmod, "Address") and hasattr(addrmod.Address, "decode"):
        return lambda s: tuple(addrmod.Address.decode(s))
    pytest.skip("No supported decode function found in omni_sdk.address")


def _get_validate() -> Callable[[str, str | None], bool]:
    if hasattr(addrmod, "validate_address"):
        return lambda s, hrp=None: addrmod.validate_address(s, hrp=hrp)
    if hasattr(addrmod, "bech32m_decode"):

        def _v(s: str, hrp: str | None = None) -> bool:
            try:
                h, _ = addrmod.bech32m_decode(s)
                return (hrp is None) or (h == hrp)
            except Exception:
                return False

        return _v
    if hasattr(addrmod, "Address") and hasattr(addrmod.Address, "validate"):
        return lambda s, hrp=None: addrmod.Address.validate(s, hrp=hrp)
    pytest.skip("No supported validate function found in omni_sdk.address")


ENC = _get_encode()
DEC = _get_decode()
VAL = _get_validate()

DEFAULT_HRP = os.getenv("CHAIN_HRP", "omni")  # overridable in CI/env


# -- Helpers ------------------------------------------------------------------


def _mutate_last_char(s: str) -> str:
    alphabet = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"  # bech32 charset
    last = s[-1]
    for c in alphabet:
        if c != last:
            return s[:-1] + c
    # Fallback (shouldn't happen)
    return s[:-1] + ("q" if last != "q" else "p")


# -- Deterministic examples ---------------------------------------------------


@pytest.mark.parametrize("length", [20, 32, 33])
def test_roundtrip_fixed_examples(length: int):
    """Round-trip a few deterministic byte payloads with the default HRP."""
    random.seed(1337 + length)
    raw = bytes(random.getrandbits(8) for _ in range(length))
    s = ENC(DEFAULT_HRP, raw)
    assert (
        isinstance(s, str) and s.lower() == s
    ), "encoder should emit lowercase bech32m"
    hrp2, raw2 = DEC(s)
    assert hrp2 == DEFAULT_HRP
    assert raw2 == raw

    # Re-encoding the decoded payload should be stable
    s2 = ENC(hrp2, raw2)
    assert s2 == s


def test_uppercase_whole_string_is_allowed():
    """bech32m decoders must accept all-uppercase strings (but not mixed-case)."""
    raw = bytes.fromhex(
        "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    )
    s = ENC(DEFAULT_HRP, raw)
    su = s.upper()
    hrp, data = DEC(su)
    # The decoded HRP should be normalized to lowercase by most implementations
    assert hrp.lower() == DEFAULT_HRP
    assert data == raw


def test_mixed_case_rejected():
    raw = b"\x00" * 20
    s = ENC(DEFAULT_HRP, raw)
    mixed = s[:5].upper() + s[5:]  # introduce mixed case
    with pytest.raises(Exception):
        DEC(mixed)


def test_checksum_detects_corruption():
    raw = b"\xff" * 32
    s = ENC(DEFAULT_HRP, raw)
    bad = _mutate_last_char(s)
    with pytest.raises(Exception):
        DEC(bad)
    assert not VAL(bad, DEFAULT_HRP)


def test_hrp_mismatch_errors():
    raw = b"\x42" * 20
    s = ENC(DEFAULT_HRP, raw)
    # If validator supports expected HRP, it should fail on mismatch.
    assert VAL(s, DEFAULT_HRP)
    other = (
        "t" + DEFAULT_HRP
        if not DEFAULT_HRP.startswith("t")
        else DEFAULT_HRP.replace("t", "u", 1)
    )
    if other == DEFAULT_HRP:
        other = "test"
    assert not VAL(s, other)


# -- Property tests -----------------------------------------------------------

# Valid HRP characters are the range [33-126] excluding uppercase for canonical form,
# but we restrict to sane chain-like hrps: lowercase letters & digits.
hrp_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=1,
    max_size=16,
).filter(
    lambda h: any(ch.isalpha() for ch in h)
)  # require at least one letter

# Typical address payload sizes (in bytes). Keep within limits for bech32m length.
payload_sizes = st.sampled_from([20, 24, 28, 32, 33])


@given(hrp=hrp_strategy, n=payload_sizes, data=st.binary(min_size=33, max_size=33))
def test_roundtrip_property_fixed_33(hrp: str, n: int, data: bytes):
    """
    Property-based round-trip for a 33-byte payload (the longest we use),
    and with randomized HRPs to ensure we don't bake in a single network.
    """
    # We pass a 33-byte sample via 'data' for good variability; if 'n' != 33,
    # slice or extend deterministically to the desired size.
    raw = data[:n] if n <= len(data) else (data + b"\x00" * (n - len(data)))
    s = ENC(hrp, raw)
    hrp2, raw2 = DEC(s)
    assert hrp2 == hrp
    assert raw2 == raw
    assert VAL(s, hrp)
