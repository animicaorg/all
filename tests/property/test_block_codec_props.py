# -*- coding: utf-8 -*-
"""
Property tests for headers/blocks:
- Encode → decode preserves structure.
- Hash is STABLE across encode/decode (idempotent under the codec).
- (Optional) Block hash equals embedded Header hash when both APIs exist.

The tests are defensive and work against slightly different module layouts:
- We try multiple encode/decode entrypoints (module helpers or core CBOR codec).
- We try several hash helpers on header/block modules; if none are present,
  we fall back to hashing the canonical CBOR bytes (sha3_256), which should be
  stable if the encoder is canonical.

If a required type or codec isn't available yet, tests are skipped with a clear reason.
"""
from __future__ import annotations

import dataclasses as _dc
import hashlib
import inspect
from typing import Any, Dict, Optional, Tuple, get_args, get_origin

import pytest
from hypothesis import given, settings, strategies as st

# ---- Optional imports (guarded) ---------------------------------------------

_hdr_mod = None
_blk_mod = None
_cbor_mod = None
_canon_mod = None
_hash_mod = None

try:
    import core.types.header as _hdr_mod  # type: ignore
except Exception:
    _hdr_mod = None

try:
    import core.types.block as _blk_mod  # type: ignore
except Exception:
    _blk_mod = None

try:
    import core.encoding.cbor as _cbor_mod  # type: ignore
except Exception:
    _cbor_mod = None

try:
    import core.encoding.canonical as _canon_mod  # type: ignore
except Exception:
    _canon_mod = None

try:
    # Optional: core.utils.hash may export sha3_256/512 wrappers
    import core.utils.hash as _hash_mod  # type: ignore
except Exception:
    _hash_mod = None


# ---- Helpers: type/codec/hash detection -------------------------------------

def _get_header_type():
    return getattr(_hdr_mod, "Header", None) if _hdr_mod else None

def _get_block_type():
    return getattr(_blk_mod, "Block", None) if _blk_mod else None

def _has_cbor_codec() -> bool:
    if _cbor_mod and hasattr(_cbor_mod, "dumps") and hasattr(_cbor_mod, "loads"):
        return True
    return False

def _has_header_codec() -> bool:
    if not _hdr_mod:
        return False
    enc = any(hasattr(_hdr_mod, n) for n in ("encode_header", "to_cbor", "cbor_encode", "encode"))
    dec = any(hasattr(_hdr_mod, n) for n in ("decode_header", "from_cbor", "cbor_decode", "decode"))
    return enc and dec

def _has_block_codec() -> bool:
    if not _blk_mod:
        return False
    enc = any(hasattr(_blk_mod, n) for n in ("encode_block", "to_cbor", "cbor_encode", "encode"))
    dec = any(hasattr(_blk_mod, n) for n in ("decode_block", "from_cbor", "cbor_decode", "decode"))
    return enc and dec

def _encode_bytes(obj: Any, kind: str) -> bytes:
    """
    kind ∈ {"header","block"} selects module-pref helpers before falling back to CBOR.
    """
    mod = _hdr_mod if kind == "header" else _blk_mod
    # Prefer specific helpers on the type module
    if mod:
        for name in (
            f"encode_{kind}",
            "to_cbor",
            "cbor_encode",
            "encode",
        ):
            fn = getattr(mod, name, None)
            if fn:
                out = fn(obj)  # type: ignore[misc]
                assert isinstance(out, (bytes, bytearray)), f"{name} must return bytes-like"
                return bytes(out)
    # Fallback to canonical CBOR (module should be deterministic)
    if _has_cbor_codec():
        return _cbor_mod.dumps(obj)  # type: ignore[attr-defined]
    pytest.skip(f"No {kind} encoder available (module helper or core.encoding.cbor.dumps)")

def _decode_obj(b: bytes, kind: str, T: Optional[type]) -> Any:
    mod = _hdr_mod if kind == "header" else _blk_mod
    if mod:
        for name in (
            f"decode_{kind}",
            "from_cbor",
            "cbor_decode",
            "decode",
        ):
            fn = getattr(mod, name, None)
            if fn:
                return fn(b)  # type: ignore[misc]
    if _has_cbor_codec():
        # Some loaders accept a 'type' kwarg
        try:
            sig = inspect.signature(_cbor_mod.loads)  # type: ignore[attr-defined]
            if any(p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY) and p.name == "type"
                   for p in sig.parameters.values()):
                return _cbor_mod.loads(b, type=T)  # type: ignore[attr-defined]
        except Exception:
            pass
        obj = _cbor_mod.loads(b)  # type: ignore[attr-defined]
        if T is not None and _dc.is_dataclass(T) and isinstance(obj, dict):
            try:
                return T(**obj)  # type: ignore[misc]
            except Exception:
                return obj
        return obj
    pytest.skip(f"No {kind} decoder available (module helper or core.encoding.cbor.loads)")

