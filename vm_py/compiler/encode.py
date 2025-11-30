"""
encode.py — Stable IR ↔ bytes encoding (CBOR preferred; msgpack fallback).

Design goals
------------
- Round-trip stable across platforms and Python versions.
- Deterministic ordering (e.g., blocks/functions sorted by name when serialized).
- No pickles or dynamic code; only plain, tagged arrays and scalars.
- Self-describing header with magic + version + format for future-proofing.

Formats
-------
We prefer canonical CBOR (via `cbor2`, with canonical ordering). If `cbor2` is
unavailable, we fall back to msgpack using `msgspec`. The file header embeds the
chosen format so decoders can dispatch correctly.

Wire layout
-----------
Header (6 bytes):
  0..3 : ASCII magic b"ACIR"  (Animica Compiler IR)
  4    : version byte (0x01)
  5    : format byte  (0x01 = CBOR, 0x02 = MSGPACK)

Payload:
  A tree of small "tagged lists". Each IR node encodes to a short list:
    [tag_id, field1, field2, ...]
  Collections with identity (functions, blocks) are encoded as *sorted* lists
  of pairs to guarantee deterministic order.

We expose helpers for both IR layers:
  - Instruction IR (Prog/Block/Instr): encode_prog / decode_prog
  - Structured IR (Module/Function/Stmt/Expr): encode_module / decode_module
"""
from __future__ import annotations

from dataclasses import is_dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Prefer CBOR; fall back to msgpack via msgspec
_CBOR_AVAILABLE = False
_MSGSPEC_AVAILABLE = False

try:
    import cbor2  # type: ignore

    _CBOR_AVAILABLE = True
except Exception:  # pragma: no cover
    _CBOR_AVAILABLE = False

try:
    import msgspec  # type: ignore

    _MSGPACK_ENC = msgspec.Encoder()  # msgpack by default
    _MSGPACK_DEC = msgspec.Decoder()
    _MSGSPEC_AVAILABLE = True
except Exception:  # pragma: no cover
    _MSGSPEC_AVAILABLE = False

from .ir import (
    # Structured IR
    Expr,
    Const,
    Name,
    BinOp,
    BoolOp,
    UnaryOp,
    Compare,
    Attribute,
    Subscript,
    Call,
    Stmt,
    Assign,
    ExprStmt,
    Return,
    If,
    While,
    Function,
    Module,
    # Instruction IR
    Instr,
    ILoadConst,
    ILoadName,
    IStoreName,
    IAttrGet,
    ISubscriptGet,
    IBinOp,
    IUnaryOp,
    ICompare,
    ICall,
    IPop,
    IDup,
    IReturn,
    IJump,
    IJumpIfTrue,
    IJumpIfFalse,
    INop,
    Block,
    Prog,
)

MAGIC = b"ACIR"
VERSION = 1
FMT_CBOR = 0x01
FMT_MSGPACK = 0x02

# -----------------------------------------------------------------------------
# Encoding helpers (format wrappers)
# -----------------------------------------------------------------------------

class IRCodecError(ValueError):
    pass


def _dumps_payload(obj: Any, fmt: int) -> bytes:
    """Serialize `obj` to bytes with the selected format."""
    if fmt == FMT_CBOR:
        if not _CBOR_AVAILABLE:
            raise IRCodecError("CBOR requested but 'cbor2' is not installed")
        # canonical=True enforces deterministic map ordering and integer encodings
        return cbor2.dumps(obj, canonical=True)  # type: ignore[name-defined]
    if fmt == FMT_MSGPACK:
        if not _MSGSPEC_AVAILABLE:
            raise IRCodecError("MSGPACK requested but 'msgspec' is not installed")
        return _MSGPACK_ENC.encode(obj)  # type: ignore[name-defined]
    raise IRCodecError(f"Unknown format byte: {fmt!r}")


