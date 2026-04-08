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
import hashlib
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
    **{f"uint{b}": True for b in (8, 16, 32, 64, 128, 256)},
    **{f"int{b}": True for b in (8, 16, 32, 64, 128, 256)},
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
_UINT_ALIAS_RE = re.compile(r"^u(\d+)$")
_INT_ALIAS_RE = re.compile(r"^i(\d+)$")


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


def _normalize_base_type(t: str) -> str:
    if t == "uint":
        return "uint256"
    if t == "int":
        return "int256"
    match = _UINT_ALIAS_RE.match(t)
    if match:
        return f"uint{match.group(1)}"
    match = _INT_ALIAS_RE.match(t)
    if match:
        return f"int{match.group(1)}"
    return t


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
    t = _normalize_base_type(t)
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
    """First 8 bytes of the ABI v1 selector domain hash."""
    if isinstance(fn, tuple):
        name, inputs = fn
    else:
        name, inputs = fn["name"], fn.get("inputs", [])
    sig = canonical_fn_signature(name, inputs)
    return hashlib.sha3_256(f"animica:abi:v1|{sig}".encode("utf-8")).digest()[:8]


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


def normalize_abi(abi: Any) -> Dict[str, Any]:
    if isinstance(abi, dict) and {"entries", "functions", "events"} <= set(abi.keys()):
        return abi
    entries = abi.get("abi") if isinstance(abi, dict) and "abi" in abi else abi
    validated = validate_abi(entries)
    model = AbiModel.from_list(validated)
    return {
        "entries": validated,
        "functions": model.functions,
        "events": model.events,
    }


def _uvarint_encode(value: int) -> bytes:
    try:
        from omni_sdk.utils.bytes import uvarint_encode
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("omni_sdk.utils.bytes.uvarint_encode is required") from exc
    return uvarint_encode(int(value))


def _uvarint_decode(buf: bytes, offset: int = 0) -> Tuple[int, int]:
    try:
        from omni_sdk.utils.bytes import uvarint_decode
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("omni_sdk.utils.bytes.uvarint_decode is required") from exc
    value, consumed = uvarint_decode(buf, offset=offset)
    return value, offset + consumed


def _minimal_unsigned_bytes(value: int) -> bytes:
    if value < 0:
        raise AbiError("unsigned integer cannot be negative")
    if value == 0:
        return b"\x00"
    length = (value.bit_length() + 7) // 8
    return value.to_bytes(length, "big", signed=False)


def _require_bytes(value: Any, *, fixed_len: int | None = None) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
    elif isinstance(value, str):
        text = value[2:] if value.startswith(("0x", "0X")) else value
        try:
            data = bytes.fromhex(text)
        except ValueError as exc:
            raise AbiError("expected bytes or 0x-hex string") from exc
    else:
        raise AbiError("expected bytes or 0x-hex string")
    if fixed_len is not None and len(data) != fixed_len:
        raise AbiError(f"expected {fixed_len} bytes, got {len(data)}")
    return data


def _encode_scalar(value: Any, typ: str) -> bytes:
    if typ.startswith("uint"):
        bits = int(typ[4:])
        if not isinstance(value, int):
            raise AbiError(f"{typ} requires an integer value")
        max_value = (1 << bits) - 1
        if value < 0 or value > max_value:
            raise AbiError(f"{typ} out of range")
        mag = _minimal_unsigned_bytes(value)
        return _uvarint_encode(len(mag)) + mag

    if typ.startswith("int"):
        bits = int(typ[3:])
        if not isinstance(value, int):
            raise AbiError(f"{typ} requires an integer value")
        min_value = -(1 << (bits - 1))
        max_value = (1 << (bits - 1)) - 1
        if value < min_value or value > max_value:
            raise AbiError(f"{typ} out of range")
        payload = bytes([0x00 if value >= 0 else 0x01]) + _minimal_unsigned_bytes(abs(value))
        return _uvarint_encode(len(payload)) + payload

    if typ == "bool":
        if not isinstance(value, bool):
            raise AbiError("bool requires a boolean value")
        return b"\x01" if value else b"\x00"

    if typ == "address":
        if not isinstance(value, str) or not value:
            raise AbiError("address requires a bech32m string")
        encoded = value.encode("utf-8")
        return _uvarint_encode(len(encoded)) + encoded

    if typ == "string":
        if not isinstance(value, str):
            raise AbiError("string requires a text value")
        encoded = value.encode("utf-8")
        return _uvarint_encode(len(encoded)) + encoded

    if typ == "bytes":
        data = _require_bytes(value)
        return _uvarint_encode(len(data)) + data

    if typ == "hash":
        return _require_bytes(value, fixed_len=32)

    if typ.startswith("bytes"):
        fixed_len = int(typ[5:])
        return _require_bytes(value, fixed_len=fixed_len)

    raise AbiError(f"unsupported ABI scalar type: {typ}")