def _sha3_256(data: bytes) -> bytes:
    if _hash_mod and hasattr(_hash_mod, "sha3_256"):
        return _hash_mod.sha3_256(data)  # type: ignore[attr-defined]
    return hashlib.sha3_256(data).digest()

def _hash_header(hdr: Any) -> bytes:
    # Try methods on the object
    for m in ("hash", "header_hash"):
        if hasattr(hdr, m) and callable(getattr(hdr, m)):
            out = getattr(hdr, m)()
            if isinstance(out, (bytes, bytearray)):
                return bytes(out)
    if hasattr(hdr, "hash") and isinstance(getattr(hdr, "hash"), (bytes, bytearray)):
        return bytes(getattr(hdr, "hash"))

    # Try free functions on the header module
    if _hdr_mod:
        for fn_name in ("hash_header", "header_hash", "compute_hash", "calc_hash", "hash"):
            fn = getattr(_hdr_mod, fn_name, None)
            if fn:
                out = fn(hdr)  # type: ignore[misc]
                if isinstance(out, (bytes, bytearray)):
                    return bytes(out)

    # Fallback: canonical CBOR then sha3-256
    if _has_cbor_codec():
        return _sha3_256(_cbor_mod.dumps(hdr))  # type: ignore[attr-defined]

    pytest.skip("No way to hash Header (no helper and CBOR missing)")

def _hash_block(blk: Any) -> bytes:
    # Try methods on object
    for m in ("hash", "block_hash"):
        if hasattr(blk, m) and callable(getattr(blk, m)):
            out = getattr(blk, m)()
            if isinstance(out, (bytes, bytearray)):
                return bytes(out)
    if hasattr(blk, "hash") and isinstance(getattr(blk, "hash"), (bytes, bytearray)):
        return bytes(getattr(blk, "hash"))

    # Try free functions on the block module
    if _blk_mod:
        for fn_name in ("hash_block", "block_hash", "compute_hash", "calc_hash", "hash"):
            fn = getattr(_blk_mod, fn_name, None)
            if fn:
                out = fn(blk)  # type: ignore[misc]
                if isinstance(out, (bytes, bytearray)):
                    return bytes(out)

    # Fallback: canonical CBOR then sha3-256
    if _has_cbor_codec():
        return _sha3_256(_cbor_mod.dumps(blk))  # type: ignore[attr-defined]

    pytest.skip("No way to hash Block (no helper and CBOR missing)")

def _normalize(obj: Any) -> Any:
    """Turn objects into plain structures for equality assertions."""
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

_U32 = st.integers(min_value=0, max_value=2**32 - 1)
_U64 = st.integers(min_value=0, max_value=2**64 - 1)
_U128 = st.integers(min_value=0, max_value=2**128 - 1)

def _h256() -> st.SearchStrategy[bytes]:
    return st.binary(min_size=32, max_size=32)

def _h160_or_h256() -> st.SearchStrategy[bytes]:
    return st.one_of(st.binary(min_size=20, max_size=20), _h256())

def _maybe(t: st.SearchStrategy[Any]) -> st.SearchStrategy[Optional[Any]]:
    return st.one_of(st.none(), t)

def _tiny_list(t: st.SearchStrategy[Any]) -> st.SearchStrategy[list]:
    return st.lists(t, min_size=0, max_size=3)

def _infer_strategy_from_type(tp: Any) -> Optional[st.SearchStrategy[Any]]:
    origin = get_origin(tp)
    args = get_args(tp)
    if tp in (int, "int"):
        return _U128
    if tp in (bytes, "bytes", bytearray):
        return st.one_of(st.binary(min_size=0, max_size=256), _h256())
    if tp in (str, "str"):
        return st.from_regex(r"^(0x)?[0-9a-fA-F]{0,64}$")
    if origin is Optional or origin is type(Optional[bytes]):
        return _maybe(_infer_strategy_from_type(args[0]) or st.binary(min_size=0, max_size=64))
    if origin in (list, tuple):
        base = _infer_strategy_from_type(args[0]) if args else st.binary(min_size=0, max_size=64)
        return _tiny_list(base)
    return None