def _loads_payload(data: bytes, fmt: int) -> Any:
    """Deserialize `data` with the selected format."""
    if fmt == FMT_CBOR:
        if not _CBOR_AVAILABLE:
            raise IRCodecError("CBOR payload but 'cbor2' not available")
        return cbor2.loads(data)  # type: ignore[name-defined]
    if fmt == FMT_MSGPACK:
        if not _MSGSPEC_AVAILABLE:
            raise IRCodecError("MSGPACK payload but 'msgspec' not available")
        return _MSGPACK_DEC.decode(data)  # type: ignore[name-defined]
    raise IRCodecError(f"Unknown format byte: {fmt!r}")


def _wrap_with_header(payload: bytes, fmt: int) -> bytes:
    if fmt not in (FMT_CBOR, FMT_MSGPACK):
        raise IRCodecError(f"Unsupported format: {fmt}")
    return MAGIC + bytes((VERSION, fmt)) + payload


def _unwrap_header(blob: bytes) -> Tuple[int, bytes]:
    """Return (fmt, payload) after validating header. Accepts legacy blobs (no header) as CBOR."""
    if len(blob) >= 6 and blob[:4] == MAGIC:
        ver = blob[4]
        fmt = blob[5]
        if ver != VERSION:
            raise IRCodecError(f"Unsupported IR version: {ver} (expected {VERSION})")
        return fmt, blob[6:]
    # Legacy/unknown: try CBOR first, then msgpack
    for fmt_try in (FMT_CBOR, FMT_MSGPACK):
        try:
            _ = _loads_payload(blob, fmt_try)
            return fmt_try, blob
        except Exception:
            continue
    raise IRCodecError("Unrecognized IR blob (bad header and decode attempts failed)")

# -----------------------------------------------------------------------------
# Instruction IR encoding
# -----------------------------------------------------------------------------
# Tags are compact and stable across versions. Do not reorder existing tags.
I_TAGS = {
    ILoadConst: 0,
    ILoadName: 1,
    IStoreName: 2,
    IAttrGet: 3,
    ISubscriptGet: 4,
    IBinOp: 5,
    IUnaryOp: 6,
    ICompare: 7,
    ICall: 8,
    IPop: 9,
    IDup: 10,
    IReturn: 11,
    IJump: 12,
    IJumpIfTrue: 13,
    IJumpIfFalse: 14,
    INop: 15,
}
I_BY_TAG = {v: k for k, v in I_TAGS.items()}


def _enc_instr(inst: Instr) -> list:
    """Instr → [tag, ...fields]"""
    t = I_TAGS.get(type(inst))
    if t is None:
        raise IRCodecError(f"Unknown instruction type: {type(inst).__name__}")
    # field order is wire-stable
    if isinstance(inst, ILoadConst):
        return [t, inst.value]
    if isinstance(inst, ILoadName):
        return [t, inst.name]
    if isinstance(inst, IStoreName):
        return [t, inst.name]
    if isinstance(inst, IAttrGet):
        return [t, inst.attr]
    if isinstance(inst, ISubscriptGet):
        return [t]
    if isinstance(inst, IBinOp):
        return [t, inst.op]
    if isinstance(inst, IUnaryOp):
        return [t, inst.op]
    if isinstance(inst, ICompare):
        return [t, inst.op]
    if isinstance(inst, ICall):
        return [t, inst.n_pos, list(inst.kw_names)]
    if isinstance(inst, IPop):
        return [t]
    if isinstance(inst, IDup):
        return [t]
    if isinstance(inst, IReturn):
        return [t]
    if isinstance(inst, IJump):
        return [t, inst.target]
    if isinstance(inst, IJumpIfTrue):
        return [t, inst.target]
    if isinstance(inst, IJumpIfFalse):
        return [t, inst.target]
    if isinstance(inst, INop):
        return [t]
    # unreachable
    raise IRCodecError(f"Unhandled instruction: {inst!r}")  # pragma: no cover


