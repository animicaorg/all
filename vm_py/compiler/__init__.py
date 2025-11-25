"""
vm_py.compiler — front-door for the Animica Python-VM toolchain.

This package groups the compiler pipeline:
  • ast_lower        — Python AST → small, safe IR
  • ir               — IR datatypes (Instr, Block, Module, etc.)
  • typecheck        — lightweight type checker over IR
  • gas_estimator    — static upper-bound gas estimate from IR
  • encode           — stable (CBOR/msgspec) IR (de)serialization
  • symbols          — symbol table and dispatch map helpers
  • builtins_allowlist — canonical allowlist consulted by validate/loader

User-facing convenience helpers are provided here and import lazily so merely
importing `vm_py.compiler` has no heavy side-effects.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

# Re-export version for convenience (keeps subpackage self-describing)
try:
    from ..version import __version__ as __version__  # type: ignore
except Exception:  # pragma: no cover - defensive
    __version__ = "0.0.0"

__all__ = [
    # submodules (lazy)
    "ast_lower",
    "ir",
    "typecheck",
    "gas_estimator",
    "encode",
    "symbols",
    "builtins_allowlist",
    # helpers
    "compile_source_to_ir",
    "encode_ir",
    "decode_ir",
    "compile_and_encode",
]

# ----- Lazy submodule access (PEP 562 style) ---------------------------------
# This lets `from vm_py.compiler import ir` work without importing everything up front.

def __getattr__(name: str) -> Any:  # pragma: no cover - thin dispatch
    if name in {"ast_lower", "ir", "typecheck", "gas_estimator", "encode", "symbols", "builtins_allowlist"}:
        import importlib
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def __dir__() -> list[str]:  # pragma: no cover
    return sorted(list(globals().keys()) + [
        "ast_lower", "ir", "typecheck", "gas_estimator", "encode", "symbols", "builtins_allowlist"
    ])

# ----- Convenience helpers ----------------------------------------------------

def compile_source_to_ir(source: str, *, filename: str = "<contract>"):
    """
    Parse & validate Python source, then lower to IR.

    Returns:
        vm_py.compiler.ir.Module
    Raises:
        vm_py.errors.ValidationError, vm_py.errors.ForbiddenImport
        (or downstream CompileError if the lowerer/typechecker fails)
    """
    # Lazy imports keep import-time lightweight and avoid hard dependency cycles.
    from ..validate import validate_source
    from . import ast_lower, ir  # type: ignore

    tree = validate_source(source, filename=filename)
    ir_mod = ast_lower.lower_to_ir(tree, filename=filename)
    return ir_mod

def encode_ir(ir_module) -> bytes:
    """
    Encode an IR module into the canonical binary representation.
    """
    from . import encode  # type: ignore
    return encode.encode(ir_module)

def decode_ir(data: bytes):
    """
    Decode bytes into an IR module (inverse of encode_ir).
    """
    from . import encode  # type: ignore
    return encode.decode(data)

def compile_and_encode(source: str, *, filename: str = "<contract>") -> bytes:
    """
    End-to-end helper: source → IR → bytes.

    Useful for tests and quick tooling paths.
    """
    ir_mod = compile_source_to_ir(source, filename=filename)
    return encode_ir(ir_mod)