def _header_strategy_dataclass(HdrType: type) -> st.SearchStrategy[Any]:
    # Heuristic per-field builders keyed by common names
    try:
        fields = list(_dc.fields(HdrType))  # type: ignore[arg-type]
    except Exception:
        return st.nothing()

    kw: Dict[str, st.SearchStrategy[Any]] = {}
    for f in fields:
        name = f.name.lower()
        strat: Optional[st.SearchStrategy[Any]] = None

        if name in ("parent", "parenthash", "prevhash", "previoushash"):
            strat = _h256()
        elif name in ("state_root", "stateroot"):
            strat = _h256()
        elif name in ("tx_root", "transactionsroot", "txsroot", "txs_root"):
            strat = _h256()
        elif name in ("receipts_root", "receiptroot", "logs_root", "logsroot"):
            strat = _h256()
        elif name in ("proofs_root", "proofroot", "proots", "proofsroot"):
            strat = _h256()
        elif name in ("da_root", "daroot", "blobroot", "nmt_root", "nmtroot"):
            strat = _h256()
        elif name in ("mixseed", "mix_seed", "randao", "prev_randao"):
            strat = _h256()
        elif name in ("coinbase", "miner", "beneficiary"):
            strat = _h160_or_h256()
        elif name in ("height", "number"):
            strat = _U64
        elif name in ("timestamp", "time"):
            strat = _U64
        elif name in ("chainid", "chain_id"):
            strat = st.integers(min_value=1, max_value=2**31 - 1)
        elif name in ("theta", "theta_micro", "theta_micros"):
            strat = st.integers(min_value=0, max_value=10**12)
        elif name in ("nonce",):
            strat = st.binary(min_size=8, max_size=8)
        elif name in ("extra", "extra_data", "extra_data_bytes"):
            strat = st.binary(min_size=0, max_size=96)
        else:
            strat = _infer_strategy_from_type(f.type)

        if strat is None:
            strat = st.binary(min_size=0, max_size=64)
        kw[f.name] = strat
    return st.builds(HdrType, **kw)

def _arb_header_strategy() -> Tuple[Optional[type], st.SearchStrategy[Any]]:
    HdrType = _get_header_type()
    if HdrType is not None:
        try:
            from hypothesis.extra import dataclasses as hdataclasses  # type: ignore
            return HdrType, hdataclasses.from_type(HdrType)  # type: ignore
        except Exception:
            return HdrType, _header_strategy_dataclass(HdrType)
    # Fallback mapping for header-like dicts
    mapping = st.fixed_dictionaries(
        {
            "parentHash": _h256(),
            "stateRoot": _h256(),
            "txRoot": _h256(),
            "receiptsRoot": _h256(),
            "proofsRoot": _maybe(_h256()),
            "daRoot": _maybe(_h256()),
            "mixSeed": _h256(),
            "coinbase": _h160_or_h256(),
            "height": _U64,
            "timestamp": _U64,
            "chainId": st.integers(min_value=1, max_value=2**31 - 1),
            "nonce": st.binary(min_size=8, max_size=8),
            "extraData": st.binary(min_size=0, max_size=96),
            "theta": st.integers(min_value=0, max_value=10**12),
        }
    )
    return None, mapping

def _block_strategy_dataclass(BlkType: type, HdrType: Optional[type]) -> st.SearchStrategy[Any]:
    # Identify field names; build with nested header strategy when possible
    try:
        fields = list(_dc.fields(BlkType))  # type: ignore[arg-type]
    except Exception:
        return st.nothing()

    hdr_strat = _arb_header_strategy()[1] if HdrType is None else (
        _header_strategy_dataclass(HdrType)
    )

    kw: Dict[str, st.SearchStrategy[Any]] = {}
    for f in fields:
        name = f.name.lower()
        strat: Optional[st.SearchStrategy[Any]] = None
        if name == "header":
            strat = hdr_strat
        elif name in ("txs", "transactions"):
            # Keep small to avoid heavy object graphs
            strat = st.lists(st.binary(min_size=0, max_size=256), min_size=0, max_size=3)
        elif name in ("proofs",):
            strat = st.lists(st.binary(min_size=0, max_size=256), min_size=0, max_size=3)
        elif name in ("receipts",):
            strat = st.lists(st.fixed_dictionaries({
                "status": st.sampled_from([0, 1]),
                "gasUsed": _U64,
                "logs": st.lists(st.binary(min_size=0, max_size=64), min_size=0, max_size=2),
            }), min_size=0, max_size=2)
        else:
            strat = _infer_strategy_from_type(f.type) or st.binary(min_size=0, max_size=64)
        kw[f.name] = strat
    return st.builds(BlkType, **kw)

