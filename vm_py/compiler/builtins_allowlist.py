"""
builtins_allowlist.py — canonical allowlist for safe builtins & functions

This module centralizes which Python builtins and imports are allowed inside
Animica's deterministic Python-VM contracts. It is purposely strict:
  • Only a small set of obviously pure/deterministic builtins is allowed.
  • No I/O, no time, no randomness, no reflection, no dynamic imports.
  • Imports are limited to the synthetic `stdlib` the VM injects at runtime.

`validate.py` should use the helpers here to reject unsafe code at compile time.

If you need to expand the allowlist, consider determinism (no process/global
state or system-time), resource bounds, and gas predictability. Prefer adding
functionality via the VM's `stdlib` layer instead of whitelisting Python stdlib.

Exposed API
-----------
- ALLOWED_BUILTINS: mapping of builtin name → BuiltinRule
- BLOCKED_BUILTINS: explicit "never allow" set
- ALLOWED_IMPORTS: {module: set(allowed names)} — only 'stdlib' is permitted
- assert_allowed_builtin_call(name, argc, kwarg_names): raises on violation
- is_allowed_builtin(name): bool
- is_allowed_import(module, names): bool
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Sequence, Set

try:
    # Prefer the VM's ValidationError for consistent reporting.
    from ..errors import ValidationError  # type: ignore
except Exception:  # pragma: no cover

    class ValidationError(Exception):  # type: ignore
        pass


# ------------------------------ Builtin rules ------------------------------- #


@dataclass(frozen=True)
class BuiltinRule:
    """Static call-shape constraints for a builtin.

    NOTE: We only validate arity and kwarg *names* here; type validation is
    left to later passes (IR type checking) to keep this fast and simple.
    """

    min_args: int = 0
    max_args: Optional[int] = None  # None = unbounded (but >= min_args)
    allowed_kwargs: Set[str] = frozenset()
    forbid_kwargs: bool = False  # If True, kwargs not allowed at all
    note: str = ""  # documentation breadcrumb


def _rule(
    min_args: int,
    max_args: Optional[int],
    *,
    kwargs: Iterable[str] = (),
    forbid_kwargs: bool = False,
    note: str = "",
) -> BuiltinRule:
    return BuiltinRule(
        min_args=min_args,
        max_args=max_args,
        allowed_kwargs=frozenset(kwargs),
        forbid_kwargs=forbid_kwargs,
        note=note,
    )


# Deterministic, side-effect-free subset.
ALLOWED_BUILTINS: Dict[str, BuiltinRule] = {
    # Int/bytes/bool constructors — restricted shapes
    "int": _rule(
        1, 2, kwargs=("base",), note="Only positional value and optional base."
    ),
    "bytes": _rule(1, 1, note="From bytes-like; no encoding/str variants."),
    "bool": _rule(1, 1),
    # Arithmetic / logic helpers (pure)
    "abs": _rule(1, 1),
    "min": _rule(1, None),  # any arity >=1
    "max": _rule(1, None),
    # Sequences
    "len": _rule(1, 1),
    "sum": _rule(1, 2, note="Optional start only."),
    "all": _rule(1, 1),
    "any": _rule(1, 1),
    "enumerate": _rule(1, 2, kwargs=("start",)),
    "range": _rule(1, 3),
    "reversed": _rule(1, 1),
    # Sorting — deterministic only if key func is NOT used; we forbid 'key'
    "sorted": _rule(
        1, None, kwargs=("reverse",), note="No key= allowed; reverse=bool only."
    ),
}

# Builtins that are explicitly NEVER allowed (I/O, reflection, dynamic code, nondeterminism)
BLOCKED_BUILTINS: Set[str] = {
    "open",
    "print",
    "input",
    "eval",
    "exec",
    "compile",
    "__import__",
    "dir",
    "vars",
    "locals",
    "globals",
    "getattr",
    "setattr",
    "delattr",
    "hasattr",
    "super",
    "hash",  # nondeterministic across processes due to hash seed
    "memoryview",  # iteration/identity subtleties; avoid
    "format",
    "object",
    "type",
    "classmethod",
    "staticmethod",
    "property",
    "help",
    "quit",
    "exit",  # REPL niceties
}


def is_allowed_builtin(name: str) -> bool:
    """Return True if `name` is in the allowlist (and not explicitly blocked)."""
    n = str(name)
    return n in ALLOWED_BUILTINS and n not in BLOCKED_BUILTINS


def assert_allowed_builtin_call(
    name: str, argc: int, kwarg_names: Sequence[str]
) -> None:
    """Validate builtin call shape. Raise ValidationError on violation."""
    if name in BLOCKED_BUILTINS:
        raise ValidationError(f"Use of builtin '{name}' is forbidden")

    rule = ALLOWED_BUILTINS.get(name)
    if rule is None:
        raise ValidationError(f"Use of builtin '{name}' is not allowed")

    # Arity
    if argc < rule.min_args:
        raise ValidationError(
            f"Builtin '{name}' expects at least {rule.min_args} argument(s), got {argc}"
        )
    if rule.max_args is not None and argc > rule.max_args:
        raise ValidationError(
            f"Builtin '{name}' expects at most {rule.max_args} argument(s), got {argc}"
        )

    # Kwargs
    if rule.forbid_kwargs and kwarg_names:
        raise ValidationError(f"Builtin '{name}' does not accept keyword arguments")
    unknown = [k for k in kwarg_names if k not in rule.allowed_kwargs]
    if unknown:
        allowed_s = ", ".join(sorted(rule.allowed_kwargs)) or "none"
        raise ValidationError(
            f"Builtin '{name}' got unsupported kwargs: {unknown}; allowed: {allowed_s}"
        )

    # Extra semantic constraints we can check syntactically:
    if name == "sorted" and ("key" in kwarg_names):
        raise ValidationError("sorted(...): key= is not permitted for determinism")
    if name == "int" and ("base" in kwarg_names and argc == 1):
        # Ok: int(value, base=10) or int(value, base) — both shapes allowed; nothing else to do.
        pass


# ------------------------------ Imports ------------------------------------- #

# Only the VM-injected "stdlib" surface is importable. Everything else must be
# linked at compile time or accessed via VM host APIs.
ALLOWED_IMPORTS: Mapping[str, Set[str]] = {
    # from stdlib import storage, events, hash, abi, treasury, syscalls
    "stdlib": frozenset({"storage", "events", "hash", "abi", "treasury", "syscalls"}),
}


def is_allowed_import(module: str, names: Optional[Iterable[str]]) -> bool:
    """Return True if `import module` (and optional names) is allowed."""
    mod = str(module)
    allowed = ALLOWED_IMPORTS.get(mod)
    if allowed is None:
        return False
    if names is None:
        # `import stdlib` is NOT supported; we require explicit named imports.
        return False
    requested = set(str(n) for n in names)
    return requested.issubset(allowed)


def assert_allowed_import(module: str, names: Optional[Iterable[str]]) -> None:
    """Raise ValidationError if the import is not permitted."""
    if not is_allowed_import(module, names):
        if module not in ALLOWED_IMPORTS:
            raise ValidationError(f"Import of module '{module}' is not allowed")
        allowed = ", ".join(sorted(ALLOWED_IMPORTS[module]))
        raise ValidationError(
            f"Only named imports from '{module}' are allowed: {{{allowed}}}"
        )


__all__ = [
    "BuiltinRule",
    "ALLOWED_BUILTINS",
    "BLOCKED_BUILTINS",
    "ALLOWED_IMPORTS",
    "is_allowed_builtin",
    "assert_allowed_builtin_call",
    "is_allowed_import",
    "assert_allowed_import",
]
