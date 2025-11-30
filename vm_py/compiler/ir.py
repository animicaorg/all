"""
ir.py — Animica Python-VM intermediate representation (IR).

We provide TWO layers:

1) Structured IR (what the AST lowerer produces):
   - Expr/Stmt node classes
   - Function / Module containers

2) Small-step, stack-based instruction IR (Instr/Block/Prog):
   - Useful for the interpreter and encoder; control-flow via labeled blocks.

The structured IR is intentionally simple and close to Python surface syntax (after
validation). A later pass can lower it to the instruction IR for execution/encoding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (Any, Dict, Iterable, List, Mapping, Optional, Sequence,
                    Tuple, Union)

# =============================================================================
# Structured IR (Expr/Stmt)
# =============================================================================


class Expr:
    """Base class for expression nodes."""

    __slots__ = ()


@dataclass(frozen=True)
class Const(Expr):
    value: Any


@dataclass(frozen=True)
class Name(Expr):
    name: str


@dataclass(frozen=True)
class BinOp(Expr):
    op: str  # "add" | "sub" | "mul" | "floordiv" | "mod" | "and" | "or" | "xor" | "lshift" | "rshift"
    left: Expr
    right: Expr


@dataclass(frozen=True)
class BoolOp(Expr):
    op: str  # "and" | "or"
    values: List[Expr]


@dataclass(frozen=True)
class UnaryOp(Expr):
    op: str  # "pos" | "neg" | "not" | "invert"
    operand: Expr


@dataclass(frozen=True)
class Compare(Expr):
    op: str  # "eq" | "ne" | "lt" | "le" | "gt" | "ge" | "in" | "not_in" | "is" | "is_not"
    left: Expr
    right: Expr


@dataclass(frozen=True)
class Attribute(Expr):
    value: Expr
    attr: str


@dataclass(frozen=True)
class Subscript(Expr):
    value: Expr
    index: Expr


@dataclass(frozen=True)
class Call(Expr):
    func: Expr
    args: List[Expr]
    kwargs: List[Tuple[str, Expr]]  # preserved order


class Stmt:
    """Base class for statement nodes."""

    __slots__ = ()


@dataclass(frozen=True)
class Assign(Stmt):
    # Either ["x"] for single target OR [["a","b"]] for tuple unpack
    targets: Union[List[str], List[List[str]]]
    value: Expr


@dataclass(frozen=True)
class ExprStmt(Stmt):
    expr: Expr


@dataclass(frozen=True)
class Return(Stmt):
    value: Optional[Expr] = None


@dataclass(frozen=True)
class If(Stmt):
    cond: Expr
    then: List[Stmt]
    orelse: List[Stmt]


@dataclass(frozen=True)
class While(Stmt):
    cond: Expr
    body: List[Stmt]


@dataclass(frozen=True)
class Function:
    name: str
    params: List[str]
    body: List[Stmt]


@dataclass(frozen=True)
class Module:
    filename: str = "<memory>"
    functions: List[Function] = field(default_factory=list)


# =============================================================================
# Small-step instruction IR
#   - A minimal, stack-based form suitable for deterministic execution and wire encoding.
# =============================================================================


@dataclass(frozen=True)
class Instr:
    """Base instruction type used by the IR encoder/decoder."""

    op: str
    args: tuple


# --- Stack/Data ops -----------------------------------------------------------


@dataclass(frozen=True)
class ILoadConst(Instr):
    value: Any


@dataclass(frozen=True)
class ILoadName(Instr):
    name: str


@dataclass(frozen=True)
class IStoreName(Instr):
    name: str


@dataclass(frozen=True)
class IAttrGet(Instr):
    attr: str


@dataclass(frozen=True)
class ISubscriptGet(Instr):
    """Pop index, pop value; push value[index]."""

    pass


@dataclass(frozen=True)
class IBinOp(Instr):
    op: str  # same operator symbols as BinOp.op


@dataclass(frozen=True)
class IUnaryOp(Instr):
    op: str  # same as UnaryOp.op


@dataclass(frozen=True)
class ICompare(Instr):
    op: str  # same as Compare.op


@dataclass(frozen=True)
class ICall(Instr):
    n_pos: int
    kw_names: Tuple[
        str, ...
    ]  # order preserved; args taken from stack: [.., *pos, *kwvals]


@dataclass(frozen=True)
class IPop(Instr):
    pass


@dataclass(frozen=True)
class IDup(Instr):
    pass


# --- Control flow -------------------------------------------------------------


@dataclass(frozen=True)
class IReturn(Instr):
    """Return top-of-stack (or None if stack empty by convention)."""

    pass


@dataclass(frozen=True)
class IJump(Instr):
    target: str  # label


@dataclass(frozen=True)
class IJumpIfTrue(Instr):
    target: str  # jumps if top-of-stack is truthy (consumes it)


@dataclass(frozen=True)
class IJumpIfFalse(Instr):
    target: str  # jumps if top-of-stack is falsy (consumes it)


@dataclass(frozen=True)
class INop(Instr):
    pass


@dataclass(frozen=True)
class Block:
    """A labeled basic block of instructions. Control flows by jumps or fallthrough."""

    label: str
    instrs: List[Instr] = field(default_factory=list)
    # Optional explicit fallthrough label; if None, the next block in layout is the fallthrough.
    fallthrough: Optional[str] = None


@dataclass(frozen=True)
class Prog:
    """A complete program comprised of labeled blocks."""

    entry: str
    blocks: Dict[str, Block]


# =============================================================================
# Utilities: validation / traversal / pretty-print
# =============================================================================


def validate_module(mod: Module) -> None:
    """Light sanity checks on structured IR."""
    if not mod.functions:
        return
    for fname, fn in mod.functions.items():
        if not fname:
            raise ValueError("Function name must be non-empty")
        _validate_identifier_list(fn.params, what=f"params of {fname}")
        _validate_stmt_block(fn.body, ctx=f"function {fname}")


def _validate_identifier_list(names: Sequence[str], *, what: str) -> None:
    seen: set[str] = set()
    for n in names:
        if not n or not _is_ident(n):
            raise ValueError(f"Invalid identifier {n!r} in {what}")
        if n in seen:
            raise ValueError(f"Duplicate identifier {n!r} in {what}")
        seen.add(n)


def _is_ident(s: str) -> bool:
    if not s:
        return False
    # Conservatively accept ASCII identifiers
    head, tail = s[0], s[1:]
    if not (head.isalpha() or head == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in tail)


def _validate_stmt_block(block: Sequence[Stmt], *, ctx: str) -> None:
    for st in block:
        if isinstance(st, Assign):
            # Validate targets shape
            if (
                isinstance(st.targets, list)
                and st.targets
                and isinstance(st.targets[0], list)
            ):
                # Unpack assignment form [["a","b",...]]
                names = st.targets[0]
                _validate_identifier_list(names, what=f"unpack targets in {ctx}")
            else:
                _validate_identifier_list(
                    st.targets if isinstance(st.targets, list) else [],
                    what=f"targets in {ctx}",
                )
        elif isinstance(st, (ExprStmt, Return)):
            pass
        elif isinstance(st, If):
            _validate_expr(st.cond, ctx=f"{ctx} (if cond)")
            _validate_stmt_block(st.then, ctx=f"{ctx} (if then)")
            _validate_stmt_block(st.orelse, ctx=f"{ctx} (if else)")
        elif isinstance(st, While):
            _validate_expr(st.cond, ctx=f"{ctx} (while cond)")
            _validate_stmt_block(st.body, ctx=f"{ctx} (while body)")
        else:
            raise ValueError(f"Unknown Stmt in {_where(ctx)}: {st!r}")


def _validate_expr(e: Expr, *, ctx: str) -> None:
    if isinstance(e, (Const, Name)):
        return
    if isinstance(e, BinOp):
        _validate_expr(e.left, ctx=ctx)
        _validate_expr(e.right, ctx=ctx)
        return
    if isinstance(e, BoolOp):
        if len(e.values) < 2:
            raise ValueError(f"BoolOp requires >=2 values in {_where(ctx)}")
        for v in e.values:
            _validate_expr(v, ctx=ctx)
        return
    if isinstance(e, UnaryOp):
        _validate_expr(e.operand, ctx=ctx)
        return
    if isinstance(e, Compare):
        _validate_expr(e.left, ctx=ctx)
        _validate_expr(e.right, ctx=ctx)
        return
    if isinstance(e, Attribute):
        _validate_expr(e.value, ctx=ctx)
        return
    if isinstance(e, Subscript):
        _validate_expr(e.value, ctx=ctx)
        _validate_expr(e.index, ctx=ctx)
        return
    if isinstance(e, Call):
        _validate_expr(e.func, ctx=ctx)
        for a in e.args:
            _validate_expr(a, ctx=ctx)
        for _, kv in e.kwargs:
            _validate_expr(kv, ctx=ctx)
        return
    raise ValueError(f"Unknown Expr in {_where(ctx)}: {e!r}")


def _where(ctx: str) -> str:
    return ctx if ctx else "<unknown>"


def walk_expr(e: Expr) -> Iterable[Expr]:
    """Preorder walk over an expression tree."""
    yield e
    if isinstance(e, BinOp):
        yield from walk_expr(e.left)
        yield from walk_expr(e.right)
    elif isinstance(e, BoolOp):
        for v in e.values:
            yield from walk_expr(v)
    elif isinstance(e, UnaryOp):
        yield from walk_expr(e.operand)
    elif isinstance(e, Compare):
        yield from walk_expr(e.left)
        yield from walk_expr(e.right)
    elif isinstance(e, Attribute):
        yield from walk_expr(e.value)
    elif isinstance(e, Subscript):
        yield from walk_expr(e.value)
        yield from walk_expr(e.index)
    elif isinstance(e, Call):
        yield from walk_expr(e.func)
        for a in e.args:
            yield from walk_expr(a)
        for _, kv in e.kwargs:
            yield from walk_expr(kv)


def walk_stmt(st: Stmt) -> Iterable[Union[Stmt, Expr]]:
    """Preorder walk over a statement (yield stmts and nested exprs)."""
    yield st
    if isinstance(st, Assign):
        yield st.value
        yield from walk_expr(st.value)
    elif isinstance(st, ExprStmt):
        yield st.expr
        yield from walk_expr(st.expr)
    elif isinstance(st, Return):
        if st.value is not None:
            yield st.value
            yield from walk_expr(st.value)
    elif isinstance(st, If):
        yield st.cond
        yield from walk_expr(st.cond)
        for s in st.then:
            yield from walk_stmt(s)
        for s in st.orelse:
            yield from walk_stmt(s)
    elif isinstance(st, While):
        yield st.cond
        yield from walk_expr(st.cond)
        for s in st.body:
            yield from walk_stmt(s)


def pretty_expr(e: Expr) -> str:
    if isinstance(e, Const):
        return repr(e.value)
    if isinstance(e, Name):
        return e.name
    if isinstance(e, BinOp):
        return f"({pretty_expr(e.left)} {e.op} {pretty_expr(e.right)})"
    if isinstance(e, BoolOp):
        join = f" {e.op} "
        return f"({join.join(pretty_expr(v) for v in e.values)})"
    if isinstance(e, UnaryOp):
        return f"({e.op} {pretty_expr(e.operand)})"
    if isinstance(e, Compare):
        return f"({pretty_expr(e.left)} {e.op} {pretty_expr(e.right)})"
    if isinstance(e, Attribute):
        return f"{pretty_expr(e.value)}.{e.attr}"
    if isinstance(e, Subscript):
        return f"{pretty_expr(e.value)}[{pretty_expr(e.index)}]"
    if isinstance(e, Call):
        pos = ", ".join(pretty_expr(a) for a in e.args)
        kws = ", ".join(f"{k}={pretty_expr(v)}" for k, v in e.kwargs)
        args = ", ".join(a for a in (pos, kws) if a)
        return f"{pretty_expr(e.func)}({args})"
    return f"<Expr {type(e).__name__}>"


def pretty_stmt(st: Stmt, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(st, Assign):
        if st.targets and isinstance(st.targets[0], list):
            tgt = f"({', '.join(st.targets[0])})"
        else:
            tgt = ", ".join(st.targets) if isinstance(st.targets, list) else "<targets>"
        return f"{pad}{tgt} = {pretty_expr(st.value)}"
    if isinstance(st, ExprStmt):
        return f"{pad}{pretty_expr(st.expr)}"
    if isinstance(st, Return):
        return f"{pad}return {pretty_expr(st.value) if st.value is not None else ''}".rstrip()
    if isinstance(st, If):
        then = "\n".join(pretty_stmt(s, indent + 1) for s in st.then)
        orelse = "\n".join(pretty_stmt(s, indent + 1) for s in st.orelse)
        out = [f"{pad}if {pretty_expr(st.cond)}:", then]
        if st.orelse:
            out += [f"{pad}else:", orelse]
        return "\n".join(out)
    if isinstance(st, While):
        body = "\n".join(pretty_stmt(s, indent + 1) for s in st.body)
        return f"{pad}while {pretty_expr(st.cond)}:\n{body}"
    return f"{pad}<Stmt {type(st).__name__}>"


__all__ = [
    # structured
    "Expr",
    "Const",
    "Name",
    "BinOp",
    "BoolOp",
    "UnaryOp",
    "Compare",
    "Attribute",
    "Subscript",
    "Call",
    "Stmt",
    "Assign",
    "ExprStmt",
    "Return",
    "If",
    "While",
    "Function",
    "Module",
    # instruction IR
    "Instr",
    "ILoadConst",
    "ILoadName",
    "IStoreName",
    "IAttrGet",
    "ISubscriptGet",
    "IBinOp",
    "IUnaryOp",
    "ICompare",
    "ICall",
    "IPop",
    "IDup",
    "IReturn",
    "IJump",
    "IJumpIfTrue",
    "IJumpIfFalse",
    "INop",
    "Block",
    "Prog",
    # utils
    "validate_module",
    "walk_expr",
    "walk_stmt",
    "pretty_expr",
    "pretty_stmt",
]