def _dec_instr(item: Sequence[Any]) -> Instr:
    """[tag, ...fields] → Instr"""
    if not item or not isinstance(item, Sequence):
        raise IRCodecError("Bad instruction payload")
    tag = int(item[0])
    cls = I_BY_TAG.get(tag)
    if cls is None:
        raise IRCodecError(f"Unknown instruction tag: {tag}")
    # reconstruct by tag
    if cls is ILoadConst:
        return ILoadConst(item[1])
    if cls is ILoadName:
        return ILoadName(str(item[1]))
    if cls is IStoreName:
        return IStoreName(str(item[1]))
    if cls is IAttrGet:
        return IAttrGet(str(item[1]))
    if cls is ISubscriptGet:
        return ISubscriptGet()
    if cls is IBinOp:
        return IBinOp(str(item[1]))
    if cls is IUnaryOp:
        return IUnaryOp(str(item[1]))
    if cls is ICompare:
        return ICompare(str(item[1]))
    if cls is ICall:
        n_pos = int(item[1])
        kw_names = tuple(str(x) for x in item[2])
        return ICall(n_pos=n_pos, kw_names=kw_names)
    if cls is IPop:
        return IPop()
    if cls is IDup:
        return IDup()
    if cls is IReturn:
        return IReturn()
    if cls is IJump:
        return IJump(str(item[1]))
    if cls is IJumpIfTrue:
        return IJumpIfTrue(str(item[1]))
    if cls is IJumpIfFalse:
        return IJumpIfFalse(str(item[1]))
    if cls is INop:
        return INop()
    # unreachable
    raise IRCodecError(f"Unhandled instruction tag: {tag}")  # pragma: no cover


def _enc_block(block: Block) -> list:
    """Block → [label, [instrs...], fallthrough_or_none]"""
    return [
        str(block.label),
        [_enc_instr(i) for i in block.instrs],
        (None if block.fallthrough is None else str(block.fallthrough)),
    ]


def _dec_block(item: Sequence[Any]) -> Block:
    if not isinstance(item, Sequence) or len(item) != 3:
        raise IRCodecError("Bad block payload")
    label = str(item[0])
    instrs = [_dec_instr(x) for x in item[1]]
    fallthrough = None if item[2] is None else str(item[2])
    return Block(label=label, instrs=instrs, fallthrough=fallthrough)


def encode_prog(prog: Prog, *, prefer: str = "cbor") -> bytes:
    """
    Encode a Prog to bytes with header. `prefer` in {"cbor", "msgpack"}.
    """
    if not isinstance(prog, Prog):
        raise TypeError("encode_prog expects a Prog")
    fmt = FMT_CBOR if (prefer == "cbor" and _CBOR_AVAILABLE) else (
        FMT_MSGPACK if _MSGSPEC_AVAILABLE else (
            FMT_CBOR if _CBOR_AVAILABLE else None
        )
    )
    if fmt is None:
        raise IRCodecError("Neither 'cbor2' nor 'msgspec' is available for encoding")

    # Deterministic order: sort blocks by label
    blocks_sorted = sorted(prog.blocks.items(), key=lambda kv: kv[0])
    payload = [
        "IR1",                    # schema id
        str(prog.entry),
        [_enc_block(b) for _, b in blocks_sorted],
    ]
    return _wrap_with_header(_dumps_payload(payload, fmt), fmt)


def decode_prog(blob: bytes) -> Prog:
    """Decode bytes (with or without header) into a Prog."""
    fmt, payload = _unwrap_header(blob)
    data = _loads_payload(payload, fmt)
    if not (isinstance(data, list) and len(data) == 3 and data[0] == "IR1"):
        raise IRCodecError("Invalid Prog payload")
    entry = str(data[1])
    blocks_list = data[2]
    blocks: Dict[str, Block] = {}
    for blk in blocks_list:
        b = _dec_block(blk)
        blocks[b.label] = b
    return Prog(entry=entry, blocks=blocks)

# -----------------------------------------------------------------------------
# Structured IR encoding
# -----------------------------------------------------------------------------
# Tags for structured IR nodes (kept separate from instruction tags)
E_CONST = 100
E_NAME = 101
E_BINOP = 102
E_BOOLOP = 103
E_UNARY = 104
E_CMP = 105
E_ATTR = 106
E_SUB = 107
E_CALL = 108

