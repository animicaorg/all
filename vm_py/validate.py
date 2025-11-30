"""
vm_py.validate — static validator for Animica's deterministic Python VM.

Goals
-----
* Enforce a tight, deterministic subset of Python syntax.
* Forbid dangerous imports/builtins (no I/O, time, randomness, reflection).
* Keep contracts small and analyzable (node-count / literal-size caps).
* Encourage the canonical pattern: `from stdlib import storage, events, abi, hash, treasury, syscalls`.

Public API
----------
validate_source(source: str, *, filename: str = "<contract>") -> ast.AST
    Parses and validates `source`. Returns the AST on success or raises
    vm_py.errors.ValidationError / ForbiddenImport with structured context.

This module does **not** execute code. It is purely syntactic/semantic validation
for the VM's compiler/runtime to consume safely.
"""

from __future__ import annotations

import ast
import builtins as _py_builtins
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

from .errors import ForbiddenImport, ValidationError

# --- Config (with safe fallbacks if config values evolve) ---------------------

try:
    from . import config as _cfg  # type: ignore
except Exception:  # pragma: no cover - defensive

    class _cfg:
        STRICT_MODE = True
        MAX_SOURCE_BYTES = 64 * 1024
        MAX_AST_NODES = 5000
        MAX_FUNC_ARGS = 8
        MAX_NESTED_FUNC_DEPTH = 4
        MAX_LITERAL_BYTES = 16 * 1024


# Only these stdlib surfaces are intended for contract use.
ALLOWED_STDLIB_NAMES: Set[str] = {
    "storage",
    "events",
    "abi",
    "hash",
    "treasury",
    "syscalls",
}

# Imports must be from 'stdlib' only (see rules in visitor below).
ALLOWED_IMPORT_MODULES: Set[str] = {"stdlib"}

# Whitelisted builtins (deterministic, pure, and small).
# NOTE: The compiler/runtime may further gate behavior; this is an upper bound.
DEFAULT_ALLOWED_BUILTINS: Set[str] = {
    "len",
    "range",
    "enumerate",
    "reversed",
    "min",
    "max",
    "abs",
    "all",
    "any",
    "sum",
    "bool",
    "int",
    "bytes",
    "sorted",
}

# Anything not in `DEFAULT_ALLOWED_BUILTINS` and present in Python's builtins
# is implicitly disallowed (e.g., open, eval, exec, getattr, setattr, __import__, print, dir, vars, input, etc.).

# Allowed AST node types (conservative; expanded only as needed for the VM spec)
_ALLOWED_AST_NODES: Tuple[type, ...] = (
    ast.Module,
    ast.Expr,  # allow docstring / simple const expressions
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Del,  # we still prevent 'del' via explicit check; included to parse
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.If,
    ast.For,
    ast.While,
    ast.Break,
    ast.Continue,
    ast.Pass,
    ast.Return,
    ast.FunctionDef,
    ast.arguments,
    ast.arg,
    ast.Call,
    ast.keyword,
    ast.IfExp,
    ast.Subscript,
    ast.Slice,
    ast.Index if hasattr(ast, "Index") else ast.Slice,  # py<3.9 compat
    ast.Tuple,
    ast.List,
    ast.Dict,
    ast.Set,
    ast.Attribute,
    ast.Constant,
    ast.ImportFrom,
    ast.Import,
    ast.With,  # will be rejected by explicit rule
)

# Disallowed higher-level constructs (non-exhaustive; enforced explicitly)
_DISALLOWED_NODE_TYPES: Tuple[type, ...] = (
    ast.ClassDef,
    ast.Lambda,
    ast.Try,
    ast.Raise,
    ast.Yield,
    ast.YieldFrom if hasattr(ast, "YieldFrom") else ast.Yield,  # pragma: no cover
    ast.GeneratorExp,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.AsyncFunctionDef,
    ast.Await if hasattr(ast, "Await") else ast.Call,  # pragma: no cover
    ast.Global,
    ast.Nonlocal,
    ast.Match if hasattr(ast, "Match") else ast.Call,  # structural pattern matching
)

