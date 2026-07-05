"""
vm_py.runtime.loader — load manifest+source, validate, compile, and link stdlib.

This module provides a high-level API to turn a contract source + manifest into a
runtime-ready object that can be invoked deterministically via the VM engine.

It:
  1) Loads and validates a contract manifest (JSON/dict).
  2) Reads Python source(s).
  3) Runs validation (AST safety checks) and the compile pipeline (lower→typecheck→IR encode).
  4) Computes a stable code hash (sha3-256 over encoded IR).
  5) Links the in-process sandboxed stdlib (safe imports only).
  6) Returns a ContractRuntime wrapper with .call(...) helpers.

The API is deliberately resilient to minor naming differences across submodules:
if a function isn't present under one expected name, we try common alternates.

Manifest (minimal):
{
  "name": "counter",
  "version": "1.0.0",
  "source": "path/to/contract.py",           # OR "sources": ["a.py", ...]
  "abi": {...}                               # optional, passed through
}

Optional fields we accept but do not require:
- "entry": name of the entry module/class (reserved; not currently used)
- "exports": ["inc", "get"] (if provided, used to hint dispatch set)
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from . import manifest as manifest_utils

# --- Errors -----------------------------------------------------------------

try:
    from vm_py.errors import CompileError, ValidationError, VmError
except Exception:  # pragma: no cover

    class VmError(Exception):  # type: ignore
        pass

    class ValidationError(VmError):  # type: ignore
        pass

    class CompileError(VmError):  # type: ignore
        pass


# --- Optional sandbox/stdlib activation -------------------------------------

try:
    from vm_py.runtime import sandbox as _sandbox
except Exception:  # pragma: no cover
    _sandbox = None  # type: ignore


def _activate_sandbox() -> None:
    """Install the synthetic stdlib / import guard if available."""
    if _sandbox is None:
        return
    # Try common activators; ignore if not present.
    for name in ("activate", "install", "init", "enable"):
        fn = getattr(_sandbox, name, None)
        if callable(fn):
            fn()
            return
    # Fallback: if module exposes a context manager factory, enter it globally.
    ctx = getattr(_sandbox, "context", None)
    if callable(ctx):  # pragma: no cover
        ctx().__enter__()


# --- Compiler plumbing (duck-typed) -----------------------------------------

# AST validator
try:
    from vm_py import validate as _validator
except Exception:  # pragma: no cover
    _validator = None  # type: ignore

# Lowering / IR / encoding
try:
    from vm_py.compiler import ast_lower as _lower
    from vm_py.compiler import encode as _ir_encode
    from vm_py.compiler import gas_estimator as _gas
    from vm_py.compiler import ir as _ir_types
    from vm_py.compiler import typecheck as _typecheck
except Exception:  # pragma: no cover
    _lower = _typecheck = _ir_encode = _ir_types = _gas = None  # type: ignore

# Engine (interpreter)
try:
    from vm_py.runtime import engine as _engine
except Exception:  # pragma: no cover
    _engine = None  # type: ignore

# ABI dispatcher (for bytes payloads, optional)
try:
    from vm_py.runtime.abi import dispatch_call as _abi_dispatch
except Exception:  # pragma: no cover
    _abi_dispatch = None  # type: ignore

# Hashing (sha3-256)
try:
    import hashlib

    _sha3_256 = getattr(hashlib, "sha3_256")
except Exception:  # pragma: no cover
    _sha3_256 = None  # type: ignore


def _call_first(obj: Any, names: Iterable[str], *args, **kwargs):
    """Call the first attribute present on obj from names; raise if none."""
    last_err: Optional[BaseException] = None
    for nm in names:
        fn = getattr(obj, nm, None)
        if callable(fn):
            try:
                return fn(*args, **kwargs)
            except BaseException as e:  # surface first failure
                last_err = e
                continue
    if last_err:
        raise last_err
    raise AttributeError(f"none of {tuple(names)} found on {obj}")


# --- Data classes ------------------------------------------------------------


@dataclass
class CompileResult:
    name: str
    ir_bytes: bytes
    code_hash: str  # 0x-prefixed hex (sha3-256 over ir_bytes)
    gas_upper_bound: int  # static estimate (best-effort)
    exports: List[str]  # function names (best-effort)
    abi: Optional[Dict[str, Any]]


class EngineFacade:
    """
    Minimal adapter over vm_py.runtime.engine with a stable call API across
    slightly different engine module shapes.
    """

    def __init__(self) -> None:
        if _engine is None:
            raise VmError("vm_py.runtime.engine is not available")
        self._engine = _engine
        # If there's a class Engine, instantiate it once.
        self._inst = getattr(_engine, "Engine", None)
        if callable(self._inst):
            self._inst = self._inst()  # type: ignore

    def call(
        self,
        ir_bytes: bytes,
        method: str,
        args: List[Any],
        gas_limit: Optional[int] = None,
        block_env: Optional[Dict[str, Any]] = None,
        tx_env: Optional[Dict[str, Any]] = None,
    ) -> Any:
        eng = self._engine
        inst = self._inst

        # Try common function/method names, preferring explicit call entrypoints.
        if inst is not None:
            for nm in ("run_call", "call", "invoke"):
                fn = getattr(inst, nm, None)
                if callable(fn):
                    return fn(
                        ir_bytes,
                        method,
                        args,
                        gas_limit=gas_limit,
                        block_env=block_env,
                        tx_env=tx_env,
                    )  # type: ignore

        # Module-level helpers (functional style)
        for nm in ("run_call", "call", "invoke"):
            fn = getattr(eng, nm, None)
            if callable(fn):
                return fn(
                    ir_bytes,
                    method,
                    args,
                    gas_limit=gas_limit,
                    block_env=block_env,
                    tx_env=tx_env,
                )  # type: ignore

        # As a last resort, try a generic 'run' signature.
        fn = getattr(eng, "run", None)
        if callable(fn):
            return fn(ir_bytes, method, args, gas_limit, block_env, tx_env)  # type: ignore

        raise VmError("No compatible call entrypoint found in engine")


@dataclass
class ContractRuntime:
    """Runtime wrapper for a compiled contract."""

    compiled: CompileResult
    _engine: EngineFacade

    @property
    def name(self) -> str:
        return self.compiled.name

    @property
    def code_hash(self) -> str:
        return self.compiled.code_hash

    @property
    def abi(self) -> Optional[Dict[str, Any]]:
        return self.compiled.abi

    @property
    def exports(self) -> List[str]:
        return list(self.compiled.exports)

    def call(
        self,
        method: str,
        args: List[Any],
        *,
        gas_limit: Optional[int] = None,
        block_env: Optional[Dict[str, Any]] = None,
        tx_env: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Invoke a method with decoded arguments; returns a Python value."""
        if method not in self.compiled.exports and self.compiled.exports:
            # Keep permissive if exports unknown; otherwise guard.
            raise ValidationError(f"method '{method}' not exported by {self.name}")
        return self._engine.call(
            self.compiled.ir_bytes,
            method,
            args,
            gas_limit=gas_limit,
            block_env=block_env,
            tx_env=tx_env,
        )

    def call_bytes(
        self,
        payload: bytes,
        *,
        gas_limit: Optional[int] = None,
        block_env: Optional[Dict[str, Any]] = None,
        tx_env: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        """Invoke using ABI-encoded call bytes; returns ABI-encoded return bytes."""
        if _abi_dispatch is None:
            raise ValidationError("ABI dispatcher not available")

        # Decode→invoke→encode is handled by runtime.abi.dispatch_call,
        # but it needs a 'contract-like' object with exported methods.
        # Build a lightweight shim whose attributes call back into the engine.
        class _Shim:
            __slots__ = ("_rt",)

            def __init__(self, rt: ContractRuntime) -> None:
                self._rt = rt

            def __getattr__(self, name: str):
                if self._rt.exports and name not in self._rt.exports:
                    raise ValidationError(f"unknown method '{name}'")

                def _fn(*args):
                    return self._rt.call(
                        name,
                        list(args),
                        gas_limit=gas_limit,
                        block_env=block_env,
                        tx_env=tx_env,
                    )

                return _fn

        return _abi_dispatch(_Shim(self), payload)


# --- Loading & compiling -----------------------------------------------------

ManifestLike = manifest_utils.ManifestLike


def load_manifest(m: ManifestLike) -> Dict[str, Any]:
    """Load a manifest from path or mapping (compat wrapper)."""
    try:
        man, _path = manifest_utils.load_manifest_input(m)
        return man
    except Exception as exc:
        raise ValidationError(str(exc)) from exc


def _read_sources(
    manifest: Dict[str, Any],
    *,
    base: Optional[Path],
    manifest_path: Optional[Path],
) -> Tuple[str, List[Path]]:
    try:
        resolved = manifest_utils.resolve_contract_source(
            manifest,
            manifest_path=manifest_path,
            base_dir=base,
        )
    except Exception as exc:
        raise ValidationError(str(exc)) from exc
    return resolved.source_text, list(resolved.source_paths)


def _sha3_256_hex(data: bytes) -> str:
    if _sha3_256 is None:  # pragma: no cover
        raise VmError("sha3_256 not available in hashlib")
    return "0x" + _sha3_256(data).hexdigest()


def _best_effort_exports(manifest: Dict[str, Any], ir_module: Any) -> List[str]:
    # 1) From manifest.exports if present
    exp = manifest.get("exports")
    if isinstance(exp, list) and all(isinstance(x, str) for x in exp):
        return list(exp)
    # 2) From ABI if present (common shape: {"functions":[{"name":...}]})
    abi = manifest.get("abi")
    try:
        fnames = [f["name"] for f in abi.get("functions", []) if isinstance(f.get("name"), str)]  # type: ignore
        if fnames:
            return sorted(set(fnames))
    except Exception:
        pass
    # 3) From IR module metadata if available (duck-typed)
    for attr in ("exports", "public", "functions"):
        names = getattr(ir_module, attr, None)
        if isinstance(names, (list, tuple)) and all(isinstance(x, str) for x in names):
            return sorted(set(names))
    return []


def _lower_to_ir_module(source: str, *, name_hint: str) -> Any:
    if _lower is None:
        raise CompileError("vm_py.compiler.ast_lower is not available")
    try:
        from vm_py.compiler.lowering_adapter import lower_source_with_compat

        out, _meta = lower_source_with_compat(
            _lower,
            source=source,
            filename=name_hint,
        )
        return out
    except Exception as exc:  # noqa: BLE001
        raise CompileError(f"unable to lower source to IR: {exc}") from exc


def build_source_bundle_bytes(
    *,
    source: str,
    name_hint: str,
    manifest: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
) -> bytes:
    """
    Deterministic final fallback bytes when compiler paths are unavailable.
    """
    payload: Dict[str, Any] = {
        "format": "animica.pyexec.bundle.v1",
        "name": name_hint,
        "source": source,
    }
    if manifest is not None:
        payload["manifest_name"] = manifest.get("name")
        payload["manifest_version"] = manifest.get("version")
    if reason:
        payload["reason"] = reason
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compile_source_to_ir(
    source: str, name_hint: str = "contract"
) -> Tuple[bytes, Any, int]:
    """
    Run the compile pipeline and return (ir_bytes, ir_module_object, gas_upper_bound).
    """
    if _lower is None or _ir_encode is None:
        raise CompileError("compiler components not available (ast_lower/encode)")
    # 1) Validate AST (safety)
    if _validator is not None:
        for fn_name in ("validate_source", "validate", "check"):
            fn = getattr(_validator, fn_name, None)
            if callable(fn):
                fn(source)  # may raise ValidationError
                break
    # 2) Lower to IR
    ir_mod = _lower_to_ir_module(source, name_hint=name_hint)
    # 3) Typecheck (if present)
    if _typecheck is not None:
        _call_first(_typecheck, ("typecheck", "check", "validate"), ir_mod)
    # 4) Encode IR deterministically
    if hasattr(_ir_encode, "encode"):
        ir_bytes = _ir_encode.encode(ir_mod)  # type: ignore
    elif hasattr(_ir_encode, "to_bytes"):
        ir_bytes = _ir_encode.to_bytes(ir_mod)  # type: ignore
    else:
        raise CompileError("IR encoder not found (encode/to_bytes)")
    # 5) Gas upper-bound estimate (best effort)
    gas_ub = 0
    if _gas is not None:
        try:
            gas_ub = _call_first(
                _gas, ("estimate", "estimate_upper_bound", "upper_bound"), ir_mod
            )
            gas_ub = int(gas_ub)
        except Exception:
            gas_ub = 0
    return ir_bytes, ir_mod, gas_ub


def compile_from_manifest(
    manifest: ManifestLike, base_dir: Optional[Union[str, Path]] = None
) -> CompileResult:
    """
    High-level compile: manifest → CompileResult
    """
    man, manifest_path = manifest_utils.load_manifest_input(manifest)
    base_path = (
        Path(base_dir)
        if base_dir is not None
        else (
            manifest_path.parent if manifest_path is not None else Path.cwd()
        )
    )
    source, files = _read_sources(man, base=base_path, manifest_path=manifest_path)

    _activate_sandbox()

    name = str(man.get("name") or (files[0].stem if files else "contract"))
    ir_bytes, ir_mod, gas_ub = compile_source_to_ir(source, name_hint=name)
    code_hash = _sha3_256_hex(ir_bytes)
    exports = _best_effort_exports(man, ir_mod)
    abi = man.get("abi") if isinstance(man.get("abi"), dict) else None

    return CompileResult(
        name=name,
        ir_bytes=ir_bytes,
        code_hash=code_hash,
        gas_upper_bound=gas_ub,
        exports=exports,
        abi=abi,
    )


def compile_manifest(
    manifest: ManifestLike, base_dir: Optional[Union[str, Path]] = None
) -> Tuple[bytes, Dict[str, Any], Dict[str, Any]]:
    """
    Compatibility helper used by vm_py CLI paths.
    Returns (ir_bytes, abi, meta).
    """
    man, manifest_path = manifest_utils.load_manifest_input(manifest)
    resolved = manifest_utils.resolve_contract_source(
        man,
        manifest_path=manifest_path,
        base_dir=base_dir,
    )
    name_hint = str(man.get("name") or (resolved.entry or "contract"))
    abi = man.get("abi") if isinstance(man.get("abi"), dict) else {}

    try:
        compiled = compile_from_manifest(
            manifest_path if manifest_path is not None else man,
            base_dir=base_dir,
        )
        return (
            compiled.ir_bytes,
            abi,
            {
                "pipeline": "loader.compile_from_manifest",
                "code_hash": compiled.code_hash,
                "gas_upper_bound": compiled.gas_upper_bound,
                "exports": list(compiled.exports),
            },
        )
    except Exception as exc:
        # Deterministic source-bundle fallback keeps CLI workflows usable even
        # when lower-level compiler APIs drift.
        fallback = build_source_bundle_bytes(
            source=resolved.source_text,
            name_hint=name_hint,
            manifest=man,
            reason=str(exc),
        )
        return (
            fallback,
            abi,
            {
                "pipeline": "loader.source_bundle_fallback",
                "fallback": True,
                "compile_error": str(exc),
                "source_field": resolved.selected_field,
                "source_path": str(resolved.source_paths[0])
                if resolved.source_paths
                else None,
            },
        )


def run_call(
    manifest: ManifestLike,
    method: str,
    args: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    Execute a contract call directly from resolved source (pyexec fallback path).
    Returns {"result": ..., "meta": {...}}.
    """
    # ANM-C05/C06: this path exec()s raw contract source with full CPython
    # builtins, no AST validation and no gas metering — an unsandboxed RCE plus
    # unbounded-loop DoS on every validating node. It must NEVER run in consensus.
    # Fail closed: refuse unless an operator explicitly opts into the unsafe
    # developer path. Consensus/production contract execution must route through
    # the validated + metered Engine instead of this loader. When this raises,
    # execution.runtime.contracts catches it and deterministically REVERTs, so
    # mainnet (which never sets this flag) stays a no-op.
    if os.getenv("ANIMICA_VM_ALLOW_UNSAFE_EXEC") != "1":
        raise ValidationError(
            "loader.run_call raw exec() path is disabled (ANM-C05/C06): "
            "unsandboxed, unmetered contract execution is forbidden. Use the "
            "validated/metered engine; set ANIMICA_VM_ALLOW_UNSAFE_EXEC=1 only for "
            "isolated local development, never on a shared or value-bearing node."
        )
    call_args = list(args or [])
    man, manifest_path = manifest_utils.load_manifest_input(manifest)
    resolved = manifest_utils.resolve_contract_source(man, manifest_path=manifest_path)

    _activate_sandbox()
    scope: Dict[str, Any] = {"__name__": "__animica_vm_contract__"}
    exec(resolved.source_text, scope)  # noqa: S102
    fn = scope.get(method)
    if not callable(fn):
        raise ValidationError(
            f"method '{method}' not found in contract source (resolved via {resolved.selected_field})"
        )

    out = fn(*call_args)
    return {
        "result": out,
        "meta": {
            "pipeline": "loader.run_call.pyexec",
            "source_field": resolved.selected_field,
            "source_path": str(resolved.source_paths[0])
            if resolved.source_paths
            else None,
        },
    }


def make_runtime(compiled: CompileResult) -> ContractRuntime:
    """Wrap a CompileResult with an engine facade and return a runtime object."""
    _activate_sandbox()
    return ContractRuntime(compiled=compiled, _engine=EngineFacade())


def load_from_manifest(
    manifest: ManifestLike, base_dir: Optional[Union[str, Path]] = None
) -> ContractRuntime:
    """
    Convenience: compile_from_manifest + make_runtime.
    """
    return make_runtime(compile_from_manifest(manifest, base_dir))


__all__ = [
    "CompileResult",
    "ContractRuntime",
    "build_source_bundle_bytes",
    "compile_manifest",
    "compile_source_to_ir",
    "compile_from_manifest",
    "make_runtime",
    "load_manifest",
    "load_from_manifest",
    "run_call",
]
