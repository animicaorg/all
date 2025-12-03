# SPDX-License-Identifier: Apache-2.0
"""
CBOR stability tests (tx/block/proof envelopes)

Goals:
- Encoding is canonical/deterministic (map key order independent).
- Decoding then re-encoding yields byte-identical CBOR.
- Typical envelopes (tx, block, proof) round-trip losslessly.

We prefer the project's own CBOR helpers from omni_sdk.tx.encode, but fall
back to cbor2 or msgspec if necessary. If no CBOR backend is available, the
suite is skipped.
"""
from __future__ import annotations

import os
from typing import Any, Dict

import pytest

pytest.importorskip("hypothesis")
from hypothesis import given
from hypothesis import strategies as st

# -----------------------------------------------------------------------------
# CBOR backend resolution
# -----------------------------------------------------------------------------

_dumps = None
_loads = None
_backend_name = None

# 1) Prefer omni_sdk.tx.encode helpers (project canonical behavior)
try:
    from omni_sdk.tx import encode as _txenc  # type: ignore

    _dumps = getattr(_txenc, "cbor_dumps", None) or getattr(_txenc, "dumps", None)
    _loads = getattr(_txenc, "cbor_loads", None) or getattr(_txenc, "loads", None)
    if callable(_dumps) and callable(_loads):
        _backend_name = "omni_sdk.tx.encode"
except Exception:
    pass

# 2) Fallback: cbor2 with canonical encoding
if _dumps is None or _loads is None:
    try:
        import cbor2  # type: ignore

        def _dumps(obj: Any) -> bytes:  # type: ignore
            # canonical=True ensures deterministic key ordering.
            return cbor2.dumps(obj, canonical=True)

        def _loads(buf: bytes) -> Any:  # type: ignore
            return cbor2.loads(buf)

        _backend_name = "cbor2"
    except Exception:
        pass

# 3) Fallback: msgspec.cbor with canonical encoding
if _dumps is None or _loads is None:
    try:
        import msgspec  # type: ignore

        def _dumps(obj: Any) -> bytes:  # type: ignore
            return msgspec.cbor.encode(obj, canonical=True)

        def _loads(buf: bytes) -> Any:  # type: ignore
            return msgspec.cbor.decode(buf)

        _backend_name = "msgspec.cbor"
    except Exception:
        pass

if not (callable(_dumps) and callable(_loads)):
    pytest.skip(
        "No CBOR backend available (omni_sdk.tx.encode / cbor2 / msgspec)",
        allow_module_level=True,
    )


# -----------------------------------------------------------------------------
# Helpers & fixtures
# -----------------------------------------------------------------------------


def b32(fill: int) -> bytes:
    return bytes([fill & 0xFF]) * 32


def addr(fill: int) -> bytes:
    # 33-byte payloads are typical in our PQ schemes (e.g., compressed forms).
    return bytes([fill & 0xFF]) * 33


# Deterministic sample envelopes representative of real payloads.
TX_ENVELOPE: Dict[str, Any] = {
    "tx": {
        "chainId": int(os.getenv("CHAIN_ID", "31337")),
        "nonce": 7,
        "from": addr(0x11),
        "to": addr(0x22),
        "value": 123_456_789,
        "data": b"hello, omni",
        "gas": 150_000,
        "maxFee": 1_000,
        "type": "transfer",
    }
}

BLOCK_ENVELOPE: Dict[str, Any] = {
    "block": {
        "height": 12_345,
        "prev": b32(0x01),
        "timestamp": 1_700_000_000,
        "roots": {
            "tx": b32(0x02),
            "state": b32(0x03),
            "da": b32(0x04),
        },
        "proposer": addr(0x33),
    }
}

PROOF_ENVELOPE: Dict[str, Any] = {
    "proof": {
        "kind": "hashshare",
        "block": 12_345,
        "root": b32(0x09),
        "publisher": addr(0xAA),
        "parts": [b32(0x0A), b32(0x0B), b32(0x0C)],
        "meta": {"version": 1, "algo": "sha3-256"},
    }
}


def reorder_map(d: Any) -> Any:
    """Return a copy with reversed insertion order for dicts (deep)."""
    if isinstance(d, dict):
        items = list(d.items())[::-1]
        return {k: reorder_map(v) for k, v in items}
    if isinstance(d, list):
        return [reorder_map(x) for x in reversed(d)]
    return d


# -----------------------------------------------------------------------------
# Deterministic tests
# -----------------------------------------------------------------------------


def test_backend_selected():
    assert _backend_name in {"omni_sdk.tx.encode", "cbor2", "msgspec.cbor"}


@pytest.mark.parametrize("env", [TX_ENVELOPE, BLOCK_ENVELOPE, PROOF_ENVELOPE])
def test_roundtrip_and_stability(env: Dict[str, Any]):
    b1 = _dumps(env)
    obj2 = _loads(b1)
    b2 = _dumps(obj2)
    assert b1 == b2, "decode→encode should be byte-identical"

    # Map order should not matter (canonical)
    env_reordered = reorder_map(env)
    b3 = _dumps(env_reordered)
    assert b1 == b3, "canonical encoding must be independent of insertion order"


def test_canonical_map_ordering_simple():
    a = {"foo": 1, "bar": 2, "baz": 3}
    b = {"baz": 3, "foo": 1, "bar": 2}
    assert _dumps(a) == _dumps(b)


# -----------------------------------------------------------------------------
# Property-based tests
# -----------------------------------------------------------------------------

# Limit structure to "envelope-like" content to keep encoders fast & deterministic.
leaf_scalars = st.one_of(
    st.integers(min_value=0, max_value=2**53 - 1),  # JS-safe range, common in APIs
    st.binary(min_size=0, max_size=48),  # typical hash/addr/data sizes
    st.text(
        alphabet=st.characters(min_codepoint=32, max_codepoint=126),
        min_size=0,
        max_size=32,
    ),
)

key_names = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-",
    min_size=1,
    max_size=16,
)

maps = st.dictionaries(keys=key_names, values=leaf_scalars, min_size=0, max_size=8)

envelope_like = st.fixed_dictionaries(
    {
        "tx": maps,
        "block": maps,
        "proof": maps,
    }
) | st.one_of(
    st.fixed_dictionaries({"tx": maps}),
    st.fixed_dictionaries({"block": maps}),
    st.fixed_dictionaries({"proof": maps}),
)

nested = st.recursive(
    leaf_scalars,
    lambda children: st.one_of(
        st.lists(children, min_size=0, max_size=5),
        st.dictionaries(keys=key_names, values=children, min_size=0, max_size=6),
    ),
    max_leaves=30,
)


@given(obj=envelope_like)
def test_envelope_like_roundtrip_is_stable(obj: Dict[str, Any]):
    b1 = _dumps(obj)
    obj2 = _loads(b1)
    b2 = _dumps(obj2)
    assert b1 == b2


@given(obj=nested)
def test_general_nested_roundtrip_is_stable(obj: Any):
    b1 = _dumps(obj)
    obj2 = _loads(b1)
    b2 = _dumps(obj2)
    assert b1 == b2


@given(obj=envelope_like)
def test_order_independence(obj: Dict[str, Any]):
    b1 = _dumps(obj)
    obj_reordered = reorder_map(obj)
    b2 = _dumps(obj_reordered)
    assert b1 == b2