S_ASSIGN = 200
S_EXPR = 201
S_RETURN = 202
S_IF = 203
S_WHILE = 204

F_FUNCTION = 210
M_MODULE = 211


def _enc_expr(e: Expr) -> list:
    if isinstance(e, Const):
        return [E_CONST, e.value]
    if isinstance(e, Name):
        return [E_NAME, e.name]
    if isinstance(e, BinOp):
        return [E_BINOP, e.op, _enc_expr(e.left), _enc_expr(e.right)]
    if isinstance(e, BoolOp):
        return [E_BOOLOP, e.op, [_enc_expr(v) for v in e.values]]
    if isinstance(e, UnaryOp):
        return [E_UNARY, e.op, _enc_expr(e.operand)]
    if isinstance(e, Compare):
        return [E_CMP, e.op, _enc_expr(e.left), _enc_expr(e.right)]
    if isinstance(e, Attribute):
        return [E_ATTR, _enc_expr(e.value), e.attr]
    if isinstance(e, Subscript):
        return [E_SUB, _enc_expr(e.value), _enc_expr(e.index)]
    if isinstance(e, Call):
        return [E_CALL, _enc_expr(e.func), [_enc_expr(a) for a in e.args], [[k, _enc_expr(v)] for k, v in e.kwargs]]
    raise IRCodecError(f"Unknown Expr node: {type(e).__name__}")


def _dec_expr(n: Sequence[Any]) -> Expr:
    tag = int(n[0])
    if tag == E_CONST:
        return Const(n[1])
    if tag == E_NAME:
        return Name(str(n[1]))
    if tag == E_BINOP:
        return BinOp(str(n[1]), _dec_expr(n[2]), _dec_expr(n[3]))
    if tag == E_BOOLOP:
        return BoolOp(str(n[1]), [_dec_expr(v) for v in n[2]])
    if tag == E_UNARY:
        return UnaryOp(str(n[1]), _dec_expr(n[2]))
    if tag == E_CMP:
        return Compare(str(n[1]), _dec_expr(n[2]), _dec_expr(n[3]))
    if tag == E_ATTR:
        return Attribute(_dec_expr(n[1]), str(n[2]))
    if tag == E_SUB:
        return Subscript(_dec_expr(n[1]), _dec_expr(n[2]))
    if tag == E_CALL:
        func = _dec_expr(n[1])
        args = [_dec_expr(a) for a in n[2]]
        kwargs = [(str(kv[0]), _dec_expr(kv[1])) for kv in n[3]]
        return Call(func, args, kwargs)
    raise IRCodecError(f"Unknown Expr tag: {tag}")


def _enc_stmt(s: Stmt) -> list:
    if isinstance(s, Assign):
        return [S_ASSIGN, s.targets, _enc_expr(s.value)]
    if isinstance(s, ExprStmt):
        return [S_EXPR, _enc_expr(s.expr)]
    if isinstance(s, Return):
        return [S_RETURN, None if s.value is None else _enc_expr(s.value)]
    if isinstance(s, If):
        return [S_IF, _enc_expr(s.cond), [_enc_stmt(x) for x in s.then], [_enc_stmt(x) for x in s.orelse]]
    if isinstance(s, While):
        return [S_WHILE, _enc_expr(s.cond), [_enc_stmt(x) for x in s.body]]
    raise IRCodecError(f"Unknown Stmt node: {type(s).__name__}")


def _dec_stmt(n: Sequence[Any]) -> Stmt:
    tag = int(n[0])
    if tag == S_ASSIGN:
        return Assign(n[1], _dec_expr(n[2]))
    if tag == S_EXPR:
        return ExprStmt(_dec_expr(n[1]))
    if tag == S_RETURN:
        val = None if n[1] is None else _dec_expr(n[1])
        return Return(val)
    if tag == S_IF:
        return If(_dec_expr(n[1]), [_dec_stmt(x) for x in n[2]], [_dec_stmt(x) for x in n[3]])
    if tag == S_WHILE:
        return While(_dec_expr(n[1]), [_dec_stmt(x) for x in n[2]])
    raise IRCodecError(f"Unknown Stmt tag: {tag}")


