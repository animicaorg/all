"""
vm_py.runtime.exec_validate — the closed-subset gate for contracts executed by
the tree engine.

The tree interpreter (tree_engine.py) is already safe by construction — it has no
opcode for import/exec/eval/arbitrary-attribute-access, so a hostile contract can
at worst revert, burn gas (→ OOG), or touch its own namespaced storage. This
validator adds two things on top of that:

  1. Deploy/compile DoS bounds — cap source size and AST node count, and reject
     `**` (Pow) so a constant like `2 ** 10**9` can't wedge the constant-folder.
  2. Early, deterministic rejection of any construct outside the closed subset the
     four standard contracts use, so unsupported code fails at compile time with a
     clear reason instead of at runtime.

It is intentionally strict and allowlist-based: every node type and operator must
be explicitly permitted.
"""

from __future__ import annotations

import ast
from typing import Tuple

try:
    from ..errors import ValidationError
except Exception:  # pragma: no cover
    class ValidationError(Exception): ...


MAX_SOURCE_BYTES = 256 * 1024
MAX_AST_NODES = 20_000

# Allowed statement/expression node types.
_ALLOWED_NODES: Tuple[type, ...] = (
    ast.Module,
    ast.FunctionDef,
    ast.arguments, ast.arg,
    ast.Assign, ast.AnnAssign, ast.Expr, ast.Return, ast.If, ast.While, ast.Pass,
    ast.Constant, ast.Name, ast.Load, ast.Store,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.Attribute, ast.Subscript, ast.Call, ast.keyword, ast.IfExp,
    ast.Tuple, ast.List, ast.Dict,
    ast.ImportFrom, ast.alias,
    # operators / ctx we explicitly gate below
    ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd,
    ast.Add, ast.Sub, ast.Mult, ast.FloorDiv,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)

# Binary operators the VM implements (int add/sub/mul/floordiv + bytes concat via Add).
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv)
_ALLOWED_UNARYOPS = (ast.Not, ast.USub, ast.UAdd)
_ALLOWED_CMPOPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)

_ALLOWED_IMPORT_FROM = {"stdlib", "__future__", "typing"}
_ALLOWED_STDLIB = {"abi", "events", "storage", "hash", "treasury"}
_ALLOWED_FUTURE = {"annotations"}
_ALLOWED_TYPING = {"Final", "Optional", "Tuple", "List", "Dict"}


def validate_for_execution(source: str) -> None:
    """Raise ValidationError if `source` is outside the executable subset."""
    raw = source.encode("utf-8") if isinstance(source, str) else bytes(source)
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValidationError(f"contract source too large ({len(raw)} > {MAX_SOURCE_BYTES})")

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValidationError(f"contract does not parse: {exc}") from None

    n = 0
    for node in ast.walk(tree):
        n += 1
        if n > MAX_AST_NODES:
            raise ValidationError("contract AST too large")

        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, _ALLOWED_BINOPS):
                raise ValidationError(
                    f"operator '{type(node.op).__name__}' is not allowed "
                    "(only + - * //; no **, %, /, or bitwise)"
                )
            continue
        if isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, _ALLOWED_UNARYOPS):
                raise ValidationError(f"unary '{type(node.op).__name__}' not allowed")
            continue
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1:
                raise ValidationError("chained comparisons are not allowed")
            if not isinstance(node.ops[0], _ALLOWED_CMPOPS):
                raise ValidationError(
                    f"comparison '{type(node.ops[0]).__name__}' not allowed (no in/is)"
                )
            continue
        if isinstance(node, ast.ImportFrom):
            _check_import(node)
            continue
        if isinstance(node, ast.FunctionDef):
            _check_funcdef(node)
            continue
        if isinstance(node, ast.Name):
            if node.id.startswith("__") and node.id.endswith("__"):
                raise ValidationError(f"dunder name '{node.id}' is not allowed")
            continue

        if not isinstance(node, _ALLOWED_NODES):
            raise ValidationError(
                f"construct '{type(node).__name__}' is not allowed in a contract"
            )


def _check_import(node: ast.ImportFrom) -> None:
    mod = node.module or ""
    if node.level != 0 or mod not in _ALLOWED_IMPORT_FROM:
        raise ValidationError(f"import from '{mod or '<relative>'}' is not allowed")
    for alias in node.names:
        name = alias.name
        if mod == "stdlib" and name not in _ALLOWED_STDLIB:
            raise ValidationError(f"stdlib import '{name}' is not allowed")
        if mod == "__future__" and name not in _ALLOWED_FUTURE:
            raise ValidationError(f"__future__ import '{name}' is not allowed")
        if mod == "typing" and name not in _ALLOWED_TYPING:
            raise ValidationError(f"typing import '{name}' is not allowed")


def _check_funcdef(node: ast.FunctionDef) -> None:
    if node.decorator_list:
        raise ValidationError(f"decorators on '{node.name}' are not allowed")
    a = node.args
    if a.vararg or a.kwarg or a.kwonlyargs or a.posonlyargs or a.defaults or a.kw_defaults:
        raise ValidationError(
            f"function '{node.name}' may only use plain positional params"
        )
    if len(a.args) > 16:
        raise ValidationError(f"function '{node.name}' has too many params")


__all__ = ["validate_for_execution", "ValidationError"]
