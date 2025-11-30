from __future__ import annotations

"""
ABI datatypes & validation (Python SDK)

This module defines:
- TypedDict shapes for ABI entries (functions/events/parameters)
- A small validator/normalizer for ABI objects
- Helpers to compute canonical signatures/selectors/topics

We intentionally avoid a heavy JSON-Schema dependency to keep the SDK slim.
Validation here is structural and type-string–aware.
"""

import re
from dataclasses import dataclass
from typing import (Any, Dict, List, Literal, Optional, Sequence, Tuple,
                    TypedDict, Union)

try:
    from omni_sdk.errors import AbiError
except Exception:  # pragma: no cover

    class AbiError(ValueError):
        pass


# --- Type-string parsing -----------------------------------------------------

# Supported base types (kept in-sync with vm & codegen minimal set)
_BASE_TYPES = {
    # integers
    **{f"u{b}": True for b in (8, 16, 32, 64, 128, 256)},
    **{f"i{b}": True for b in (8, 16, 32, 64, 128, 256)},
    # misc scalars
    "bool": True,
    "address": True,  # bech32m string at the wire level
    "hash": True,  # 32-byte hex
    "bytes": True,  # dynamic
    "string": True,  # utf-8
    # fixed-size bytes
    **{f"bytes{n}": True for n in range(1, 33)},
}

_TUPLE_RE = re.compile(r"^\((.*)\)$")
_ARRAY_SUFFIX_RE = re.compile(r"(\[\]|\[\d+\])$")


def canonical_type(type_str: str) -> str:
    """Normalize an ABI type string: lowercase, strip spaces, collapse array suffixes."""
    s = type_str.strip().lower()
    s = re.sub(r"\s+", "", s)
    # quick validation pass by parsing
    _parse_type(s)  # will raise if invalid
    return s


def is_valid_type(type_str: str) -> bool:
    try:
        canonical_type(type_str)
        return True
    except AbiError:
        return False


def _split_top_level_commas(s: str) -> List[str]:
    """Split on commas but ignore commas inside nested tuples/arrays."""
    out: List[str] = []
    depth = 0
    buf: List[str] = []
    for ch in s:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise AbiError("Unbalanced parentheses in tuple type")
            buf.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return out


def _parse_tuple(inner: str) -> Tuple[str, ...]:
    if not inner:
        return tuple()
    elems = _split_top_level_commas(inner)
    return tuple(canonical_type(e) for e in elems)


def _peel_array_suffixes(t: str) -> Tuple[str, List[Optional[int]]]:
    """Return (base, dims) where dims is list of sizes or None for dynamic."""
    dims: List[Optional[int]] = []
    while True:
        m = _ARRAY_SUFFIX_RE.search(t)
        if not m:
            break
        suffix = m.group(1)
        t = t[: -len(suffix)]
        if suffix == "[]":
            dims.append(None)
        else:
            size = int(suffix[1:-1])
            if size <= 0:
                raise AbiError("Fixed array dimension must be positive")
            dims.append(size)
    return t, dims


def _parse_type(
    type_str: str,
) -> Union[str, Tuple[str, Tuple[Union[str, Tuple], ...], Tuple[Optional[int], ...]]]:
    """
    Parse a type string into a structured form:
      - base scalar like "u256"
      - or ("tuple", (elem1, elem2, ...), dims) where dims is array dims applied to the tuple
      - or ("array", base_scalar, dims) for scalar arrays
    Will raise AbiError for unsupported shapes.
    """
    t, dims = _peel_array_suffixes(type_str)
    # Tuple?
    m = _TUPLE_RE.match(t)
    if m:
        elems = _parse_tuple(m.group(1))
        return ("tuple", elems, tuple(dims))
    # Scalar?
    if t not in _BASE_TYPES:
        raise AbiError(f"Unsupported base type: {t}")
    if dims:
        return ("array", t, tuple(dims))
    return t


# --- ABI shapes --------------------------------------------------------------


class AbiParam(TypedDict, total=False):
    name: str
    type: str
    indexed: bool  # only meaningful for event inputs


class AbiFunction(TypedDict, total=False):
    type: Literal["function"]
    name: str
    inputs: List[AbiParam]
    outputs: List[AbiParam]
    stateMutability: Literal["view", "pure", "nonpayable", "payable"]


class AbiEvent(TypedDict, total=False):
    type: Literal["event"]
    name: str
    inputs: List[AbiParam]
    anonymous: bool


AbiEntry = Union[AbiFunction, AbiEvent]
Abi = List[AbiEntry]


# --- Validation & normalization ---------------------------------------------


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise AbiError(msg)


def _validate_param(p: Dict[str, Any], ctx: str) -> AbiParam:
    _require(isinstance(p, dict), f"{ctx}: parameter must be an object")
    name = p.get("name", "")
    _require(isinstance(name, str), f"{ctx}: param.name must be string")
    typ = p.get("type")
    _require(isinstance(typ, str), f"{ctx}: param.type must be string")
    ctyp = canonical_type(typ)
    indexed = bool(p.get("indexed", False))
    return {
        "name": name,
        "type": ctyp,
        **({"indexed": indexed} if "indexed" in p else {}),
    }