def _enc_function(fn: Function) -> list:
    return [F_FUNCTION, fn.name, list(fn.params), [_enc_stmt(s) for s in fn.body]]


def _dec_function(n: Sequence[Any]) -> Function:
    if int(n[0]) != F_FUNCTION:
        raise IRCodecError("Function tag mismatch")
    return Function(name=str(n[1]), params=[str(p) for p in n[2]], body=[_dec_stmt(s) for s in n[3]])


def encode_module(mod: Module, *, prefer: str = "cbor") -> bytes:
    """Encode a structured IR Module with header."""
    if not isinstance(mod, Module):
        raise TypeError("encode_module expects a Module")
    fmt = FMT_CBOR if (prefer == "cbor" and _CBOR_AVAILABLE) else (
        FMT_MSGPACK if _MSGSPEC_AVAILABLE else (
            FMT_CBOR if _CBOR_AVAILABLE else None
        )
    )
    if fmt is None:
        raise IRCodecError("Neither 'cbor2' nor 'msgspec' is available for encoding")
    # Deterministic order: sort functions by name
    fns_sorted = sorted(mod.functions.items(), key=lambda kv: kv[0])
    payload = [
        M_MODULE,
        str(mod.filename),
        [[name, _enc_function(fn)] for name, fn in fns_sorted],
    ]
    return _wrap_with_header(_dumps_payload(payload, fmt), fmt)


def decode_module(blob: bytes) -> Module:
    """Decode bytes (with or without header) into a Module."""
    fmt, payload = _unwrap_header(blob)
    data = _loads_payload(payload, fmt)
    if not (isinstance(data, list) and len(data) == 3 and int(data[0]) == M_MODULE):
        raise IRCodecError("Invalid Module payload")
    filename = str(data[1])
    functions: Dict[str, Function] = {}
    for name, fn_node in data[2]:
        functions[str(name)] = _dec_function(fn_node)
    return Module(filename=filename, functions=functions)

# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def is_cbor_available() -> bool:
    return _CBOR_AVAILABLE


def is_msgpack_available() -> bool:
    return _MSGSPEC_AVAILABLE


__all__ = [
    "IRCodecError",
    "encode_prog",
    "decode_prog",
    "encode_module",
    "decode_module",
    "is_cbor_available",
    "is_msgpack_available",
    "FMT_CBOR",
    "FMT_MSGPACK",
    "MAGIC",
    "VERSION",
]

# ---------------------------------------------------------------------------
# Backwards-compatible adapter helpers for CLI & compiler helpers
# ---------------------------------------------------------------------------

def to_bytes(ir_module) -> bytes:
    """
    Canonical IR → bytes encoder used by CLI tooling.

    This is a thin wrapper around ``encode_ir`` and expects a
    ``vm_py.compiler.ir.Module`` instance. It exists so that
    tools like the CLI can look for `encode.to_bytes` /
    `encode.encode` / `encode.ir_to_bytes` in a stable way.
    """
    if not isinstance(ir_module, Module):
        raise TypeError("to_bytes expects an ir.Module")
    return encode_module(ir_module)


def encode(ir_module) -> bytes:
    """
    Alias for :func:`to_bytes`, kept for older docs and helpers:

        from vm_py.compiler import encode_ir
        from vm_py.compiler import encode as enc_mod
        blob = enc_mod.encode(ir_mod)

    """
    return encode_module(ir_module)


def ir_to_bytes(ir_module) -> bytes:
    """
    Additional alias for compatibility with older tooling that expects
    `encode.ir_to_bytes(...)` as the entrypoint.
    """
    return to_bytes(ir_module)


def from_bytes(data: bytes):
    """
    Convenience alias mirroring :func:`decode_ir`, so callers can do
    `encode.from_bytes(...)` when working directly with this module.
    """
    return decode_ir(data)
