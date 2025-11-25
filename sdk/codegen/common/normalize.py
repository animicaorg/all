from __future__ import annotations

"""
ABI normalization & validation

This module transforms a raw ABI JSON object/string into a canonical,
language-agnostic IR consumed by all codegen backends. It performs:

- Schema validation (best-effort, optional if jsonschema is unavailable)
- Identifier sanitization
- Type normalization (strings/dicts → TypeRef)
- Stable signature construction
- Deterministic ordering for functions/events/errors
- Stable short discriminators for overloads
- Hashes:
    - topic_id for events (sha3-256 of signature)
    - abi_hash for the whole normalized surface

The returned object is an instance of AbiIR (from .model).
"""

import json
import re
import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

try:
    # Optional, used if available.
    import jsonschema  # type: ignore
except Exception:  # pragma: no cover - import is optional
    jsonschema = None  # type: ignore

from .model import (
    AbiIR,
    EventIR,
    ErrorIR,
    FunctionIR,
    Param,
    TypeRef,
)
from . import __version__ as IR_VERSION


# ----------------------------
# Public API & Error Handling
# ----------------------------

class AbiNormalizationError(Exception):
    """Raised when ABI normalization fails."""


def normalize_abi(
    abi: Union[str, Dict[str, Any]],
    *,
    validate_schema: bool = True,
) -> AbiIR:
    """
    Normalize a raw ABI (JSON string or dict) into an AbiIR.

    Args:
        abi: ABI as JSON string or already-parsed dict.
        validate_schema: If True, attempt to validate against
            sdk/common/schemas/abi.schema.json (best-effort).

    Returns:
        AbiIR instance with canonical, deterministic ordering and hashes.

    Raises:
        AbiNormalizationError: on invalid structure, identifiers or types.
    """
    raw = _load_json(abi)

    if validate_schema:
        _maybe_validate_schema(raw)

    # Extract arrays with lenient key names (compat with earlier drafts)
    fns_in = _expect_array(raw, ["functions", "methods"])
    evs_in = _expect_array(raw, ["events"])
    errs_in = _expect_array(raw, ["errors"])

    # Normalize functions
    fn_items: List[FunctionIR] = []
    for item in fns_in:
        fn_items.append(_normalize_function(item))

    # Normalize events
    ev_items: List[EventIR] = []
    for item in evs_in:
        ev_items.append(_normalize_event(item))

    # Normalize errors
    er_items: List[ErrorIR] = []
    for item in errs_in:
        er_items.append(_normalize_error(item))

    # Sort deterministically: (name, inputs_signature)
    fn_items.sort(key=lambda f: (f.name, _inputs_signature(f.inputs)))
    ev_items.sort(key=lambda e: (e.name, _inputs_signature(e.inputs)))
    er_items.sort(key=lambda e: (e.name, _inputs_signature(e.inputs)))

    # Compute overload discriminators (stable, short) where necessary
    _apply_overload_discriminators(fn_items)
    _apply_overload_discriminators(evv := ev_items)  # type: ignore
    _apply_overload_discriminators(err := er_items)  # type: ignore
    # (events/errors rarely overload by name+inputs, but rules are uniform)

    abi_ir = AbiIR(
        functions=fn_items,
        events=ev_items,
        errors=er_items,
        metadata={
            "ir_version": IR_VERSION,
        },
    )

    # Attach a stable abi_hash derived from the normalized surface
    abi_ir.metadata["abi_hash"] = compute_abi_hash(abi_ir)

    return abi_ir