def _validate_fn(e: Dict[str, Any]) -> AbiFunction:
    name = e.get("name")
    _require(isinstance(name, str) and name, "function.name must be non-empty string")
    inputs = e.get("inputs", [])
    outputs = e.get("outputs", [])
    _require(isinstance(inputs, list), "function.inputs must be a list")
    _require(isinstance(outputs, list), "function.outputs must be a list")
    mut = e.get("stateMutability", "nonpayable")
    _require(
        mut in ("view", "pure", "nonpayable", "payable"),
        "function.stateMutability invalid",
    )

    v_inputs = [_validate_param(p, f"function {name} input") for p in inputs]
    v_outputs = [_validate_param(p, f"function {name} output") for p in outputs]
    return {
        "type": "function",
        "name": name,
        "inputs": v_inputs,
        "outputs": v_outputs,
        "stateMutability": mut,  # type: ignore[typeddict-item]
    }


def _validate_event(e: Dict[str, Any]) -> AbiEvent:
    name = e.get("name")
    _require(isinstance(name, str) and name, "event.name must be non-empty string")
    inputs = e.get("inputs", [])
    _require(isinstance(inputs, list), "event.inputs must be a list")
    v_inputs = [_validate_param(p, f"event {name} input") for p in inputs]
    anonymous = bool(e.get("anonymous", False))
    return {
        "type": "event",
        "name": name,
        "inputs": v_inputs,
        "anonymous": anonymous,  # type: ignore[typeddict-item]
    }


def validate_abi(abi: Any) -> Abi:
    """
    Validate and normalize an ABI value (list of entries).
    - Ensures structure is correct
    - Canonicalizes all type strings
    - Enforces unique (entry_type, name) for functions/events
    Returns a new normalized list (does not mutate input).
    """
    _require(isinstance(abi, list), "ABI must be a list of entries")
    out: List[AbiEntry] = []
    seen: set[Tuple[str, str]] = set()
    for i, raw in enumerate(abi):
        _require(isinstance(raw, dict), f"ABI entry at index {i} must be an object")
        etype = raw.get("type")
        _require(etype in ("function", "event"), f"Unsupported ABI entry type: {etype}")
        if etype == "function":
            v = _validate_fn(raw)
        else:
            v = _validate_event(raw)
        key = (v["type"], v["name"])
        _require(key not in seen, f"Duplicate ABI entry: {key}")
        seen.add(key)
        out.append(v)
    return out


# --- Signatures, selectors, topics ------------------------------------------

try:
    from omni_sdk.utils.hash import keccak256 as _keccak256  # preferred
except Exception:  # pragma: no cover
    _keccak256 = None

try:
    from omni_sdk.utils.hash import sha3_256 as _sha3_256
except Exception:  # pragma: no cover
    import hashlib

    def _sha3_256(data: bytes) -> bytes:
        return hashlib.sha3_256(data).digest()


def _hash_sig(sig: str) -> bytes:
    data = sig.encode("utf-8")
    if _keccak256:
        return _keccak256(data)
    return _sha3_256(data)


def canonical_fn_signature(name: str, inputs: Sequence[AbiParam]) -> str:
    """e.g., transfer(address,u256)"""
    in_types = ",".join(canonical_type(p["type"]) for p in inputs)
    return f"{name}({in_types})"


def function_selector(fn: Union[AbiFunction, Tuple[str, Sequence[AbiParam]]]) -> bytes:
    """First 4 bytes of hash(signature)."""
    if isinstance(fn, tuple):
        name, inputs = fn
    else:
        name, inputs = fn["name"], fn.get("inputs", [])
    sig = canonical_fn_signature(name, inputs)
    return _hash_sig(sig)[:4]


def event_topic(ev: Union[AbiEvent, Tuple[str, Sequence[AbiParam]]]) -> bytes:
    """Full 32-byte hash of event signature."""
    if isinstance(ev, tuple):
        name, inputs = ev
    else:
        name, inputs = ev["name"], ev.get("inputs", [])
    # For canonical signature, include all input types (indexed/non-indexed)
    types = ",".join(canonical_type(p["type"]) for p in inputs)
    sig = f"{name}({types})"
    return _hash_sig(sig)


# --- Convenience model -------------------------------------------------------


@dataclass(frozen=True)
class AbiModel:
    functions: Dict[str, AbiFunction]
    events: Dict[str, AbiEvent]

    @staticmethod
    def from_list(entries: Abi) -> "AbiModel":
        v = validate_abi(entries)
        fns: Dict[str, AbiFunction] = {}
        evs: Dict[str, AbiEvent] = {}
        for e in v:
            if e["type"] == "function":
                fns[e["name"]] = e  # last-one-wins prevented by validate_abi
            else:
                evs[e["name"]] = e
        return AbiModel(functions=fns, events=evs)

    def get_function(self, name: str) -> AbiFunction:
        try:
            return self.functions[name]
        except KeyError:
            raise AbiError(f"Function not found in ABI: {name}")

    def get_event(self, name: str) -> AbiEvent:
        try:
            return self.events[name]
        except KeyError:
            raise AbiError(f"Event not found in ABI: {name}")


__all__ = [
    "AbiParam",
    "AbiFunction",
    "AbiEvent",
    "AbiEntry",
    "Abi",
    "AbiModel",
    "validate_abi",
    "canonical_type",
    "is_valid_type",
    "canonical_fn_signature",
    "function_selector",
    "event_topic",
]
