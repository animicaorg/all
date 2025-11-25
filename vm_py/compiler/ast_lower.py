"""
ast_lower.py — Lower validated Python AST to Animica VM's small, structured IR.

This module assumes the input Python AST has already been validated by
`vm_py.validate.validate_source` to enforce the determinism and safety rules
(forbidden imports/builtins, recursion limits, allowed nodes, etc.).

We lower to a *structured* IR defined in `vm_py.compiler.ir`, with the following
expected shapes (summarized for reference):

Expressions (ir.Expr):
- Const(value)                                 # ints, bools, bytes, str, None, tuples/lists/dicts composed of Const/Expr
- Name(name: str)
- BinOp(op: str, left: Expr, right: Expr)      # op in {"add","sub","mul","floordiv","mod","and","or","xor","lshift","rshift"}
- BoolOp(op: str, values: list[Expr])          # op in {"and","or"}; values length ≥ 2
- UnaryOp(op: str, operand: Expr)              # op in {"pos","neg","not","invert"}
- Compare(op: str, left: Expr, right: Expr)    # op in {"eq","ne","lt","le","gt","ge","in","not_in","is","is_not"}
- Call(func: Expr, args: list[Expr], kwargs: list[tuple[str, Expr]])
- Attribute(value: Expr, attr: str)
- Subscript(value: Expr, index: Expr)

Statements (ir.Stmt):
- Assign(targets: list[str] | list[list[str]], value: Expr)
  • For simple assignment, targets is ["x"].
  • For tuple-unpack, targets is [["a","b",...]] (one entry that is a list of names).
- ExprStmt(expr: Expr)
- Return(value: Expr | None)
- If(cond: Expr, then: list[Stmt], orelse: list[Stmt])
- While(cond: Expr, body: list[Stmt])

Top-level:
- Function(name: str, params: list[str], body: list[Stmt])
- Module(filename: str, functions: dict[str, Function])

If your IR evolves, adjust the constructors in `_IR` adapter below to match.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import ast

from ..errors import CompileError
from . import ir as _ir  # The target IR module (created elsewhere in vm_py.compiler)


# ---- Op tables ----------------------------------------------------------------

_BIN_OP = {
    ast.Add: "add",
    ast.Sub: "sub",
    ast.Mult: "mul",
    ast.FloorDiv: "floordiv",
    ast.Mod: "mod",
    ast.BitAnd: "and",
    ast.BitOr: "or",
    ast.BitXor: "xor",
    ast.LShift: "lshift",
    ast.RShift: "rshift",
}

_BOOL_OP = {
    ast.And: "and",
    ast.Or: "or",
}

_UNARY_OP = {
    ast.UAdd: "pos",
    ast.USub: "neg",
    ast.Not: "not",
    ast.Invert: "invert",
}

_CMP_OP = {
    ast.Eq: "eq",
    ast.NotEq: "ne",
    ast.Lt: "lt",
    ast.LtE: "le",
    ast.Gt: "gt",
    ast.GtE: "ge",
    ast.In: "in",
    ast.NotIn: "not_in",
    ast.Is: "is",
    ast.IsNot: "is_not",
}

# ---- Helpers ------------------------------------------------------------------


@dataclass(frozen=True)
class _Span:
    filename: str
    lineno: int
    col: int


def _span(node: ast.AST, filename: str) -> _Span:
    ln = getattr(node, "lineno", 0) or 0
    col = getattr(node, "col_offset", 0) or 0
    return _Span(filename, ln, col)


def _fail(node: ast.AST, filename: str, msg: str) -> CompileError:
    s = _span(node, filename)
    return CompileError(f"{msg} at {s.filename}:{s.lineno}:{s.col}")


def _is_const_literal(n: ast.AST) -> bool:
    if isinstance(n, ast.Constant):
        return isinstance(n.value, (int, bool, bytes, str, type(None)))
    if isinstance(n, (ast.Tuple, ast.List)):
        return all(_is_const_literal(elt) for elt in n.elts)
    if isinstance(n, ast.Dict):
        return all(_is_const_literal(k) and _is_const_literal(v) for k, v in zip(n.keys, n.values))
    return False


# ---- Lowering -----------------------------------------------------------------


class NodeLowerer:
    def __init__(self, filename: str):
        self.filename = filename

    # -- Entry points --

    def lower_module(self, mod: ast.Module) -> _ir.Module:
        functions: dict[str, _ir.Function] = {}
        for node in mod.body:
            if isinstance(node, ast.FunctionDef):
                fn = self.lower_function(node)
                if fn.name in functions:
                    raise _fail(node, self.filename, f"duplicate function {fn.name!r}")
                functions[fn.name] = fn
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                # The validator should already block arbitrary imports; allow only
                # `from stdlib import ...` style if validator permits. We don't lower imports.
                raise _fail(node, self.filename, "imports not allowed at top level")
            elif isinstance(node, ast.Expr) and _is_docstring_node(node):
                # Skip module docstring.
                continue
            else:
                raise _fail(node, self.filename, f"unsupported top-level statement: {type(node).__name__}")
        return _ir.Module(filename=self.filename, functions=functions)

    def lower_function(self, fn: ast.FunctionDef) -> _ir.Function:
        # Parameters: only simple args, no varargs/kwargs-only/posonly
        if (fn.args.vararg or fn.args.kwarg or fn.args.kwonlyargs or fn.args.posonlyargs):
            raise _fail(fn, self.filename, "only simple positional parameters are allowed")
        params = [a.arg for a in fn.args.args]
        body_stmts = self._lower_block(fn.body)
        return _ir.Function(name=fn.name, params=params, body=body_stmts)

    # -- Statements --

    def _lower_block(self, body: Sequence[ast.stmt]) -> list[_ir.Stmt]:
        out: list[_ir.Stmt] = []
        for st in body:
            lowered = self.lower_stmt(st)
            if isinstance(lowered, list):
                out.extend(lowered)
            elif lowered is not None:
                out.append(lowered)
        return out

    def lower_stmt(self, st: ast.stmt) -> _ir.Stmt | list[_ir.Stmt] | None:
        if isinstance(st, ast.Return):
            value = None if st.value is None else self.lower_expr(st.value)
            return _ir.Return(value=value)

        if isinstance(st, ast.Assign):
            if len(st.targets) != 1:
                # Support chained a = b = expr by turning into two Assigns
                assigns: list[_ir.Stmt] = []
                val = self.lower_expr(st.value)
                for t in st.targets:
                    assigns.append(_ir.Assign(targets=self._lower_assign_target(t), value=val))
                return assigns
            targets = self._lower_assign_target(st.targets[0])
            value = self.lower_expr(st.value)
            return _ir.Assign(targets=targets, value=value)

        if isinstance(st, ast.AugAssign):
            target_names = self._lower_assign_target(st.target)
            if len(target_names) != 1 or not isinstance(target_names[0], str):
                raise _fail(st, self.filename, "augmented assignment requires a single simple name target")
            left = self.lower_expr(st.target)
            right = self.lower_expr(st.value)
            op = _BIN_OP.get(type(st.op))
            if op is None:
                raise _fail(st, self.filename, f"unsupported augmented operator: {type(st.op).__name__}")
            return _ir.Assign(
                targets=[target_names[0]],
                value=_ir.BinOp(op=op, left=left, right=right),
            )

        if isinstance(st, ast.Expr):
            # Skip docstrings at function-level too
            if _is_docstring_node(st):
                return None
            return _ir.ExprStmt(expr=self.lower_expr(st.value))

        if isinstance(st, ast.If):
            cond = self.lower_expr(st.test)
            then = self._lower_block(st.body)
            orelse = self._lower_block(st.orelse)
            return _ir.If(cond=cond, then=then, orelse=orelse)

        if isinstance(st, ast.While):
            cond = self.lower_expr(st.test)
            body = self._lower_block(st.body)
            # 'else' on while is rare; validator likely disallows. We ignore orelse if empty.
            if st.orelse:
                raise _fail(st, self.filename, "while-else is not supported")
            return _ir.While(cond=cond, body=body)

        if isinstance(st, ast.Pass):
            return None

        # Disallow: For, With, Try, ClassDef, Assert, Break/Continue (unless validator later allows)
        raise _fail(st, self.filename, f"unsupported statement: {type(st).__name__}")

    def _lower_assign_target(self, t: ast.expr) -> list[str] | list[list[str]]:
        # Returns ["x"] or [["a","b",...]] for tuple unpack
        if isinstance(t, ast.Name):
            return [t.id]
        if isinstance(t, (ast.Tuple, ast.List)):
            names: list[str] = []
            for elt in t.elts:
                if not isinstance(elt, ast.Name):
                    raise _fail(elt, self.filename, "only simple names allowed in tuple/list assignment targets")
                names.append(elt.id)
            return [names]
        raise _fail(t, self.filename, "only simple names or tuple/list of names can be assignment targets")

    # -- Expressions --

    def lower_expr(self, e: ast.expr) -> _ir.Expr:
        if isinstance(e, ast.Constant):
            if not isinstance(e.value, (int, bool, bytes, str, type(None))):
                raise _fail(e, self.filename, f"unsupported constant type: {type(e.value).__name__}")
            return _ir.Const(e.value)

        if isinstance(e, ast.Name):
            if e.id in ("True", "False", "None"):
                # These typically come as ast.Constant in recent Python, but guard anyway:
                mapping = {"True": True, "False": False, "None": None}
                return _ir.Const(mapping[e.id])
            return _ir.Name(e.id)

        if isinstance(e, ast.Tuple):
            return _ir.Const(tuple(self.lower_expr(elt) if not _is_const_literal(elt) else _const_from_ast(elt)
                                  for elt in e.elts))

        if isinstance(e, ast.List):
            return _ir.Const([self.lower_expr(elt) if not _is_const_literal(elt) else _const_from_ast(elt)
                              for elt in e.elts])

        if isinstance(e, ast.Dict):
            items: list[tuple[Any, Any]] = []
            for k, v in zip(e.keys, e.values):
                if k is None:
                    raise _fail(e, self.filename, "**kwargs style dict unpack not supported")
                k_val = self.lower_expr(k) if not _is_const_literal(k) else _const_from_ast(k)
                v_val = self.lower_expr(v) if not _is_const_literal(v) else _const_from_ast(v)
                items.append((k_val, v_val))
            return _ir.Const(dict(items))

        if isinstance(e, ast.BinOp):
            op = _BIN_OP.get(type(e.op))
            if op is None:
                raise _fail(e, self.filename, f"unsupported binary operator: {type(e.op).__name__}")
            return _ir.BinOp(op=op, left=self.lower_expr(e.left), right=self.lower_expr(e.right))

        if isinstance(e, ast.BoolOp):
            op = _BOOL_OP.get(type(e.op))
            if op is None:
                raise _fail(e, self.filename, f"unsupported boolean operator: {type(e.op).__name__}")
            if len(e.values) < 2:
                raise _fail(e, self.filename, "boolean op requires at least two operands")
            return _ir.BoolOp(op=op, values=[self.lower_expr(v) for v in e.values])

        if isinstance(e, ast.UnaryOp):
            op = _UNARY_OP.get(type(e.op))
            if op is None:
                raise _fail(e, self.filename, f"unsupported unary operator: {type(e.op).__name__}")
            return _ir.UnaryOp(op=op, operand=self.lower_expr(e.operand))

        if isinstance(e, ast.Compare):
            # We only allow a single comparator (validator should enforce).
            if len(e.ops) != 1 or len(e.comparators) != 1:
                # Lower chained compares as AND of pairwise comparisons if ever permitted
                raise _fail(e, self.filename, "chained comparisons are not supported")
            op = _CMP_OP.get(type(e.ops[0]))
            if op is None:
                raise _fail(e, self.filename, f"unsupported comparison operator: {type(e.ops[0]).__name__}")
            return _ir.Compare(op=op, left=self.lower_expr(e.left), right=self.lower_expr(e.comparators[0]))

        if isinstance(e, ast.Call):
            func_expr = self._lower_callee(e.func)
            args = [self.lower_expr(a) for a in e.args]
            kwargs = []
            for kw in e.keywords:
                if kw.arg is None:
                    # No **kwargs unpacking in deterministic subset
                    raise _fail(e, self.filename, "dict unpack (**kwargs) not supported")
                kwargs.append((kw.arg, self.lower_expr(kw.value)))
            return _ir.Call(func=func_expr, args=args, kwargs=kwargs)

        if isinstance(e, ast.Attribute):
            val = self.lower_expr(e.value)
            return _ir.Attribute(value=val, attr=e.attr)

        if isinstance(e, ast.Subscript):
            val = self.lower_expr(e.value)
            # Python 3.9+: slice can be ast.Slice, ast.ExtSlice, ast.Tuple; we only allow simple index
            if isinstance(e.slice, ast.Slice):
                raise _fail(e, self.filename, "slicing not supported; only simple index")
            index = self.lower_expr(e.slice)
            return _ir.Subscript(value=val, index=index)

        if isinstance(e, ast.IfExp):
            # Lower `a if cond else b` to If with two branches that yield a value, represented as a
            # pseudo-call to a ternary helper in IR for simplicity.
            cond = self.lower_expr(e.test)
            then_v = self.lower_expr(e.body)
            else_v = self.lower_expr(e.orelse)
            return _ir.Call(func=_ir.Name("__ternary__"), args=[cond, then_v, else_v], kwargs=[])

        raise _fail(e, self.filename, f"unsupported expression: {type(e).__name__}")

    def _lower_callee(self, f: ast.expr) -> _ir.Expr:
        # Allow Name or chained Attribute (e.g., stdlib.storage.get)
        if isinstance(f, ast.Name):
            return _ir.Name(f.id)
        if isinstance(f, ast.Attribute):
            return _ir.Attribute(value=self._lower_callee(f.value), attr=f.attr)
        # Disallow lambdas and other dynamic callables in deterministic subset
        raise _fail(f, self.filename, "callable must be a name or attribute")

# ---- Public API ---------------------------------------------------------------


def lower_to_ir(tree: ast.AST, *, filename: str = "<contract>") -> _ir.Module:
    """
    Lower a validated Python AST into the structured Animica VM IR.

    Args:
        tree: An `ast.Module` produced by `ast.parse` and (ideally) validated by `vm_py.validate`.
        filename: Used only for error messages/spans.

    Returns:
        vm_py.compiler.ir.Module
    """
    if not isinstance(tree, ast.Module):
        raise CompileError("lower_to_ir expects an ast.Module")
    return NodeLowerer(filename).lower_module(tree)


# ---- Utilities ----------------------------------------------------------------


def _is_docstring_node(expr_stmt: ast.Expr) -> bool:
    return isinstance(expr_stmt.value, ast.Constant) and isinstance(expr_stmt.value.value, str)


def _const_from_ast(n: ast.AST) -> Any:
    """Best-effort constant extraction for nested literal structures."""
    if isinstance(n, ast.Constant):
        return n.value
    if isinstance(n, (ast.Tuple, ast.List)):
        return tuple(_const_from_ast(elt) for elt in n.elts) if isinstance(n, ast.Tuple) else [
            _const_from_ast(elt) for elt in n.elts
        ]
    if isinstance(n, ast.Dict):
        return { _const_from_ast(k): _const_from_ast(v) for k, v in zip(n.keys, n.values) }
    # Should not reach here if caller guarded with _is_const_literal
    raise TypeError(f"not a constant literal: {ast.dump(n, include_attributes=False)}")