# --- Helper dataclasses -------------------------------------------------------


@dataclass
class _Scope:
    """Tracks local/defined names for builtin resolution."""

    locals: Set[str]


# --- Validator ----------------------------------------------------------------


class _Validator(ast.NodeVisitor):
    def __init__(self, *, filename: str) -> None:
        self.filename = filename
        self.node_count = 0
        self.func_depth = 0
        self.module_level = True
        self.scopes: List[_Scope] = [_Scope(locals=set())]
        self.defined_funcs: Set[str] = set()
        self.imported_stdlib: Set[str] = set()
        # Effective caps
        self.max_nodes = getattr(_cfg, "MAX_AST_NODES", 5000)
        self.max_func_args = getattr(_cfg, "MAX_FUNC_ARGS", 8)
        self.max_depth = getattr(_cfg, "MAX_NESTED_FUNC_DEPTH", 4)
        self.max_lit_bytes = getattr(_cfg, "MAX_LITERAL_BYTES", 16 * 1024)
        # Builtins allowlist (support override via config if provided)
        self.allowed_builtins: Set[str] = set(
            getattr(_cfg, "ALLOWED_BUILTINS", DEFAULT_ALLOWED_BUILTINS)
        )

    # --- Core traversal -------------------------------------------------------

    def generic_visit(self, node: ast.AST) -> None:
        self.node_count += 1
        if self.node_count > self.max_nodes:
            raise ValidationError(
                "AST too large",
                phase="ast",
                reason="node_limit",
                context={"limit": self.max_nodes, "filename": self.filename},
            )

        # Quick structural allow/deny before specialized checks
        if not isinstance(node, _ALLOWED_AST_NODES):
            if isinstance(node, _DISALLOWED_NODE_TYPES):
                raise ValidationError(
                    f"Disallowed syntax: {type(node).__name__}",
                    phase="ast",
                    reason="node_disallowed",
                    context={"node": type(node).__name__, "filename": self.filename},
                )
            # Anything else unknown is also disallowed
            raise ValidationError(
                f"Unsupported syntax: {type(node).__name__}",
                phase="ast",
                reason="node_unsupported",
                context={"node": type(node).__name__, "filename": self.filename},
            )

        super().generic_visit(node)

    # --- Module-level constraints --------------------------------------------

    def visit_Module(self, node: ast.Module) -> None:  # type: ignore[override]
        # Only allow: docstring, allowed imports, constant assignments, function defs.
        for idx, stmt in enumerate(node.body):
            if isinstance(stmt, ast.Expr) and isinstance(
                getattr(stmt, "value", None), ast.Constant
            ):
                # docstring or harmless constant at top; allowed anywhere but does nothing
                continue
            if isinstance(stmt, ast.ImportFrom):
                self._check_importfrom(stmt)
                continue
            if isinstance(stmt, ast.Import):
                self._check_import(stmt)
                continue
            if isinstance(stmt, ast.Assign) or isinstance(stmt, ast.AnnAssign):
                self._check_module_assignment(stmt)
                continue
            if isinstance(stmt, ast.FunctionDef):
                # record then validate
                if stmt.name.startswith("_"):
                    raise ValidationError(
                        "Function names must not start with underscore",
                        phase="ast",
                        reason="private_name",
                        context={"name": stmt.name},
                    )
                if stmt.name in self.defined_funcs:
                    raise ValidationError(
                        "Duplicate function name",
                        phase="ast",
                        reason="duplicate_symbol",
                        context={"name": stmt.name},
                    )
                self.defined_funcs.add(stmt.name)
                continue

            # Everything else at module scope is forbidden
            raise ValidationError(
                "Only imports from stdlib, constant assignments, and function definitions are allowed at module scope",
                phase="ast",
                reason="module_stmt_forbidden",
                context={"stmt": type(stmt).__name__},
            )

        # After a first pass, actually traverse deeply so we get per-node checks too
        for stmt in node.body:
            self.visit(stmt)

    def _check_module_assignment(self, stmt: Union[ast.Assign, ast.AnnAssign]) -> None:
        # Targets must be simple names at module scope, no attribute/index writes.
        targets: List[ast.AST] = []
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
            value = stmt.value
        else:
            targets = [stmt.target]
            value = stmt.value

        for t in targets:
            if not isinstance(t, ast.Name) or not isinstance(t.ctx, ast.Store):
                raise ValidationError(
                    "Module-scope assignment must be to a simple name",
                    phase="ast",
                    reason="module_assign_target",
                    context={"target": type(t).__name__},
                )
            if t.id.startswith("_"):
                raise ValidationError(
                    "Global names must not start with underscore",
                    phase="ast",
                    reason="private_name",
                    context={"name": t.id},
                )

        # Value must be a shallow constant or container of constants (no Calls/Attr).
        if not self._is_constant_like(value):
            raise ValidationError(
                "Module-scope assignment must use literal constants (no calls/attributes)",
                phase="ast",
                reason="module_assign_value",
                context={"value": type(value).__name__},
            )

    def _is_constant_like(self, node: ast.AST, *, depth: int = 0) -> bool:
        if depth > 4:
            return False
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (bytes, bytearray)):
                return len(node.value) <= self.max_lit_bytes
            return True
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return all(
                self._is_constant_like(elt, depth=depth + 1) for elt in node.elts
            )
        if isinstance(node, ast.Dict):
            return all(
                (k is None or self._is_constant_like(k, depth=depth + 1))
                and self._is_constant_like(v, depth=depth + 1)
                for k, v in zip(node.keys, node.values)
            )
        return False

    # --- Import policy --------------------------------------------------------

    def _check_importfrom(self, node: ast.ImportFrom) -> None:
        if node.level and node.level != 0:
            raise ForbiddenImport(f".{node.level} relative import", symbol=None)
        if node.module not in ALLOWED_IMPORT_MODULES:
            raise ForbiddenImport(node.module or "<relative>", symbol=None)

        for alias in node.names:
            if alias.name == "*":
                raise ValidationError(
                    "Wildcard imports are not allowed",
                    phase="ast",
                    reason="import_wildcard",
                    context={"module": node.module},
                )
            if alias.asname and alias.asname != alias.name:
                # Allow aliasing to the same name only (no short/hidden names)
                raise ValidationError(
                    "Aliasing imported stdlib names is not allowed",
                    phase="ast",
                    reason="import_alias_forbidden",
                    context={"name": alias.name, "as": alias.asname},
                )
            if alias.name not in ALLOWED_STDLIB_NAMES:
                raise ForbiddenImport(node.module or "stdlib", symbol=alias.name)

            self.imported_stdlib.add(alias.name)
            self._scope().locals.add(alias.name)

    def _check_import(self, node: ast.Import) -> None:
        # The only tolerated form is: `import stdlib` (without alias).
        for alias in node.names:
            mod = alias.name
            if mod not in ALLOWED_IMPORT_MODULES or alias.asname not in (None, mod):
                raise ForbiddenImport(mod, symbol=alias.asname)
            # Keep the name `stdlib` in scope when `import stdlib` is used.
            self._scope().locals.add(mod)
            self.imported_stdlib.add("stdlib")

    # --- Function constraints -------------------------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # type: ignore[override]
        if node.decorator_list:
            raise ValidationError(
                "Decorators are not allowed",
                phase="ast",
                reason="decorator_forbidden",
                context={"function": node.name},
            )
        if node.returns is not None:
            # Type annotations are fine but we don't interpret them; allow simple names/attributes only
            if not isinstance(
                node.returns, (ast.Name, ast.Attribute, ast.Subscript, ast.Constant)
            ):
                raise ValidationError(
                    "Unsupported return annotation",
                    phase="ast",
                    reason="annotation_unsupported",
                    context={
                        "function": node.name,
                        "anno": type(node.returns).__name__,
                    },
                )

        # Args limits
        a: ast.arguments = node.args
        if any([a.vararg, a.kwarg, a.kwonlyargs, a.posonlyargs]):
            raise ValidationError(
                "varargs/kwargs/pos-only/kw-only arguments are not allowed",
                phase="ast",
                reason="varargs_forbidden",
                context={"function": node.name},
            )
        total_args = len(a.args)
        if total_args > self.max_func_args:
            raise ValidationError(
                "Too many function arguments",
                phase="ast",
                reason="arg_limit",
                context={
                    "function": node.name,
                    "args": total_args,
                    "limit": self.max_func_args,
                },
            )

        # Enter function scope
        self.func_depth += 1
        if self.func_depth > self.max_depth:
            raise ValidationError(
                "Function nesting depth exceeded",
                phase="ast",
                reason="depth_limit",
                context={"limit": self.max_depth, "function": node.name},
            )

        self.module_level = False
        self._push_scope()
        # Register function name and parameters in local scope to avoid misclassifying as builtins
        self._scope().locals.update({arg.arg for arg in a.args})
        # Visit body
        for stmt in node.body:
            self.visit(stmt)
        # Exit
        self._pop_scope()
        self.func_depth -= 1
        if self.func_depth == 0:
            self.module_level = True

    # --- Specific statement/expr checks --------------------------------------

    def visit_With(self, node: ast.With) -> None:  # type: ignore[override]
        raise ValidationError(
            "with-statements are not allowed",
            phase="ast",
            reason="with_forbidden",
            context={"filename": self.filename},
        )

    def visit_Raise(
        self, node: ast.Raise
    ) -> None:  # pragma: no cover - guarded earlier
        raise ValidationError(
            "raise is not allowed", phase="ast", reason="raise_forbidden"
        )

    def visit_Try(self, node: ast.Try) -> None:  # pragma: no cover - guarded earlier
        raise ValidationError(
            "try/except is not allowed", phase="ast", reason="try_forbidden"
        )

    def visit_Global(
        self, node: ast.Global
    ) -> None:  # pragma: no cover - guarded earlier
        raise ValidationError(
            "global is not allowed", phase="ast", reason="global_forbidden"
        )

    def visit_Nonlocal(
        self, node: ast.Nonlocal
    ) -> None:  # pragma: no cover - guarded earlier
        raise ValidationError(
            "nonlocal is not allowed", phase="ast", reason="nonlocal_forbidden"
        )

    def visit_Import(self, node: ast.Import) -> None:  # type: ignore[override]
        self._check_import(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # type: ignore[override]
        self._check_importfrom(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # type: ignore[override]
        # Ban dunder/private attrs anywhere.
        if node.attr.startswith("_"):
            raise ValidationError(
                "Access to private/dunder attributes is not allowed",
                phase="ast",
                reason="private_attr",
                context={"attr": node.attr},
            )
        # Allow traversal; deeper checks happen on Call.
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # type: ignore[override]
        # Forbid star-args and kw-expansion to keep static analyzability crisp
        if any(isinstance(a, ast.Starred) for a in node.args) or any(
            k.arg is None for k in node.keywords
        ):
            raise ValidationError(
                "Star-args and kwargs expansion are not allowed",
                phase="ast",
                reason="star_args_forbidden",
            )

        # Identify callee
        callee_ok = False
        callee_desc = ""

        if isinstance(node.func, ast.Name):
            name = node.func.id
            callee_desc = name
            if self._is_builtin_name(name):
                if name not in self.allowed_builtins:
                    raise ValidationError(
                        f"Use of builtin '{name}' is not allowed",
                        phase="ast",
                        reason="builtin_forbidden",
                    )
                callee_ok = True
            else:
                # Calls to user-defined functions are allowed
                if name.startswith("_"):
                    raise ValidationError(
                        "Calls to private names are not allowed",
                        phase="ast",
                        reason="private_call",
                        context={"name": name},
                    )
                callee_ok = True

        elif isinstance(node.func, ast.Attribute):
            # Allow stdlib.<module>.<func>(...) if user imported `import stdlib`
            # or <module>.<func>(...) if user imported `from stdlib import <module>`
            callee_desc = self._attr_chain(node.func)
            parts = callee_desc.split(".")
            if parts[0] == "stdlib":
                # Expect stdlib.<module>.<func>
                if (
                    len(parts) != 3
                    or parts[1] not in ALLOWED_STDLIB_NAMES
                    or parts[2].startswith("_")
                ):
                    raise ValidationError(
                        "Only stdlib.<module>.<func> calls are allowed",
                        phase="ast",
                        reason="stdlib_call_shape",
                        context={"call": callee_desc},
                    )
                if "stdlib" not in self.imported_stdlib:
                    # Must have been explicitly imported
                    raise ValidationError(
                        "Use of 'stdlib' requires `import stdlib`",
                        phase="ast",
                        reason="stdlib_not_imported",
                    )
                callee_ok = True
            else:
                # Expect <module>.<func> where <module> is an imported stdlib name
                if parts[0] not in self.imported_stdlib:
                    raise ValidationError(
                        "Calls on modules other than imported stdlib are not allowed",
                        phase="ast",
                        reason="module_call_forbidden",
                        context={"base": parts[0]},
                    )
                if len(parts) != 2 or parts[1].startswith("_"):
                    raise ValidationError(
                        "Only <module>.<func> calls are allowed",
                        phase="ast",
                        reason="module_call_shape",
                        context={"call": callee_desc},
                    )
                callee_ok = True

        else:
            raise ValidationError(
                "Unsupported call target",
                phase="ast",
                reason="call_target_unsupported",
                context={"target": type(node.func).__name__},
            )

        if not callee_ok:
            raise ValidationError(
                "Call not permitted",
                phase="ast",
                reason="call_forbidden",
                context={"callee": callee_desc},
            )

        # Recurse into args/keywords
        self.generic_visit(node)

    # --- Utility --------------------------------------------------------------

    def _attr_chain(self, node: ast.Attribute) -> str:
        parts: List[str] = [node.attr]
        base = node.value
        while isinstance(base, ast.Attribute):
            parts.append(base.attr)
            base = base.value
        if isinstance(base, ast.Name):
            parts.append(base.id)
        else:
            parts.append(type(base).__name__)
        return ".".join(reversed(parts))

    def _is_builtin_name(self, name: str) -> bool:
        # It's a builtin if present in Python builtins AND not shadowed by a local/param/import.
        if name not in _py_builtins.__dict__:
            return False
        return not self._is_defined(name)

    def _is_defined(self, name: str) -> bool:
        return (
            any(name in scope.locals for scope in reversed(self.scopes))
            or name in self.defined_funcs
        )

    def _push_scope(self) -> None:
        self.scopes.append(_Scope(locals=set()))

    def _pop_scope(self) -> None:
        self.scopes.pop()

    def _scope(self) -> _Scope:
        return self.scopes[-1]


# --- Public API ---------------------------------------------------------------


def validate_source(source: str, *, filename: str = "<contract>") -> ast.AST:
    """
    Parse + validate contract source. Returns the AST on success.

    Raises:
        ValidationError | ForbiddenImport
    """
    # Size guard (bytes)
    max_src = getattr(_cfg, "MAX_SOURCE_BYTES", 64 * 1024)
    if isinstance(source, str):
        size = len(source.encode("utf-8", "ignore"))
    else:  # pragma: no cover - defensive
        raise ValidationError("source must be str", phase="ast", reason="type")

    if size > max_src:
        raise ValidationError(
            "Source too large",
            phase="ast",
            reason="size_limit",
            context={"bytes": size, "limit": max_src},
        )

    try:
        tree = ast.parse(source, filename=filename, mode="exec", type_comments=False)
    except SyntaxError as e:
        raise ValidationError(
            "Syntax error",
            phase="ast",
            reason="syntax",
            context={
                "lineno": getattr(e, "lineno", None),
                "offset": getattr(e, "offset", None),
                "msg": e.msg,
            },
        ) from None

    validator = _Validator(filename=filename)
    validator.visit(tree)
    return tree


__all__ = [
    "validate_source",
    "ALLOWED_STDLIB_NAMES",
    "ALLOWED_IMPORT_MODULES",
    "DEFAULT_ALLOWED_BUILTINS",
]