def _encode_desc(value: Any, desc: Any) -> bytes:
    if isinstance(desc, str):
        return _encode_scalar(value, desc)

    kind = desc[0]
    if kind == "array":
        _, element_desc, dims = desc
        if not isinstance(value, (list, tuple)):
            raise AbiError("array argument must be a list or tuple")
        if dims and dims[0] is not None and len(value) != dims[0]:
            raise AbiError(f"array length mismatch: expected {dims[0]}, got {len(value)}")
        encoded_items = []
        next_desc = element_desc if len(dims) == 1 else ("array", element_desc, dims[1:])
        for item in value:
            encoded_items.append(_encode_desc(item, next_desc))
        return _uvarint_encode(len(value)) + b"".join(encoded_items)

    if kind == "tuple":
        _, elements, dims = desc
        if dims:
            return _encode_desc(value, ("array", ("tuple", elements, ()), dims))
        if not isinstance(value, (list, tuple)):
            raise AbiError("tuple argument must be a list or tuple")
        if len(value) != len(elements):
            raise AbiError(f"tuple length mismatch: expected {len(elements)}, got {len(value)}")
        encoded = [_uvarint_encode(len(elements))]
        for item, element_type in zip(value, elements):
            encoded.append(_encode_desc(item, _parse_type(element_type)))
        return b"".join(encoded)

    raise AbiError(f"unsupported ABI descriptor: {desc!r}")


def encode_call(abi: Any, fn_name: str, args: Sequence[Any]) -> bytes:
    normalized = normalize_abi(abi)
    functions = normalized["functions"]
    if fn_name not in functions:
        raise AbiError(f"function not found in ABI: {fn_name}")
    fn = functions[fn_name]
    inputs = fn.get("inputs", [])
    if len(inputs) != len(args):
        raise AbiError(f"{fn_name} expects {len(inputs)} argument(s), got {len(args)}")
    encoded = [function_selector(fn), _uvarint_encode(len(inputs))]
    for arg, param in zip(args, inputs):
        encoded.append(_encode_desc(arg, _parse_type(param["type"])))
    return b"".join(encoded)


def _read_exact(buf: bytes, offset: int, length: int) -> Tuple[bytes, int]:
    end = offset + length
    if end > len(buf):
        raise AbiError("truncated ABI payload")
    return buf[offset:end], end


def _decode_scalar(buf: bytes, typ: str, offset: int) -> Tuple[Any, int]:
    if typ.startswith("uint"):
        length, offset = _uvarint_decode(buf, offset)
        payload, offset = _read_exact(buf, offset, length)
        return int.from_bytes(payload, "big", signed=False), offset

    if typ.startswith("int"):
        length, offset = _uvarint_decode(buf, offset)
        payload, offset = _read_exact(buf, offset, length)
        if not payload:
            raise AbiError("truncated signed integer payload")
        magnitude = int.from_bytes(payload[1:] or b"\x00", "big", signed=False)
        return (-magnitude if payload[0] == 0x01 else magnitude), offset

    if typ == "bool":
        payload, offset = _read_exact(buf, offset, 1)
        if payload not in (b"\x00", b"\x01"):
            raise AbiError("invalid boolean value")
        return payload == b"\x01", offset

    if typ in {"address", "string", "bytes"}:
        length, offset = _uvarint_decode(buf, offset)
        payload, offset = _read_exact(buf, offset, length)
        if typ == "address" or typ == "string":
            return payload.decode("utf-8"), offset
        return payload, offset

    if typ == "hash":
        payload, offset = _read_exact(buf, offset, 32)
        return "0x" + payload.hex(), offset

    if typ.startswith("bytes"):
        fixed_len = int(typ[5:])
        payload, offset = _read_exact(buf, offset, fixed_len)
        return "0x" + payload.hex(), offset

    raise AbiError(f"unsupported ABI scalar type: {typ}")


