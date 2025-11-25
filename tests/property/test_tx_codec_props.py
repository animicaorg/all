# -*- coding: utf-8 -*-
"""
Property tests for transaction codec: encode ↔ decode round-trip and byte-level idempotence.

Goals:
- For arbitrary (but bounded) transactions, encoding to CBOR then decoding yields
  an equal object (structure/content equality, not identity).
- Re-encoding a decoded tx yields byte-identical CBOR (canonical/idempotent).

These tests are intentionally defensive about implementation shapes:
- We try multiple encode/decode entrypoints (tx module helpers or core CBOR codec).
- We construct arbitrary Tx values via Hypothesis, preferring dataclass-aware
  generation if available, and otherwise falling back to a heuristic builder that
  covers common fields (sender/to/nonce/gas/value/data/chainId/kind/signature).

If no compatible Tx type or codec is available yet, tests are skipped with a
clear reason rather than failing spuriously.
"""
from __future__ import annotations

import dataclasses as _dc
import inspect
import sys
from typing import Any, Dict, Optional, Tuple, get_origin, get_args

import pytest
from hypothesis import given, settings, strategies as st

# ---- Optional imports (guarded) ---------------------------------------------

_tx_mod = None
_cbor_mod = None
_canon_mod = None
try:  # core types
    import core.types.tx as _tx_mod  # type: ignore
except Exception:  # pragma: no cover - module not present yet
    _tx_mod = None

try:  # canonical CBOR encoder/decoder
    import core.encoding.cbor as _cbor_mod  # type: ignore
except Exception:  # pragma: no cover
    _cbor_mod = None

try:  # optional canonical "SignBytes" encoder (nice-to-have)
    import core.encoding.canonical as _canon_mod  # type: ignore
except Exception:  # pragma: no cover
    _canon_mod = None


# ---- Capability detection ----------------------------------------------------

def _get_tx_type():
    return getattr(_tx_mod, "Tx", None) if _tx_mod else None


def _has_codec() -> bool:
    """
    We support either:
      - tx.encode_tx / tx.decode_tx, or similar names
      - core.encoding.cbor.dumps / loads
    """
    if _tx_mod:
        enc_names = ("encode_tx", "to_cbor", "cbor_encode", "encode")
        dec_names = ("decode_tx", "from_cbor", "cbor_decode", "decode")
        if any(hasattr(_tx_mod, n) for n in enc_names) and any(
            hasattr(_tx_mod, n) for n in dec_names
        ):
            return True
    if _cbor_mod and hasattr(_cbor_mod, "dumps") and hasattr(_cbor_mod, "loads"):
        return True
    return False


# ---- Encoding/decoding shims ------------------------------------------------

def _encode_bytes(tx_obj: Any) -> bytes:
    """Try a few likely function names on the tx module; fall back to core CBOR."""
    if _tx_mod:
        for name in ("encode_tx", "to_cbor", "cbor_encode", "encode"):
            fn = getattr(_tx_mod, name, None)
            if fn:
                b = fn(tx_obj)  # type: ignore[call-arg]
                assert isinstance(b, (bytes, bytearray)), f"{name} must return bytes-like"
                return bytes(b)
    if _cbor_mod and hasattr(_cbor_mod, "dumps"):
        return _cbor_mod.dumps(tx_obj)  # type: ignore[attr-defined]
    pytest.skip("No tx encoder available (tx.encode_tx/… or core.encoding.cbor.dumps not found)")


def _decode_obj(b: bytes, TxType: Optional[type]) -> Any:
    """Decode bytes to a Tx-like object using available helpers."""
    if _tx_mod:
        for name in ("decode_tx", "from_cbor", "cbor_decode", "decode"):
            fn = getattr(_tx_mod, name, None)
            if fn:
                obj = fn(b)  # type: ignore[call-arg]
                return obj
    if _cbor_mod and hasattr(_cbor_mod, "loads"):
        try:
            if TxType is not None:
                # Some loaders accept a 'type' kwarg; call defensively
                sig = inspect.signature(_cbor_mod.loads)  # type: ignore[attr-defined]
                if any(p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY) and p.name == "type"
                       for p in sig.parameters.values()):
                    return _cbor_mod.loads(b, type=TxType)  # type: ignore[attr-defined]
        except Exception:
            pass
        obj = _cbor_mod.loads(b)  # type: ignore[attr-defined]
        # If we got a dict and we know TxType is a dataclass, try to construct it.
        if TxType is not None and _dc.is_dataclass(TxType) and isinstance(obj, dict):
            try:
                return TxType(**obj)  # type: ignore[misc]
            except Exception:
                return obj
        return obj
    pytest.skip("No tx decoder available (tx.decode_tx/… or core.encoding.cbor.loads not found)")