def compute_abi_hash(abi_ir: Union[AbiIR, Dict[str, Any], str]) -> str:
    """
    Compute a stable sha3-256 hash (0x...) of the relevant ABI surface.

    Accepts:
      - an AbiIR instance,
      - a dict containing functions/events/errors (already normalized-like),
      - or a JSON string (will be parsed and minimally projected).
    """
    if isinstance(abi_ir, AbiIR):
        proj = _projection_for_hash(abi_ir)
    else:
        # tolerate dict/json input by projecting the same minimal view
        raw = _load_json(abi_ir)
        fns = _expect_array(raw, ["functions", "methods"])
        evs = _expect_array(raw, ["events"])
        ers = _expect_array(raw, ["errors"])
        proj = {
            "f": [_fn_hash_tuple_like(x) for x in fns],
            "e": [_ev_hash_tuple_like(x) for x in evs],
            "r": [_er_hash_tuple_like(x) for x in ers],
        }

    payload = json.dumps(proj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "0x" + hashlib.sha3_256(payload).hexdigest()


# ---------------
# Normalization
# ---------------

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NON_ID_CHAR = re.compile(r"[^A-Za-z0-9_]")

def _sanitize_identifier(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise AbiNormalizationError("Empty identifier is not allowed")

    # Replace disallowed characters with underscore
    name = _NON_ID_CHAR.sub("_", name)
    # Ensure it doesn't start with a digit
    if not re.match(r"^[A-Za-z_]", name):
        name = "_" + name

    # Optionally compress consecutive underscores
    name = re.sub(r"_+", "_", name)

    return name


def _load_json(obj: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return json.loads(json.dumps(obj))  # deep copy & normalize ints/strs
    if isinstance(obj, str):
        # Heuristics: JSON string (starts with { or [) vs file path
        s = obj.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                val = json.loads(s)
            except json.JSONDecodeError as e:
                raise AbiNormalizationError(f"ABI JSON parse error: {e}") from e
            if not isinstance(val, dict):
                raise AbiNormalizationError("ABI top-level must be an object")
            return val
        # We avoid file IO by default to keep the function pure.
        raise AbiNormalizationError("Expected ABI object or JSON string, not a file path")
    raise AbiNormalizationError(f"Unsupported ABI input type: {type(obj)}")


def _maybe_validate_schema(raw: Dict[str, Any]) -> None:
    """Best-effort validation against sdk/common/schemas/abi.schema.json."""
    if jsonschema is None:
        # Soft skip if jsonschema not installed.
        return

    schema_path = (
        Path(__file__).resolve().parents[2] / "common" / "schemas" / "abi.schema.json"
    )
    try:
        with schema_path.open("r", encoding="utf-8") as f:
            schema = json.load(f)
    except Exception:
        # If schema is not present (e.g., in a trimmed build), skip strictly.
        return

    try:
        jsonschema.validate(raw, schema)  # type: ignore
    except Exception as e:  # pragma: no cover - depends on schema availability
        raise AbiNormalizationError(f"ABI schema validation failed: {e}") from e


def _expect_array(obj: Dict[str, Any], keys: Iterable[str]) -> List[Dict[str, Any]]:
    for k in keys:
        if k in obj:
            v = obj[k]
            if v is None:
                return []
            if isinstance(v, list):
                # Ensure each element is an object
                out: List[Dict[str, Any]] = []
                for i, el in enumerate(v):
                    if not isinstance(el, dict):
                        raise AbiNormalizationError(f"Expected object in {k}[{i}]")
                    out.append(el)
                return out
            raise AbiNormalizationError(f"Expected array for {k}")
    # Missing ⇒ empty
    return []


# ---- Type normalization ----

def _normalize_type(typ: Any) -> TypeRef:
    """
    Accept strings like "uint256", "bytes", "bytes32", "address", "bool",
    "uint256[]", "tuple", or dict forms:
      {"type": "array", "items": <type>, "length": 10?}
      {"type": "tuple", "items": [<type>, ...]}
      {"type": "uint", "bits": 256}
    Returns a TypeRef instance.
    """
    if isinstance(typ, str):
        return _normalize_type_from_string(typ.strip())

    if isinstance(typ, dict):
        t = (typ.get("type") or "").strip().lower()
        if not t:
            raise AbiNormalizationError("Type object missing 'type' field")

        if t in ("uint", "int"):
            bits = _coerce_int(typ.get("bits"), allow_none=True)
            return TypeRef(kind=t, bits=bits)

        if t in ("bytes", "fixedbytes"):
            bits = _coerce_int(typ.get("bits"), allow_none=True)
            # For bytesN, bits must be multiple of 8 and between 8..2048 (sanity)
            if bits is not None and (bits % 8 != 0 or bits <= 0):
                raise AbiNormalizationError(f"Invalid fixed bytes width: {bits}")
            return TypeRef(kind="bytes", bits=bits)

        if t == "bool":
            return TypeRef(kind="bool")

        if t == "address":
            return TypeRef(kind="address")

        if t == "array":
            items = typ.get("items")
            if items is None:
                raise AbiNormalizationError("Array type missing 'items'")
            length = _coerce_int(typ.get("length"), allow_none=True)
            elem = _normalize_type(items)
            return TypeRef(kind="array", array_item=elem, array_len=length)

        if t == "tuple":
            items = typ.get("items")
            if not isinstance(items, list):
                raise AbiNormalizationError("Tuple type requires list 'items'")
            elems = [_normalize_type(it) for it in items]
            return TypeRef(kind="tuple", tuple_elems=elems)

    raise AbiNormalizationError(f"Unsupported type form: {typ!r}")


def _normalize_type_from_string(s: str) -> TypeRef:
    # Array suffixes: type[], type[10]
    m = re.match(r"^(?P<base>[A-Za-z0-9]+)(\[(?P<len>\d*)\])+$", s)
    if m:
        # Re-parse iteratively for nested arrays
        base_and_dims = re.findall(r"([^\[\]]+)|\[(\d*)\]", s)
        # base_and_dims = list of tuples where only one of elements is set
        # Extract base
        base = None
        dims: List[Optional[int]] = []
        for a, b in base_and_dims:
            if a:
                base = a
            else:
                dims.append(int(b) if b != "" else None)
        if base is None:
            raise AbiNormalizationError(f"Malformed array type: {s}")

        ref = _normalize_type_from_string(base)
        for dim in dims:
            ref = TypeRef(kind="array", array_item=ref, array_len=dim)
        return ref

    s_low = s.lower()

    # uint / int with optional bits
    um = re.match(r"^(u?int)(\d+)?$", s_low)
    if um:
        kind = "uint" if um.group(1).startswith("u") else "int"
        bits = int(um.group(2)) if um.group(2) else None
        if bits is not None and bits <= 0:
            raise AbiNormalizationError(f"Invalid integer width: {bits}")
        return TypeRef(kind=kind, bits=bits)

    # bytes / bytesN
    bm = re.match(r"^bytes(\d+)?$", s_low)
    if bm:
        n = int(bm.group(1)) if bm.group(1) else None
        if n is not None and (n <= 0 or n > 256):
            raise AbiNormalizationError(f"Invalid bytesN width: {n}")
        bits = n * 8 if n is not None else None
        return TypeRef(kind="bytes", bits=bits)

    if s_low in ("bool", "address", "string"):
        # 'string' is allowed in some ABIs; encode as bytes in wire-layer later
        mapped = "string" if s_low == "string" else s_low
        return TypeRef(kind=mapped)

    if s_low == "tuple":
        # Without explicit items we can't build a tuple; reject
        raise AbiNormalizationError("Bare 'tuple' type requires explicit items[]")

    raise AbiNormalizationError(f"Unknown type: {s}")


def _coerce_int(v: Any, *, allow_none: bool = False) -> Optional[int]:
    if v is None:
        if allow_none:
            return None
        raise AbiNormalizationError("Expected integer, got None")
    if isinstance(v, bool):
        raise AbiNormalizationError("Expected integer, got bool")
    try:
        iv = int(v)
    except Exception as e:
        raise AbiNormalizationError(f"Expected integer, got {v!r}") from e
    return iv


def _canonical_type_str(t: TypeRef) -> str:
    """Return a canonical signature string for a TypeRef."""
    if t.kind in ("uint", "int"):
        return f"{t.kind}{t.bits or ''}"
    if t.kind == "bytes":
        if t.bits:
            return f"bytes{t.bits // 8}"
        return "bytes"
    if t.kind in ("bool", "address", "string"):
        return t.kind
    if t.kind == "array":
        inner = _canonical_type_str(_assert_not_none(t.array_item, "array_item"))
        # Note: dynamic array '[]' vs fixed '[N]'
        suffix = f"[{t.array_len}]" if t.array_len is not None else "[]"
        return f"{inner}{suffix}"
    if t.kind == "tuple":
        elems = ",".join(_canonical_type_str(e) for e in (t.tuple_elems or []))
        return f"({elems})"
    raise AbiNormalizationError(f"Unsupported TypeRef kind: {t.kind}")


def _assert_not_none(x: Optional[Any], label: str) -> Any:
    if x is None:
        raise AbiNormalizationError(f"Internal error: {label} is None")
    return x


# ---- Functions, Events, Errors ----

def _normalize_params(params_in: Any, *, allow_indexed: bool = False) -> List[Param]:
    if params_in is None:
        return []
    if not isinstance(params_in, list):
        raise AbiNormalizationError("params must be a list")
    out: List[Param] = []
    for i, p in enumerate(params_in):
        if not isinstance(p, dict):
            raise AbiNormalizationError(f"param[{i}] must be an object")
        name = _sanitize_identifier(str(p.get("name", f"arg{i}")))
        typ = _normalize_type(p.get("type"))
        indexed = bool(p.get("indexed")) if allow_indexed else False
        out.append(Param(name=name, type=typ, indexed=indexed))
    return out


def _inputs_signature(params: List[Param]) -> str:
    return "(" + ",".join(_canonical_type_str(p.type) for p in params) + ")"


def _compute_signature(name: str, inputs: List[Param]) -> str:
    return f"{name}{_inputs_signature(inputs)}"


def _sha3_hex(data: bytes) -> str:
    return "0x" + hashlib.sha3_256(data).hexdigest()


def _short_discriminator(signature: str) -> str:
    # 6-8 hex chars are usually enough and stable.
    return hashlib.sha3_256(signature.encode("utf-8")).hexdigest()[:8]


def _normalize_function(item: Dict[str, Any]) -> FunctionIR:
    name_raw = item.get("name")
    if not isinstance(name_raw, str):
        raise AbiNormalizationError("function.name must be a string")
    name = _sanitize_identifier(name_raw)

    inputs = _normalize_params(item.get("inputs"))
    outputs_raw = item.get("outputs")
    outputs: List[TypeRef] = []
    if outputs_raw is not None:
        if not isinstance(outputs_raw, list):
            raise AbiNormalizationError("function.outputs must be a list")
        outputs = [_normalize_type(t) for t in outputs_raw]

    mut = str(item.get("stateMutability", item.get("mutability", "nonpayable"))).lower()
    if mut not in ("pure", "view", "nonpayable", "payable"):
        mut = "nonpayable"

    sig = _compute_signature(name, inputs)
    selector = _sha3_hex(sig.encode("utf-8"))

    return FunctionIR(
        name=name,
        inputs=inputs,
        outputs=outputs,
        state_mutability=mut,
        signature=sig,
        selector=selector,
        discriminator=None,  # added in overload pass if needed
    )


def _normalize_event(item: Dict[str, Any]) -> EventIR:
    name_raw = item.get("name")
    if not isinstance(name_raw, str):
        raise AbiNormalizationError("event.name must be a string")
    name = _sanitize_identifier(name_raw)

    inputs = _normalize_params(item.get("inputs"), allow_indexed=True)
    anonymous = bool(item.get("anonymous", False))
    sig = _compute_signature(name, inputs)
    topic_id = _sha3_hex(sig.encode("utf-8"))

    return EventIR(
        name=name,
        inputs=inputs,
        anonymous=anonymous,
        signature=sig,
        topic_id=topic_id,
        discriminator=None,  # added if ever needed due to name+inputs collision
    )


def _normalize_error(item: Dict[str, Any]) -> ErrorIR:
    name_raw = item.get("name")
    if not isinstance(name_raw, str):
        raise AbiNormalizationError("error.name must be a string")
    name = _sanitize_identifier(name_raw)
    inputs = _normalize_params(item.get("inputs"))
    sig = _compute_signature(name, inputs)
    selector = _sha3_hex(sig.encode("utf-8"))
    return ErrorIR(
        name=name,
        inputs=inputs,
        signature=sig,
        selector=selector,
        discriminator=None,
    )


def _apply_overload_discriminators(items: List[Any]) -> None:
    """
    For a list of FunctionIR/EventIR/ErrorIR, detect groups with identical
    (name, inputs_signature) collisions (rare) or simple same-name overloads,
    and assign a short stable discriminator derived from the full signature.

    The discriminator is intended for language backends that need a stable
    suffix to disambiguate symbols in emitted code.
    """
    # Group by simple name
    buckets: Dict[str, List[Any]] = {}
    for it in items:
        buckets.setdefault(it.name, []).append(it)

    for name, group in buckets.items():
        if len(group) <= 1:
            continue
        # Multiple with same name: assign discriminators based on signature
        seen = set()
        for it in group:
            # If two entries have *identical* signatures, this is a duplicate;
            # we'll still assign discriminators but keep relative order stable.
            disc = _short_discriminator(it.signature)
            # Ensure uniqueness across pathological identical sigs by extending
            while disc in seen:
                disc = _short_discriminator(disc + it.signature)
            seen.add(disc)
            it.discriminator = disc


# ---- Hash projection helpers (deterministic & minimal) ----

def _fn_hash_tuple_like(fn: Dict[str, Any]) -> Tuple[str, Tuple[str, ...], Tuple[str, ...]]:
    name = _sanitize_identifier(str(fn.get("name", "")))
    ins = _expect_array(fn, ["inputs"])
    outs = fn.get("outputs") or []
    return (
        name,
        tuple(_canonical_type_str(_normalize_type(p.get("type"))) for p in ins),
        tuple(_canonical_type_str(_normalize_type(t)) for t in outs) if isinstance(outs, list) else tuple(),
    )


def _ev_hash_tuple_like(ev: Dict[str, Any]) -> Tuple[str, Tuple[str, ...]]:
    name = _sanitize_identifier(str(ev.get("name", "")))
    ins = _expect_array(ev, ["inputs"])
    return (
        name,
        tuple(_canonical_type_str(_normalize_type(p.get("type"))) for p in ins),
    )


def _er_hash_tuple_like(er: Dict[str, Any]) -> Tuple[str, Tuple[str, ...]]:
    name = _sanitize_identifier(str(er.get("name", "")))
    ins = _expect_array(er, ["inputs"])
    return (
        name,
        tuple(_canonical_type_str(_normalize_type(p.get("type"))) for p in ins),
    )


def _projection_for_hash(abi_ir: AbiIR) -> Dict[str, Any]:
    # Convert to a compact projection (tuples in arrays) to avoid noise.
    f = []
    for fn in abi_ir.functions:
        f.append(
            (
                fn.name,
                tuple(_canonical_type_str(p.type) for p in fn.inputs),
                tuple(_canonical_type_str(t) for t in fn.outputs),
            )
        )
    e = []
    for ev in abi_ir.events:
        e.append((ev.name, tuple(_canonical_type_str(p.type) for p in ev.inputs)))
    r = []
    for er in abi_ir.errors:
        r.append((er.name, tuple(_canonical_type_str(p.type) for p in er.inputs)))
    return {"f": f, "e": e, "r": r, "v": IR_VERSION}