def _decode_desc(buf: bytes, desc: Any, offset: int) -> Tuple[Any, int]:
    if isinstance(desc, str):
        return _decode_scalar(buf, desc, offset)

    kind = desc[0]
    if kind == "array":
        _, element_desc, dims = desc
        count, offset = _uvarint_decode(buf, offset)
        if dims and dims[0] is not None and count != dims[0]:
            raise AbiError(f"array length mismatch: expected {dims[0]}, got {count}")
        next_desc = element_desc if len(dims) == 1 else ("array", element_desc, dims[1:])
        items = []
        for _ in range(count):
            item, offset = _decode_desc(buf, next_desc, offset)
            items.append(item)
        return items, offset

    if kind == "tuple":
        _, elements, dims = desc
        if dims:
            return _decode_desc(buf, ("array", ("tuple", elements, ()), dims), offset)
        count, offset = _uvarint_decode(buf, offset)
        if count != len(elements):
            raise AbiError(f"tuple length mismatch: expected {len(elements)}, got {count}")
        values = []
        for element_type in elements:
            value, offset = _decode_desc(buf, _parse_type(element_type), offset)
            values.append(value)
        return values, offset

    raise AbiError(f"unsupported ABI descriptor: {desc!r}")


def decode_return(abi: Any, fn_name: str, data: bytes) -> Any:
    normalized = normalize_abi(abi)
    functions = normalized["functions"]
    if fn_name not in functions:
        raise AbiError(f"function not found in ABI: {fn_name}")
    outputs = functions[fn_name].get("outputs", [])
    if not outputs and data in (b"", b"\x00"):
        return None

    count, offset = _uvarint_decode(data, 0)
    if count != len(outputs):
        raise AbiError(f"return value count mismatch: expected {len(outputs)}, got {count}")
    values = []
    for output in outputs:
        value, offset = _decode_desc(data, _parse_type(output["type"]), offset)
        values.append(value)
    if offset != len(data):
        raise AbiError("unexpected trailing bytes in return payload")
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return values


def event_selector(ev: Union[AbiEvent, Tuple[str, Sequence[AbiParam]]]) -> bytes:
    if isinstance(ev, tuple):
        name, inputs = ev
    else:
        name, inputs = ev["name"], ev.get("inputs", [])
    signature = f"{name}(" + ",".join(canonical_type(p["type"]) for p in inputs) + ")"
    return hashlib.sha3_256(signature.encode("utf-8")).digest()


def _decode_indexed_topic(topic: bytes, typ: str) -> Any:
    if typ.startswith("uint"):
        return int.from_bytes(topic[-32:], "big", signed=False)
    if typ.startswith("int"):
        value = int.from_bytes(topic[-32:], "big", signed=False)
        bits = int(typ[3:])
        if value >= (1 << (bits - 1)):
            value -= 1 << bits
        return value
    if typ == "bool":
        return topic[-1] != 0
    return "0x" + topic.hex()


def decode_event(*args: Any) -> Dict[str, Any]:
    if len(args) == 4:
        abi, name, topics, data = args
        normalized = normalize_abi(abi)
        event_def = normalized["events"].get(name)
        if event_def is None:
            raise AbiError(f"event not found in ABI: {name}")
    elif len(args) == 3:
        event_def, topics, data = args
        name = event_def.get("name", "")
    else:  # pragma: no cover - defensive
        raise TypeError("decode_event expects (abi, name, topics, data) or (event_def, topics, data)")

    if not isinstance(event_def, dict):
        raise AbiError("event definition must be a mapping")

    topics_b = [bytes(topic) for topic in topics]
    data_b = bytes(data)
    topic_index = 0 if bool(event_def.get("anonymous", False)) else 1
    offset = 0
    decoded: Dict[str, Any] = {}

    for index, param in enumerate(event_def.get("inputs", [])):
        param_name = str(param.get("name") or index)
        param_type = canonical_type(str(param.get("type") or "bytes"))
        if bool(param.get("indexed", False)):
            if topic_index >= len(topics_b):
                raise AbiError("not enough indexed topics to decode event")
            decoded[param_name] = _decode_indexed_topic(topics_b[topic_index], param_type)
            topic_index += 1
        else:
            decoded[param_name], offset = _decode_desc(data_b, _parse_type(param_type), offset)

    return decoded


__all__ = [
    "AbiParam",
    "AbiFunction",
    "AbiEvent",
    "AbiEntry",
    "Abi",
    "AbiModel",
    "validate_abi",
    "normalize_abi",
    "encode_call",
    "decode_return",
    "event_selector",
    "decode_event",
    "canonical_type",
    "is_valid_type",
    "canonical_fn_signature",
    "function_selector",
    "event_topic",
]