def _normalize(obj: Any) -> Any:
    """
    Convert a Tx-like object into a structure suitable for equality checks.
    - dataclasses → asdict()
    - has .to_dict() → use it
    - fallback: __dict__ or the object as-is
    """
    if _dc.is_dataclass(obj):
        return _dc.asdict(obj)
    if hasattr(obj, "to_dict") and callable(getattr(obj, "to_dict")):
        try:
            return obj.to_dict()
        except Exception:
            pass
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return obj


# ---- Strategies -------------------------------------------------------------

# Generic bounded integer for tx fields
_INT64 = st.integers(min_value=0, max_value=(1 << 63) - 1)
_U128 = st.integers(min_value=0, max_value=(1 << 128) - 1)

def _addr_bytes() -> st.SearchStrategy[bytes]:
    # 20 or 32 bytes addresses; choose one randomly
    return st.one_of(st.binary(min_size=20, max_size=20), st.binary(min_size=32, max_size=32))

def _maybe(t: st.SearchStrategy[Any]) -> st.SearchStrategy[Optional[Any]]:
    return st.one_of(st.none(), t)

def _bytes(max_len: int = 2048) -> st.SearchStrategy[bytes]:
    return st.binary(min_size=0, max_size=max_len)

def _tiny_list(elt: st.SearchStrategy[Any]) -> st.SearchStrategy[list]:
    return st.lists(elt, min_size=0, max_size=3)

def _signature_placeholder() -> st.SearchStrategy[Any]:
    """
    Minimal PQ signature placeholder. Implementations can ignore/override shape;
    this is only to satisfy constructors during codec tests.
    """
    alg_id = st.sampled_from([0, 1, 2, 3])  # e.g., reserved/dilithium3/sphincs+/hybrid
    pk = _bytes(2048)
    sig = _bytes(4096)
    # try both dict and tuple-ish shapes
    return st.one_of(
        st.fixed_dictionaries({"alg_id": alg_id, "public_key": pk, "signature": sig}),
        st.tuples(alg_id, pk, sig),
        _bytes(8192),  # some codecs may use raw bytes for signatures
    )

def _infer_strategy_from_type(tp: Any) -> Optional[st.SearchStrategy[Any]]:
    """Very small type → strategy mapper for common field annotations."""
    origin = get_origin(tp)
    args = get_args(tp)
    if tp in (int, "int"):
        return _U128
    if tp in (bytes, "bytes", bytearray):
        return _bytes()
    if tp in (str, "str"):
        # bech32 strings or hex; keep it simple
        return st.from_regex(r"^(0x)?[0-9a-fA-F]{0,80}$")
    if origin is Optional or origin is type(Optional[bytes]):
        return _maybe(_infer_strategy_from_type(args[0]) or _bytes())
    if origin in (list, tuple):
        base = _infer_strategy_from_type(args[0]) if args else _bytes()
        return _tiny_list(base)
    return None