def _arb_block_strategy() -> Tuple[Optional[type], st.SearchStrategy[Any]]:
    BlkType = _get_block_type()
    HdrType = _get_header_type()
    if BlkType is not None:
        try:
            from hypothesis.extra import dataclasses as hdataclasses  # type: ignore
            return BlkType, hdataclasses.from_type(BlkType)  # type: ignore
        except Exception:
            return BlkType, _block_strategy_dataclass(BlkType, HdrType)
    # Fallback mapping for blocks (dict shape)
    _, hdr_strat = _arb_header_strategy()
    mapping = st.fixed_dictionaries(
        {
            "header": hdr_strat,
            "txs": st.lists(st.binary(min_size=0, max_size=256), min_size=0, max_size=3),
            "proofs": st.lists(st.binary(min_size=0, max_size=256), min_size=0, max_size=3),
            "receipts": st.lists(
                st.fixed_dictionaries(
                    {
                        "status": st.sampled_from([0, 1]),
                        "gasUsed": _U64,
                        "logs": st.lists(st.binary(min_size=0, max_size=64), min_size=0, max_size=2),
                    }
                ),
                min_size=0,
                max_size=2,
            ),
        }
    )
    return None, mapping


# ---- Tests: Header -----------------------------------------------------------

@pytest.mark.skipif(not (_has_header_codec() or _has_cbor_codec()), reason="Header codec not available")
@given(_arb_header_strategy()[1])
@settings(max_examples=200)
def test_header_roundtrip_and_hash_stability(hdr_any: Any):
    HdrType, _ = _arb_header_strategy()
    # Encode → decode
    b1 = _encode_bytes(hdr_any, kind="header")
    hdr2 = _decode_obj(b1, kind="header", T=HdrType)

    # Structural equality (normalized)
    assert _normalize(hdr_any) == _normalize(hdr2)

    # Hash stability before/after decode
    h1 = _hash_header(hdr_any)
    h2 = _hash_header(hdr2)
    assert isinstance(h1, (bytes, bytearray)) and isinstance(h2, (bytes, bytearray))
    assert h1 == h2, "Header hash must be stable across encode/decode"

    # Idempotent bytes (canonical encoding implied)
    b2 = _encode_bytes(hdr2, kind="header")
    assert b1 == b2, "Header CBOR must be canonical/idempotent"


# ---- Tests: Block ------------------------------------------------------------

@pytest.mark.skipif(not (_has_block_codec() or _has_cbor_codec()), reason="Block codec not available")
@given(_arb_block_strategy()[1])
@settings(max_examples=120)
def test_block_roundtrip_and_hash_stability(blk_any: Any):
    BlkType, _ = _arb_block_strategy()

    b1 = _encode_bytes(blk_any, kind="block")
    blk2 = _decode_obj(b1, kind="block", T=BlkType)

    assert _normalize(blk_any) == _normalize(blk2)

    hb1 = _hash_block(blk_any)
    hb2 = _hash_block(blk2)
    assert hb1 == hb2, "Block hash must be stable across encode/decode"

    b2 = _encode_bytes(blk2, kind="block")
    assert b1 == b2, "Block CBOR must be canonical/idempotent"


@pytest.mark.skipif(
    not ((_has_block_codec() or _has_cbor_codec()) and (_has_header_codec() or _has_cbor_codec())),
    reason="Header/Block codecs not available",
)
@given(_arb_block_strategy()[1])
@settings(max_examples=100)
def test_block_hash_matches_header_hash_when_applicable(blk_any: Any):
    """
    Many designs define block hash == header hash. If we can extract a header-like
    object from the block, assert equality. If not, skip gracefully.
    """
    # Extract header field if present
    hdr = None
    if isinstance(blk_any, dict) and "header" in blk_any:
        hdr = blk_any["header"]
    elif hasattr(blk_any, "header"):
        hdr = getattr(blk_any, "header")

    if hdr is None:
        pytest.skip("Block has no accessible 'header' field")

    bh = _hash_block(blk_any)
    hh = _hash_header(hdr)
    assert bh == hh, "By convention, Block hash should equal embedded Header hash"
