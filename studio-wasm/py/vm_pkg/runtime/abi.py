from __future__ import annotations

"""
abi — call dispatch, arg/return encode/decode for the browser simulator.

Scope
-----
This module provides a small, deterministic ABI layer used by the in-browser
Python VM subset. It validates arguments according to a minimal type system
and converts between JSON-friendly transport values and VM-native Python types.

Supported scalar types (by name):
  - "u256"      : unsigned 256-bit integer
  - "bool"      : boolean
  - "bytes"     : arbitrary-length byte string (bounded by MAX_BYTES)
  - "address"   : 32-byte address (raw bytes in the VM)

JSON transport conventions:
  - u256  → "0x" hex string (lowercase, no leading zeros except "0x0")
  - bytes → "0x" hex string
  - bool  → JSON true/false
  - address → "0x" + 64 hex chars (32 bytes)

Why hex strings for integers?
  - Browsers/JS cannot losslessly represent 256-bit integers as numbers.
    Using "0x.." avoids precision loss and keeps deterministic formatting.

Dispatch
--------
AbiSchema indexes a manifest-like ABI with functions:
  {"name": str, "inputs": [{"name": str, "type": str}, ...],
   "outputs": [{"name": str, "type": str}]}

AbiDispatcher can:
  - validate & decode inputs from JSON → native
  - encode outputs from native → JSON

Note: The simulator's execution engine is responsible for actually invoking
contract code. This module focuses on (de)serialization and validation.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

# Use the trimmed ValidationError from the VM package
from ..errors import ValidationError


# ---------------- Limits ----------------

U256_MAX = (1 << 256) - 1
MAX_BYTES = 256 * 1024  # 256 KiB for browser sims
ADDRESS_LEN = 32


# ---------------- Small hex helpers ----------------

def _is_hex_prefixed(s: str) -> bool:
    return s.startswith("0x") or s.startswith("0X")


def _strip_0x(s: str) -> str:
    return s[2:] if _is_hex_prefixed(s) else s


def _hex_to_bytes(s: str) -> bytes:
    if not isinstance(s, str):
        raise ValidationError("expected hex string")
    h = _strip_0x(s)
    if len(h) % 2 != 0:
        # canonically left-pad a single nibble
        h = "0" + h
    try:
        b = bytes.fromhex(h)
    except ValueError as e:
        raise ValidationError(f"invalid hex: {e}") from e
    return b


def _bytes_to_hex(b: bytes) -> str:
    if not isinstance(b, (bytes, bytearray)):
        raise ValidationError("expected bytes")
    return "0x" + bytes(b).hex()


def _int_to_hex(n: int) -> str:
    if not isinstance(n, int) or n < 0:
        raise ValidationError("u256 must be non-negative int")
    if n > U256_MAX:
        raise ValidationError("u256 overflow")
    return "0x" + (hex(n)[2:].lower() or "0")


def _hex_to_int(s: str) -> int:
    if not isinstance(s, str):
        raise ValidationError("u256 expects hex string")
    h = _strip_0x(s)
    if not h:
        return 0
    try:
        n = int(h, 16)
    except ValueError as e:
        raise ValidationError(f"invalid u256 hex: {e}") from e
    if n < 0 or n > U256_MAX:
        raise ValidationError("u256 out of range")
    return n


# ---------------- Type validators ----------------

def _as_u256(v: Any) -> int:
    if isinstance(v, bool):
        # Bool is a subclass of int; treat explicitly to avoid surprises.
        return 1 if v else 0
    if isinstance(v, int):
        if v < 0 or v > U256_MAX:
            raise ValidationError("u256 out of range")
        return v
    if isinstance(v, str):
        return _hex_to_int(v)
    raise ValidationError("u256 must be int or hex string")

def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        if v in (0, 1):
            return bool(v)
        raise ValidationError("bool int form must be 0 or 1")
    if isinstance(v, str):
        sv = v.strip().lower()
        if sv in ("true", "1"):
            return True
        if sv in ("false", "0"):
            return False
    raise ValidationError("bool must be true/false, 0/1, or 'true'/'false'")

def _as_bytes(v: Any, *, max_len: int = MAX_BYTES) -> bytes:
    if isinstance(v, (bytes, bytearray)):
        b = bytes(v)
    elif isinstance(v, str):
        b = _hex_to_bytes(v)
    else:
        raise ValidationError("bytes must be hex string or bytes")
    if len(b) > max_len:
        raise ValidationError("bytes exceeds maximum length")
    return b

def _as_address(v: Any) -> bytes:
    b = _as_bytes(v, max_len=ADDRESS_LEN)
    if len(b) != ADDRESS_LEN:
        raise ValidationError(f"address must be {ADDRESS_LEN} bytes")
    return b


# ---------------- Public (de)serializers ----------------

def decode_arg(type_name: str, value: Any) -> Any:
    """
    Convert a JSON/transport value into VM-native type according to `type_name`.
    """
    t = type_name.strip().lower()
    if t == "u256":
        return _as_u256(value)
    if t == "bool":
        return _as_bool(value)
    if t == "bytes":
        return _as_bytes(value)
    if t == "address":
        return _as_address(value)
    raise ValidationError(f"unsupported abi type: {type_name}")

def encode_value(type_name: str, value: Any) -> Any:
    """
    Convert a VM-native value into a JSON/transport representation.
    """
    t = type_name.strip().lower()
    if t == "u256":
        return _int_to_hex(_as_u256(value))
    if t == "bool":
        return _as_bool(value)
    if t == "bytes":
        return _bytes_to_hex(_as_bytes(value))
    if t == "address":
        return _bytes_to_hex(_as_address(value))
    raise ValidationError(f"unsupported abi type: {type_name}")


# ---------------- ABI schema & dispatch helpers ----------------

@dataclass(frozen=True)
class AbiParam:
    name: str
    type: str

@dataclass(frozen=True)
class AbiFunction:
    name: str
    inputs: Tuple[AbiParam, ...]
    outputs: Tuple[AbiParam, ...]  # zero or more
    view: bool  # True for read-only

class AbiSchema:
    """
    Parse and index a minimal manifest ABI:

    {
      "functions": [
        {
          "name": "inc",
          "mutability": "nonpayable" | "view",
          "inputs":  [{"name": "delta", "type": "u256"}],
          "outputs": [{"name": "newValue", "type": "u256"}]
        },
        ...
      ]
    }
    """

    def __init__(self, manifest_like: Mapping[str, Any]) -> None:
        funcs_in = manifest_like.get("functions") or manifest_like.get("abi", {}).get("functions")
        if not isinstance(funcs_in, Sequence):
            raise ValidationError("manifest must include an array of functions")
        index: Dict[str, AbiFunction] = {}
        for f in funcs_in:
            if not isinstance(f, Mapping):
                raise ValidationError("function entries must be objects")
            name = str(f.get("name") or "")
            if not name:
                raise ValidationError("function.name missing")
            mut = str(f.get("mutability") or f.get("stateMutability") or "nonpayable").lower()
            view = mut in ("view", "pure", "readonly")
            inputs = tuple(self._parse_params(f.get("inputs", []), "inputs"))
            outputs = tuple(self._parse_params(f.get("outputs", []), "outputs"))
            af = AbiFunction(name=name, inputs=inputs, outputs=outputs, view=view)
            index[name] = af
        self._funcs = index

    @staticmethod
    def _parse_params(ps: Any, field: str) -> Iterable[AbiParam]:
        if not isinstance(ps, Sequence):
            raise ValidationError(f"{field} must be a list")
        out: List[AbiParam] = []
        for p in ps:
            if not isinstance(p, Mapping):
                raise ValidationError(f"{field} entries must be objects")
            nm = str(p.get("name") or "")
            tp = str(p.get("type") or "")
            if not nm or not tp:
                raise ValidationError(f"{field} param requires name and type")
            # Basic sanity on type name
            t = tp.strip().lower()
            if t not in ("u256", "bool", "bytes", "address"):
                raise ValidationError(f"unsupported param type: {tp}")
            out.append(AbiParam(name=nm, type=tp))
        return out

    def get(self, name: str) -> AbiFunction:
        if name not in self._funcs:
            raise ValidationError(f"unknown function: {name}")
        return self._funcs[name]

    def functions(self) -> List[AbiFunction]:
        return list(self._funcs.values())


class AbiDispatcher:
    """
    Validate & transform call arguments/returns according to AbiSchema.

    - decode_inputs(fn_name, payload) -> tuple(native_args)
      Accepts either dict{name:value} or list in declared order.
    - encode_outputs(fn_name, native_values) -> dict or list
      Returns dict keyed by output param names if names are unique and non-empty,
      otherwise returns list in declared order for stability.
    """

    def __init__(self, abi: AbiSchema) -> None:
        self._abi = abi

    # ---- Inputs ----

    def decode_inputs(self, fn_name: str, payload: Any) -> Tuple[Any, ...]:
        fn = self._abi.get(fn_name)
        if isinstance(payload, Mapping):
            # dict mode: map by name
            return tuple(
                decode_arg(p.type, self._get_required(payload, p.name))
                for p in fn.inputs
            )
        elif isinstance(payload, Sequence) and not isinstance(payload, (bytes, bytearray, str)):
            # list/tuple mode: positional
            if len(payload) != len(fn.inputs):
                raise ValidationError("argument count mismatch")
            return tuple(
                decode_arg(p.type, payload[i])
                for i, p in enumerate(fn.inputs)
            )
        else:
            raise ValidationError("payload must be dict or list")

    @staticmethod
    def _get_required(payload: Mapping[str, Any], key: str) -> Any:
        if key not in payload:
            raise ValidationError(f"missing argument: {key}")
        return payload[key]

    # ---- Outputs ----

    def encode_outputs(self, fn_name: str, native_values: Any) -> Any:
        fn = self._abi.get(fn_name)
        outs = fn.outputs
        # Normalize native_values to a tuple
        if len(outs) == 0:
            return None
        if len(outs) == 1:
            # Single return: allow bare scalar
            val = native_values[0] if isinstance(native_values, (list, tuple)) else native_values
            return encode_value(outs[0].type, val)
        # Multi-return: expect a sequence
        if not isinstance(native_values, (list, tuple)) or len(native_values) != len(outs):
            raise ValidationError("expected a list/tuple of return values")
        # Prefer object if names are unique & non-empty
        names = [o.name for o in outs]
        if all(n and names.count(n) == 1 for n in names):
            return {
                o.name: encode_value(o.type, native_values[i])
                for i, o in enumerate(outs)
            }
        # Otherwise, return an ordered list
        return [encode_value(o.type, native_values[i]) for i, o in enumerate(outs)]


# ---------------- Convenience wrappers ----------------

def decode_inputs(manifest_like: Mapping[str, Any], fn_name: str, payload: Any) -> Tuple[Any, ...]:
    """One-shot helper: manifest + function + payload → native args tuple."""
    return AbiDispatcher(AbiSchema(manifest_like)).decode_inputs(fn_name, payload)

def encode_outputs(manifest_like: Mapping[str, Any], fn_name: str, native_values: Any) -> Any:
    """One-shot helper: manifest + function + native values → transport data."""
    return AbiDispatcher(AbiSchema(manifest_like)).encode_outputs(fn_name, native_values)


__all__ = [
    "AbiSchema",
    "AbiFunction",
    "AbiParam",
    "AbiDispatcher",
    "decode_arg",
    "encode_value",
    "decode_inputs",
    "encode_outputs",
]