def _tx_strategy_dataclass(TxType: type) -> st.SearchStrategy[Any]:
    """
    Try to build a strategy for the Tx dataclass by per-field heuristics.
    Only uses required fields; optional fields may be omitted to avoid failing builds.
    """
    fields = []
    try:
        fields = list(_dc.fields(TxType))  # type: ignore[arg-type]
    except Exception:
        return st.nothing()

    kw: Dict[str, st.SearchStrategy[Any]] = {}
    required = []
    for f in fields:
        is_required = f.default is _dc.MISSING and f.default_factory is _dc.MISSING  # type: ignore
        if not is_required:
            # optional fields: provide reasonable small strategies but allow omission
            strat = _infer_strategy_from_type(f.type) or _bytes()
            # common names special-cased
            if f.name.lower() in ("to", "receiver", "recipient"):
                strat = _maybe(_addr_bytes())
            elif f.name.lower() in ("data", "input", "payload"):
                strat = _bytes(1024)
            elif f.name.lower() in ("access_list", "accesslist"):
                strat = _tiny_list(st.tuples(_addr_bytes(), _tiny_list(_bytes(32))))
            elif f.name.lower() in ("signature", "sig", "witness"):
                strat = _signature_placeholder()
            kw[f.name] = strat
        else:
            required.append(f)

    # Required fields: try to satisfy common names
    for f in required:
        name = f.name.lower()
        if name in ("sender", "from_addr", "from"):
            kw[f.name] = _addr_bytes()
        elif name in ("nonce",):
            kw[f.name] = _INT64
        elif name in ("gas", "gaslimit", "gas_limit"):
            kw[f.name] = st.integers(min_value=21_000, max_value=30_000_000)
        elif name in ("gasprice", "gas_price", "maxfeepergas", "max_fee_per_gas"):
            kw[f.name] = st.integers(min_value=1, max_value=10**12)
        elif name in ("value", "amount"):
            kw[f.name] = _U128
        elif name in ("chainid", "chain_id"):
            kw[f.name] = st.integers(min_value=1, max_value=2**31 - 1)
        elif name in ("kind", "tx_kind", "type", "tx_type"):
            kw[f.name] = st.sampled_from(["transfer", "deploy", "call"])
        elif name in ("to", "receiver", "recipient"):
            kw[f.name] = _maybe(_addr_bytes())
        elif name in ("data", "input", "payload"):
            kw[f.name] = _bytes(1024)
        elif name in ("signature", "sig", "witness"):
            kw[f.name] = _signature_placeholder()
        else:
            # last resort: infer from annotation or set a small bytes/int
            kw[f.name] = _infer_strategy_from_type(f.type) or _bytes(64)

    return st.builds(TxType, **kw)


def _arb_tx_strategy() -> Tuple[Optional[type], st.SearchStrategy[Any]]:
    """
    Determine the most realistic strategy to generate a Tx object.
    - If a Tx dataclass is available, build from it.
    - Else, create a plain dict with typical tx fields for codecs that accept mappings.
    """
    TxType = _get_tx_type()
    if TxType is not None:
        # Try Hypothesis' dataclass support (if available), else our builder
        try:
            from hypothesis.extra import dataclasses as hdataclasses  # type: ignore
            return TxType, hdataclasses.from_type(TxType)  # type: ignore
        except Exception:
            return TxType, _tx_strategy_dataclass(TxType)

    # Fallback mapping strategy (for codecs that accept dicts)
    mapping = st.fixed_dictionaries(
        {
            "sender": _addr_bytes(),
            "nonce": _INT64,
            "gasLimit": st.integers(min_value=21_000, max_value=30_000_000),
            "gasPrice": st.integers(min_value=1, max_value=10**12),
            "chainId": st.integers(min_value=1, max_value=2**31 - 1),
            "kind": st.sampled_from(["transfer", "deploy", "call"]),
            "to": _maybe(_addr_bytes()),
            "value": _U128,
            "data": _bytes(1024),
            # Optional extras some implementations may accept:
            "access_list": _tiny_list(st.tuples(_addr_bytes(), _tiny_list(_bytes(32)))),
        }
    )
    return None, mapping


# ---- Tests ------------------------------------------------------------------

@pytest.mark.skipif(not _has_codec(), reason="Tx codec not available yet")
@given(_arb_tx_strategy()[1])
@settings(max_examples=200)
def test_tx_cbor_roundtrip_object_equality(tx_any: Any):
    """Encode → decode reproduces the same Tx (normalized structural equality)."""
    TxType, _ = _arb_tx_strategy()
    b1 = _encode_bytes(tx_any)
    tx_dec = _decode_obj(b1, TxType)
    assert _normalize(tx_any) == _normalize(tx_dec)


@pytest.mark.skipif(not _has_codec(), reason="Tx codec not available yet")
@given(_arb_tx_strategy()[1])
@settings(max_examples=200)
def test_tx_cbor_idempotent_bytes(tx_any: Any):
    """Re-encoding a decoded Tx yields byte-identical CBOR (canonical encoding)."""
    TxType, _ = _arb_tx_strategy()
    b1 = _encode_bytes(tx_any)
    tx_dec = _decode_obj(b1, TxType)
    b2 = _encode_bytes(tx_dec)
    assert b1 == b2, "CBOR encoding must be canonical/idempotent for Tx"


