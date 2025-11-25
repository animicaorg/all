from __future__ import annotations

"""
IR (de)serialization for the in-browser VM.

Goals
-----
* Deterministic and portable.
* Zero-ambiguity layout that does not depend on map key ordering.
* Prefer a fast binary codec if available (msgspec+msgpack); otherwise fall
  back to CBOR (via cbor2). The on-wire logical shape is the same.

Logical shape (arrays only, for determinism)
--------------------------------------------
IR_BLOB := [
  "animica|ir|v1",
  <entry: str>,
  <functions: [
      [ <name: str>, <params: int>, <body: [ [<op: str>, <args: [scalars...] >], ... ]> ],
      ...
  ]>
]

Scalars := int | bytes | str

This module only handles encoding/decoding + light validation. Execution
semantics and stack discipline are enforced in the engine and validator.
"""

from dataclasses import is_dataclass
from typing import Any, Iterable, List, Sequence, Tuple

from . import ir

_DOM = "animica|ir|v1"


# ---------------- Codec selection ----------------

# Primary: msgspec (msgpack backend)
try:  # pragma: no cover - codec choice is environment-dependent
    import msgspec as _msgspec
    _HAVE_MSGSPEC = True
except Exception:  # pragma: no cover
    _HAVE_MSGSPEC = False
    _msgspec = None  # type: ignore

# Fallback: cbor2
try:  # pragma: no cover
    import cbor2 as _cbor2
    _HAVE_CBOR2 = True
except Exception:  # pragma: no cover
    _HAVE_CBOR2 = False
    _cbor2 = None  # type: ignore


# ---------------- Public API ----------------

def encode_ir(module: ir.Module) -> bytes:
    """
    Serialize an IR Module into deterministic binary bytes.

    Prefers msgpack via msgspec; falls back to CBOR via cbor2.
    """
    if not isinstance(module, ir.Module):
        raise TypeError("encode_ir expects an ir.Module")

    obj = _module_to_obj(module)

    if _HAVE_MSGSPEC:
        return _msgspec.msgpack.encode(obj)
    if _HAVE_CBOR2:
        # Arrays only => canonical by construction.
        return _cbor2.dumps(obj)
    # Last resort: pure-Python repr bytes (not recommended, but keeps dev
    # workflows alive). This is stable but not interoperable.
    return repr(obj).encode("utf-8")


def decode_ir(blob: bytes | bytearray) -> ir.Module:
    """
    Parse bytes into an IR Module.

    Tries msgpack (msgspec), then CBOR (cbor2). Raises ValueError on failure.
    """
    if not isinstance(blob, (bytes, bytearray)):
        raise TypeError("decode_ir expects bytes")

    data: Any = None
    errs: List[str] = []
    if _HAVE_MSGSPEC:
        try:  # pragma: no cover - depends on env
            data = _msgspec.msgpack.decode(blob)
        except Exception as e:  # keep trying other codecs
            errs.append(f"msgpack:{e!s}")
            data = None
    if data is None and _HAVE_CBOR2:
        try:  # pragma: no cover
            data = _cbor2.loads(blob)
        except Exception as e:
            errs.append(f"cbor2:{e!s}")
            data = None
    if data is None:
        # Last-ditch: try eval-safe literal list parsing (from repr fallback).
        try:
            text = blob.decode("utf-8", "strict")
            if text.startswith("[") and text.endswith("]"):
                data = eval(text, {"__builtins__": {}}, {})  # noqa: S307 (no untrusted input expected)
        except Exception as e:
            errs.append(f"repr:{e!s}")

    if data is None:
        raise ValueError(f"IR decode failed via codecs: {', '.join(errs) or 'none available'}")

    return _obj_to_module(data)


# ---------------- Object <-> IR conversion ----------------

def _module_to_obj(m: ir.Module) -> list:
    # Sort functions by name for determinism.
    funcs = sorted(m.functions.values(), key=lambda f: f.name)
    return [
        _DOM,
        m.entry,
        [
            [
                fn.name,
                int(fn.params),
                [
                    [ins.op, _args_to_list(ins.args)]
                    for ins in fn.body
                ],
            ]
            for fn in funcs
        ],
    ]


def _obj_to_module(data: Any) -> ir.Module:
    # Validate outer shape.
    if not isinstance(data, (list, tuple)) or len(data) != 3:
        raise ValueError("invalid IR blob: top-level must be a 3-element list")
    dom, entry, funs = data
    if dom != _DOM:
        raise ValueError("invalid IR domain header")
    if not isinstance(entry, str):
        raise ValueError("entry must be a string")
    if not isinstance(funs, (list, tuple)):
        raise ValueError("functions list must be an array")

    functions: dict[str, ir.Function] = {}
    for item in funs:
        if not (isinstance(item, (list, tuple)) and len(item) == 3):
            raise ValueError("function must be [name, params, body]")
        name, params, body = item
        if not isinstance(name, str):
            raise ValueError("function name must be string")
        if not isinstance(params, int) or params < 0:
            raise ValueError("function params must be non-negative int")
        if not isinstance(body, (list, tuple)):
            raise ValueError("function body must be an array")

        instrs: list[ir.Instr] = []
        for ins in body:
            if not (isinstance(ins, (list, tuple)) and len(ins) == 2):
                raise ValueError("instr must be [op, args]")
            op, args = ins
            if not isinstance(op, str) or op not in ir.ALLOWED_OPS:
                raise ValueError(f"unknown opcode: {op!r}")
            args_t = _list_to_args(args)
            instrs.append(ir.Instr(op=op, args=args_t))

        if name in functions:
            raise ValueError(f"duplicate function name: {name}")
        functions[name] = ir.Function(name=name, params=params, body=instrs)

    if entry not in functions:
        raise ValueError(f"entry function '{entry}' not found")

    return ir.Module(functions=functions, entry=entry)


# ---------------- Helpers ----------------

def _args_to_list(args: Sequence[ir.Operand]) -> list:
    out: list = []
    for a in args:
        if isinstance(a, (bytes, bytearray)):
            out.append(bytes(a))
        elif isinstance(a, (str, int)):
            out.append(a)
        else:
            raise TypeError(f"unsupported operand type: {type(a).__name__}")
    return out


def _list_to_args(seq: Any) -> Tuple[ir.Operand, ...]:
    if not isinstance(seq, (list, tuple)):
        raise ValueError("instr args must be a list")
    out: list[ir.Operand] = []
    for a in seq:
        if isinstance(a, (bytes, bytearray)):
            out.append(bytes(a))
        elif isinstance(a, (str, int)):
            out.append(a)
        else:
            raise ValueError(f"unsupported operand scalar: {type(a).__name__}")
    return tuple(out)


