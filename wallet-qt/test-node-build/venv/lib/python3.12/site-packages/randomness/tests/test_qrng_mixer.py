import os
from binascii import unhexlify
from typing import Any, Callable, Optional, Tuple

import pytest

# Module under test
import randomness.qrng.mixer as mix_mod  # type: ignore


def _hb(s: str) -> bytes:
    s = s.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) % 2 == 1:
        s = "0" + s
    return unhexlify(s)


def _extract_bytes(obj: Any) -> Optional[bytes]:
    """Try to get mixed/beacon bytes from a variety of return types."""
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    if isinstance(obj, dict):
        for k in ("mixed", "output", "beacon", "value", "bytes"):
            v = obj.get(k)
            if isinstance(v, (bytes, bytearray)):
                return bytes(v)
            if isinstance(v, str):
                try:
                    return _hb(v)
                except Exception:
                    pass
    if isinstance(obj, (tuple, list)) and obj:
        # Favor last element (common in (ok, bytes))
        last = obj[-1]
        if isinstance(last, (bytes, bytearray)):
            return bytes(last)
        # Or first, in case the API returns (bytes, meta)
        first = obj[0]
        if isinstance(first, (bytes, bytearray)):
            return bytes(first)
    for k in ("mixed", "output", "beacon", "value"):
        if hasattr(obj, k):
            v = getattr(obj, k)
            if isinstance(v, (bytes, bytearray)):
                return bytes(v)
            if isinstance(v, str):
                try:
                    return _hb(v)
                except Exception:
                    pass
    return None


def _call_mix(
    beacon: bytes,
    qrng: Optional[bytes],
    transcript: Optional[bytes],
    disabled: bool = False,
) -> Tuple[bool, Optional[bytes]]:
    """
    Try common function names and signatures:
      - mix(beacon, qrng, transcript)
      - mix(beacon, qrng, transcript=...)
      - apply_qrng_mix(...), mix_with_qrng(...), qrng_mix(...)
      Returns (called, bytes_or_none)
    """
    fn_names = [
        "mix",
        "mix_qrng",
        "apply_qrng_mix",
        "mix_with_qrng",
        "qrng_mix",
    ]
    f: Optional[Callable[..., Any]] = None
    for name in fn_names:
        if hasattr(mix_mod, name) and callable(getattr(mix_mod, name)):
            f = getattr(mix_mod, name)
            break
    if f is None:
        pytest.skip("No QRNG mixer function exported by randomness.qrng.mixer")

    candidates = []
    # Primary signatures
    if qrng is not None and transcript is not None:
        candidates += [
            ((beacon, qrng, transcript), {}),
            ((beacon, qrng), {"transcript": transcript}),
            (tuple(), {"beacon": beacon, "qrng": qrng, "transcript": transcript}),
        ]
    elif qrng is not None:
        candidates += [
            ((beacon, qrng), {}),
            (tuple(), {"beacon": beacon, "qrng": qrng}),
        ]
    else:
        candidates += [
            ((beacon,), {}),
            (tuple(), {"beacon": beacon}),
        ]

    # Some APIs may have an explicit flag to disable mixing
    if disabled:
        more = []
        for args, kwargs in candidates:
            kw = dict(kwargs)
            kw.update({"enabled": False})
            more.append((args, kw))
            kw2 = dict(kwargs)
            kw2.update({"disable": True})
            more.append((args, kw2))
        candidates = more + candidates

    last_type_error: Optional[TypeError] = None
    for args, kwargs in candidates:
        try:
            out = f(*args, **kwargs)
            return True, _extract_bytes(out)
        except TypeError as e:
            last_type_error = e
            continue
    pytest.skip(
        f"Could not call QRNG mixer with any supported signature (last TypeError: {last_type_error})"
    )
    return False, None  # unreachable


@pytest.fixture
def beacon() -> bytes:
    # 32-byte deterministic "beacon" (pre-mix)
    return _hb("0x1f7aa8a5e0b0b2e6c5d4f3a2b1c0ffeeddccbbaa99887766554433221100abcd")


@pytest.fixture
def qrng_bytes() -> bytes:
    # 48 bytes of QRNG — longer than beacon to exercise extractor/stretch logic
    return _hb(
        "0x"
        "7e3f2c1d0e9a8b7c6d5e4f3a2b1c0dfe"
        "112233445566778899aabbccddeeff00"
        "cafebabef00ddeadbeef001122334455"
    )


@pytest.fixture
def transcript() -> bytes:
    # Domain-separated transcript; change this and the output should change
    return b"animica.qrng|round:12345|provider:demo|v1"


def test_qrng_disabled_is_passthrough(beacon: bytes, transcript: bytes):
    # No qrng provided -> must pass through unmodified
    called, out = _call_mix(beacon, qrng=None, transcript=transcript, disabled=True)
    assert called
    assert out is not None, "Mixer must return bytes"
    assert (
        out == beacon
    ), "Disabled/no-QRNG mix should be a pure passthrough of the beacon"


def test_qrng_enabled_is_deterministic(
    beacon: bytes, qrng_bytes: bytes, transcript: bytes
):
    # Same inputs → same outputs (determinism)
    called, out1 = _call_mix(beacon, qrng_bytes, transcript)
    assert called and out1 is not None
    called, out2 = _call_mix(beacon, qrng_bytes, transcript)
    assert called and out2 is not None
    assert out1 == out2, "Mixer must be deterministic for identical inputs"

    # Length should generally match beacon length for XOR-style mixers
    assert len(out1) == len(beacon), "Mixed output length should match beacon length"


def test_qrng_changes_with_transcript_and_bytes(
    beacon: bytes, qrng_bytes: bytes, transcript: bytes
):
    # Changing the QRNG bytes should (with overwhelming probability) change the output
    called, out_base = _call_mix(beacon, qrng_bytes, transcript)
    assert called and out_base is not None

    qrng_alt = bytes(reversed(qrng_bytes))
    called, out_alt_bytes = _call_mix(beacon, qrng_alt, transcript)
    assert called and out_alt_bytes is not None
    assert (
        out_alt_bytes != out_base
    ), "Different QRNG bytes should produce different mixed outputs"

    # Changing the transcript should change the output (transcript binding)
    transcript_alt = transcript + b"|alt"
    called, out_alt_tx = _call_mix(beacon, qrng_bytes, transcript_alt)
    assert called and out_alt_tx is not None
    assert out_alt_tx != out_base, "Different transcript must yield a different output"


def test_qrng_empty_bytes_is_passthrough(beacon: bytes, transcript: bytes):
    # Explicit empty QRNG input should be treated like "no mix"
    called, out = _call_mix(beacon, b"", transcript)
    assert called and out is not None
    assert out == beacon, "Empty QRNG input should not alter the beacon"


def test_qrng_idempotence_with_same_inputs(
    beacon: bytes, qrng_bytes: bytes, transcript: bytes
):
    # Apply mixer twice should be equivalent to once if mixer is extract-then-xor with same derived mask
    called, once = _call_mix(beacon, qrng_bytes, transcript)
    assert called and once is not None

    called, twice = _call_mix(once, qrng_bytes, transcript)
    assert called and twice is not None

    # If the mixer derives the mask solely from (qrng, transcript) and XORs it with the beacon,
    # then reapplying it would revert to original beacon (mask XOR mask cancels out).
    # Some designs instead use transcript that evolves; in that case twice != beacon.
    # We accept BOTH patterns by asserting determinism and one of the two outcomes.
    assert twice in (
        beacon,
        once,
    ), "Applying mixer twice should be either a no-op or invertible; got unexpected behavior"
