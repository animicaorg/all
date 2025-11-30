# -*- coding: utf-8 -*-
"""
lint_contract.py
================

Static determinism linter for Animica Python contracts.

Goal
----
Enforce a conservative subset of Python that is safe & deterministic for
on-chain contracts (mirrors the intent of `contracts/CODESTYLE.md` and
`vm_py/validate.py`). This script does **static** checks only; it never
executes user code.

What it checks (high level)
---------------------------
Errors (fail the run):
  - DET001: Forbidden imports (I/O, network, clock, OS, randomness, etc.)
  - DET002: Forbidden builtins (open, eval, exec, compile, __import__)
  - DET003: Float literals (non-deterministic/precision pitfalls in contracts)
  - DET004: Builtin hash() usage (hash randomization salts → non-deterministic)
  - DET005: Nondeterministic dict/set iteration without sorting
  - DET006: Dynamic code execution (ast.Exec, eval/exec/compile)
  - DET010: (Detected) recursion or cycle in call graph (resource unbounded)
  - DET011: Top-level side effects (non-constant module-level statements)

Warnings (shown by default; can be promoted to errors with --strict):
  - DET020: getattr/setattr/delattr (metaprogramming can hide unsafe flows)
  - DET021: Class definitions with inheritance or metaclass usage
  - DET022: Ambiguous comprehension ordering on dict/set without sorted()
  - DET023: Broad try/except that could swallow determinism errors

Allowed imports
---------------
Only:
  - `from stdlib import storage, events, hash, abi, treasury, syscalls, random`
  - `import typing` (for annotations only)
  - `from typing import ...` (annotations)
  - `from __future__ import annotations`

Everything else is forbidden at contract-level.

CLI
---
Examples:

# Lint one file (human-readable):
python -m contracts.tools.lint_contract --source contracts/examples/counter/contract.py

# Lint multiple files and emit JSON:
python -m contracts.tools.lint_contract --source a.py b.py --json

# Treat warnings as errors:
python -m contracts.tools.lint_contract --source contract.py --strict

# Ignore specific rules:
python -m contracts.tools.lint_contract --source contract.py --ignore DET021 --ignore DET022

Exit codes:
  0: clean (no errors; warnings allowed unless --strict)
  1: has errors (or warnings when --strict)
  2: usage / I/O error

Note
----
This linter is intentionally conservative. If you need to widen/relax a rule,
update CODESTYLE first, then keep this in sync.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# -----------------------------------------------------------------------------
# Optional helpers from contracts.tools (pretty JSON)
# -----------------------------------------------------------------------------
try:
    from contracts.tools import canonical_json_str  # type: ignore
except Exception:

    def canonical_json_str(obj: Any) -> str:
        return json.dumps(
            obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )


# -----------------------------------------------------------------------------
# Rules & configuration
# -----------------------------------------------------------------------------

FORBIDDEN_IMPORT_MODULES: Set[str] = {
    # system / files / processes
    "os",
    "sys",
    "pathlib",
    "io",
    "tempfile",
    "subprocess",
    "shutil",
    # clocks / time / sleep
    "time",
    "datetime",
    "zoneinfo",
    # randomness outside stdlib.random (deterministic shim)
    "random",
    "secrets",
    # networking / IPC
    "socket",
    "selectors",
    "asyncio",
    "ssl",
    "http",
    "urllib",
    "requests",
    "grpc",
    # concurrency / threads / processes
    "threading",
    "multiprocessing",
    "concurrent",
    # FFI / native
    "ctypes",
    "cffi",
    # serialization with side-effects / unsafe
    "pickle",
    "dill",
    "marshal",
    # filesystem config parsers
    "configparser",
    # crypto/hashes not via stdlib.hash
    "hashlib",
    # reflection-heavy modules
    "importlib",
    "inspect",
}

# Allowed import patterns:
# - typing & __future__
# - stdlib.<allowed>
ALLOWED_STDLIB_SUBMODULES: Set[str] = {
    "storage",
    "events",
    "hash",
    "abi",
    "treasury",
    "syscalls",
    "random",
}

FORBIDDEN_BUILTINS: Set[str] = {
    "open",
    "eval",
    "exec",
    "compile",
    "__import__",
}

WARN_REFLECTIVE_BUILTINS: Set[str] = {
    "getattr",
    "setattr",
    "delattr",
}

# Methods that imply nondeterministic ordering unless wrapped by sorted()
DICT_ORDER_SENSITIVE_METHODS: Set[str] = {"keys", "items", "values"}

# Expression node types which obviously represent dict/set literals
DICT_LIKE_NODES = (ast.Dict,)
SET_LIKE_NODES = (ast.Set,)


@dataclass
class Violation:
    rule: str
    message: str
    severity: str  # "error" or "warning"
    file: str
    line: int
    col: int
    snippet: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# -----------------------------------------------------------------------------
# AST-based linter
# -----------------------------------------------------------------------------


class DeterminismLinter(ast.NodeVisitor):
    def __init__(self, filename: str, source_text: str, ignore: Set[str], strict: bool):
        self.filename = filename
        self.source_text = source_text
        self.lines = source_text.splitlines()
        self.ignore = ignore
        self.strict = strict

        self.violations: List[Violation] = []

        # Simple flow info for dict/set iteration detection
        self._name_is_dict_like: Set[str] = set()
        self._name_is_set_like: Set[str] = set()

        # Track a call graph to detect recursion
        self._current_func: List[str] = []
        self._func_calls: Dict[str, Set[str]] = {}  # f -> {g, h, ...}

        # Track top-level side effects
        self._module_level: int = 0

    # --------------------- helpers ---------------------

    def _emit(self, rule: str, msg: str, node: ast.AST, severity: str = "error"):
        if rule in self.ignore:
            return
        line = getattr(node, "lineno", 1)
        col = getattr(node, "col_offset", 0)
        snippet = None
        if 1 <= line <= len(self.lines):
            snippet = self.lines[line - 1].rstrip()
        self.violations.append(
            Violation(
                rule=rule,
                message=msg,
                severity=severity,
                file=self.filename,
                line=line,
                col=col,
                snippet=snippet,
            )
        )

    def _is_module_level(self) -> bool:
        return len(self._current_func) == 0 and self._module_level == 0

    def _name(self, node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    def _is_sorted_wrapper(self, node: ast.AST) -> bool:
        # sorted(x) or sorted(x.keys()) etc.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "sorted"
        ):
            return True
        return False

    def _is_call_to_builtin(self, node: ast.Call, builtin: str) -> bool:
        return isinstance(node.func, ast.Name) and node.func.id == builtin

    def _mark_name_kinds_on_assign(self, node: ast.Assign | ast.AnnAssign):
        # Track simple "name = {...}" or "name = set(...)" or "name = dict(...)"
        targets: List[ast.expr] = []
        value: Optional[ast.AST] = None

        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value

        if not value:
            return

        is_dict_like = isinstance(value, ast.Dict) or (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "dict"
        )
        is_set_like = isinstance(value, ast.Set) or (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "set"
        )

        if is_dict_like or is_set_like:
            for t in targets:
                if isinstance(t, ast.Name):
                    if is_dict_like:
                        self._name_is_dict_like.add(t.id)
                    if is_set_like:
                        self._name_is_set_like.add(t.id)

    # --------------------- module & defs ---------------------

    def visit_Module(self, node: ast.Module):
        # Walk children; mark non-trivial top-level as potential side effects
        # Allowed top-level nodes: Import, ImportFrom, FunctionDef, ClassDef,
        # Assign (constants only), AnnAssign (constants), Expr (docstring).
        for stmt in node.body:
            if isinstance(
                stmt,
                (
                    ast.Import,
                    ast.ImportFrom,
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                ),
            ):
                continue
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                # try to ensure constants: targets names and Constant value only
                val = stmt.value if isinstance(stmt, ast.Assign) else stmt.value
                if isinstance(val, ast.Constant):
                    continue
                # Typed constants with literal container ok (tuple/list of constants)
                if isinstance(val, (ast.Tuple, ast.List)) and all(
                    isinstance(e, ast.Constant) for e in val.elts
                ):
                    continue
                self._emit(
                    "DET011", "Top-level assignment must be constant literal.", stmt
                )
            elif isinstance(stmt, ast.Expr):
                # Allow docstring only: first statement is a string literal
                if not (
                    isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    self._emit(
                        "DET011",
                        "Top-level side-effectful expression is not allowed.",
                        stmt,
                    )
            else:
                self._emit(
                    "DET011",
                    f"Top-level statement {type(stmt).__name__} is not allowed.",
                    stmt,
                )

        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._func_calls.setdefault(node.name, set())
        self._current_func.append(node.name)
        # Parameters with *args/**kwargs are permitted but discouraged — no rule.
        self.generic_visit(node)
        self._current_func.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        # Async is not supported in VM runtime; flag as error.
        self._emit("DET001", "async functions are not permitted in contracts.", node)
        self._func_calls.setdefault(node.name, set())
        self._current_func.append(node.name)
        self.generic_visit(node)
        self._current_func.pop()

    def visit_ClassDef(self, node: ast.ClassDef):
        # Classes may be allowed, but inheritance/metaclasses are suspicious.
        if node.bases or any(
            isinstance(k, ast.keyword) and k.arg == "metaclass"
            for k in node.keywords or []
        ):
            self._emit(
                "DET021",
                "Class inheritance or metaclass usage is discouraged.",
                node,
                severity="warning",
            )
        self.generic_visit(node)

    # --------------------- imports ---------------------

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            mod = alias.name.split(".")[0]
            if mod == "typing" or mod == "__future__":
                continue
            if mod == "stdlib":
                # require explicit from-import for stdlib to keep surface tight
                self._emit(
                    "DET001",
                    "Use 'from stdlib import ...' instead of 'import stdlib'.",
                    node,
                )
                continue
            if mod in FORBIDDEN_IMPORT_MODULES:
                self._emit("DET001", f"Forbidden import: '{alias.name}'", node)
            else:
                self._emit(
                    "DET001",
                    f"Only stdlib/* and typing are allowed, got '{alias.name}'",
                    node,
                )

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module == "__future__":
            return
        if node.module == "typing":
            return
        if node.module and node.module.startswith("stdlib"):
            # Accept 'from stdlib import storage, ...'
            # Disallow nested 'from stdlib.storage import x' (unnecessary)
            if node.level != 0 or node.module != "stdlib":
                self._emit(
                    "DET001",
                    "Use 'from stdlib import {storage,events,hash,abi,treasury,syscalls,random}'",
                    node,
                )
                return
            for alias in node.names:
                if alias.name not in ALLOWED_STDLIB_SUBMODULES:
                    self._emit("DET001", f"'stdlib.{alias.name}' is not allowed", node)
            return

        # Anything else → forbidden
        self._emit("DET001", f"Forbidden import-from: '{node.module}'", node)

    # --------------------- assignments (track containers) ---------------------

    def visit_Assign(self, node: ast.Assign):
        self._mark_name_kinds_on_assign(node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        self._mark_name_kinds_on_assign(node)
        self.generic_visit(node)

    # --------------------- literals & calls ---------------------

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, float):
            self._emit("DET003", "Float literal is not allowed in contracts.", node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # Builtins: forbid
        if isinstance(node.func, ast.Name):
            fn = node.func.id
            if fn in FORBIDDEN_BUILTINS:
                self._emit("DET002", f"Forbidden builtin '{fn}'", node)
            if fn in WARN_REFLECTIVE_BUILTINS:
                self._emit(
                    "DET020",
                    f"Use of '{fn}' is discouraged in contracts.",
                    node,
                    severity="warning",
                )
            if fn == "hash":
                self._emit(
                    "DET004",
                    "Python builtin hash() is non-deterministic across runs.",
                    node,
                )

        # Attribute call: obj.method()
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            # dict iteration: d.keys()/values()/items()
            if attr in DICT_ORDER_SENSITIVE_METHODS:
                # Is it wrapped in sorted(...) ? Check parent in a hacky way via stack isn't trivial;
                # we conservatively check the node itself: if parent is a Call to sorted, it's fine.
                parent = getattr(node, "_parent", None)
                if (
                    not isinstance(parent, ast.Call)
                    or not isinstance(parent.func, ast.Name)
                    or parent.func.id != "sorted"
                ):
                    self._emit(
                        "DET005",
                        f"Iteration over dict.{attr}() without sorted() is non-deterministic.",
                        node,
                    )

        # Track call graph
        if self._current_func:
            callee = None
            if isinstance(node.func, ast.Name):
                callee = node.func.id
            elif isinstance(node.func, ast.Attribute):
                callee = node.func.attr
            if callee:
                self._func_calls.setdefault(self._current_func[-1], set()).add(callee)

        self.generic_visit(node)

    # --------------------- loops (dict/set iteration) ---------------------

    def visit_For(self, node: ast.For):
        # for x in <iterable>:
        target_iter = node.iter

        def _iterates_over_dict_or_set(it: ast.AST) -> bool:
            # Literal directly
            if isinstance(it, DICT_LIKE_NODES + SET_LIKE_NODES):
                return True
            # Name previously assigned as dict/set
            if isinstance(it, ast.Name):
                return (
                    it.id in self._name_is_dict_like or it.id in self._name_is_set_like
                )
            # Method call like d.keys()/items()/values()
            if isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute):
                if it.func.attr in DICT_ORDER_SENSITIVE_METHODS:
                    return True
            return False

        # If sorted(<...>) wrapper present, accept
        if not self._is_sorted_wrapper(target_iter) and _iterates_over_dict_or_set(
            target_iter
        ):
            self._emit(
                "DET005",
                "Iterating dict/set without sorted() has non-deterministic order.",
                node,
            )

        self.generic_visit(node)

    def visit_DictComp(self, node: ast.DictComp):
        # Dict comp order depends on input order; warn if based on a dict/set without sorted()
        self._emit(
            "DET022",
            "Dict comprehension may have non-deterministic iteration without sorted().",
            node,
            severity="warning",
        )
        self.generic_visit(node)

    def visit_SetComp(self, node: ast.SetComp):
        self._emit(
            "DET022",
            "Set comprehension may have non-deterministic iteration without sorted().",
            node,
            severity="warning",
        )
        self.generic_visit(node)

    # --------------------- try/except ---------------------

    def visit_Try(self, node: ast.Try):
        # Broad 'except:' with no type catches everything → can hide determinism errors.
        for h in node.handlers:
            if h.type is None:
                self._emit(
                    "DET023",
                    "Bare 'except:' is discouraged; catch explicit exceptions.",
                    h,
                    severity="warning",
                )
        self.generic_visit(node)

    # --------------------- recursion detection ---------------------

    def finalize(self):
        # Simple cycle detection in call graph
        visited: Set[str] = set()
        stack: Set[str] = set()

        def dfs(fn: str) -> bool:
            if fn in stack:
                return True
            if fn in visited:
                return False
            visited.add(fn)
            stack.add(fn)
            for callee in self._func_calls.get(fn, ()):
                if callee in self._func_calls and dfs(callee):
                    return True
            stack.remove(fn)
            return False

        for f in list(self._func_calls.keys()):
            if dfs(f):
                # Point to the first function in cycle (heuristic)
                # We don't have exact node; create a synthetic one with line 1.
                fake = ast.parse("pass").body[0]
                self._emit(
                    "DET010", f"Recursion/cycle detected starting at '{f}'.", fake
                )
                break

    # --------------------- generic override to keep parent links --------------

    def generic_visit(self, node):
        for child in ast.iter_child_nodes(node):
            setattr(child, "_parent", node)
            self.visit(child)


# -----------------------------------------------------------------------------
# Lint runner
# -----------------------------------------------------------------------------


@dataclass
class FileReport:
    file: str
    errors: int
    warnings: int
    violations: List[Violation]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file,
            "errors": self.errors,
            "warnings": self.warnings,
            "violations": [v.to_dict() for v in self.violations],
        }


def lint_source(path: Path, ignore: Set[str], strict: bool) -> FileReport:
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise RuntimeError(f"Failed to read {path}: {exc}") from exc

    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        v = Violation(
            rule="PARSE",
            message=f"SyntaxError: {exc}",
            severity="error",
            file=str(path),
            line=getattr(exc, "lineno", 1) or 1,
            col=getattr(exc, "offset", 0) or 0,
            snippet=None,
        )
        return FileReport(str(path), errors=1, warnings=0, violations=[v])

    linter = DeterminismLinter(str(path), src, ignore=ignore, strict=strict)
    linter.visit(tree)
    linter.finalize()

    errs = sum(1 for v in linter.violations if v.severity == "error")
    warns = sum(1 for v in linter.violations if v.severity == "warning")
    if strict:
        errs += warns
        warns = 0

    return FileReport(
        str(path), errors=errs, warnings=warns, violations=linter.violations
    )


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="contracts.tools.lint_contract",
        description="Static determinism linter for Animica Python contracts.",
    )
    p.add_argument(
        "--source",
        "-s",
        type=Path,
        nargs="+",
        required=True,
        help="Contract source file(s) to lint (.py).",
    )
    p.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON report."
    )
    p.add_argument(
        "--strict", action="store_true", help="Treat warnings as errors (CI mode)."
    )
    p.add_argument(
        "--ignore",
        "-I",
        action="append",
        default=[],
        help="Ignore specific rule id (e.g., DET021). Can be repeated.",
    )
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    ignore: Set[str] = set(args.ignore or [])

    reports: List[FileReport] = []
    total_errors = 0
    total_warnings = 0

    for src in args.source:
        if not src.exists():
            print(f"[lint] ERROR: file not found: {src}", file=sys.stderr)
            return 2
        if src.suffix != ".py":
            print(f"[lint] ERROR: not a .py file: {src}", file=sys.stderr)
            return 2
        rep = lint_source(src, ignore=ignore, strict=args.strict)
        reports.append(rep)
        total_errors += rep.errors
        total_warnings += rep.warnings

    if args.json:
        payload = {
            "summary": {
                "files": len(reports),
                "errors": total_errors,
                "warnings": total_warnings,
                "strict": bool(args.strict),
            },
            "reports": [r.to_dict() for r in reports],
        }
        print(canonical_json_str(payload))
    else:
        # Human-readable
        for r in reports:
            status = (
                "OK"
                if (r.errors == 0 and (r.warnings == 0 or not args.strict))
                else "FAIL"
            )
            print(f"\n{r.file}: {status}")
            for v in r.violations:
                sev = v.severity.upper()
                print(f"  [{sev}] {v.rule} L{v.line}:{v.col} — {v.message}")
                if v.snippet:
                    print(f"       │ {v.snippet}")
        print("\nSummary:")
        print(f"  Files:    {len(reports)}")
        print(f"  Errors:   {total_errors}")
        print(
            f"  Warnings: {total_warnings}{' (treated as errors)' if args.strict else ''}"
        )

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
