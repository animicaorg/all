"""
execution.adapters.vm_entry — optional bridge to vm_py (feature-flagged)

This adapter provides a narrow, execution-layer–friendly interface for calling
into the deterministic Python VM (the `vm_py` package) *if and only if*:
  1) the package is importable at runtime, and
  2) the feature flag is enabled.

If either condition is not met, the functions in this module raise
`VmNotAvailable`, allowing callers (e.g., execution/runtime/contracts.py) to
gracefully degrade or short-circuit deploy/call semantics until the VM lands.

Environment flag
----------------
Set ANIMICA_VM_ENABLED=1 to enable calls. Any falsy value (0, "", "false")
disables the adapter regardless of whether vm_py is installed.

Minimal expected vm_py surface (duck-typed)
-------------------------------------------
• vm_py.version.__version__
• vm_py.runtime.loader: load(manifest: Mapping, source: bytes|str|None=None, code: bytes|None=None)
    -> returns an opaque "program/module" handle suitable for execution
• vm_py.runtime.engine: has either:
    - Engine class with run_call(module, method, args, *, gas_limit, block_env, tx_env)
      returning an object/dict with fields/keys {return_data|ret|output, gas_used, logs}
      OR
    - run_call(module, method, args, *, gas_limit, block_env, tx_env) as a function.

The adapter is defensive and will attempt multiple attribute names when mapping
results (e.g., 'return_data', 'ret', or 'output').

Returned shape
--------------
VmExecResult(return_data: bytes, gas_used: int, logs: list[dict])

Notes
-----
• This adapter performs *no I/O* beyond importing vm_py. It is deterministic.
• It does not persist state; callers are responsible for state integration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Sequence

# -----------------------------------------------------------------------------
# Feature flag & availability
# -----------------------------------------------------------------------------

def _env_truthy(name: str, default: str = "0") -> bool:
    v = os.getenv(name, default)
    return str(v).strip().lower() in ("1", "true", "yes", "on")

VM_FEATURE_ENABLED: bool = _env_truthy("ANIMICA_VM_ENABLED", "0")

_VM_AVAILABLE = False
try:  # pragma: no cover - optional runtime dependency
    import importlib

    _vm_pkg = importlib.import_module("vm_py")
    _vm_version_mod = importlib.import_module("vm_py.version")
    _vm_loader = importlib.import_module("vm_py.runtime.loader")
    _vm_engine_mod = importlib.import_module("vm_py.runtime.engine")
    _VM_AVAILABLE = True
except Exception:
    _vm_pkg = None           # type: ignore
    _vm_version_mod = None   # type: ignore
    _vm_loader = None        # type: ignore
    _vm_engine_mod = None    # type: ignore


# -----------------------------------------------------------------------------
# Errors & result types
# -----------------------------------------------------------------------------

class VmNotAvailable(RuntimeError):
    """Raised when vm_py is not importable or the feature flag is disabled."""


class VmCallError(RuntimeError):
    """Raised when the underlying vm_py reports an execution error."""


@dataclass(frozen=True)
class VmExecResult:
    return_data: bytes
    gas_used: int
    logs: list[dict]


# -----------------------------------------------------------------------------
# Public helpers
# -----------------------------------------------------------------------------

def vm_is_available() -> bool:
    """Return True if vm_py is importable and the feature flag is enabled."""
    return VM_FEATURE_ENABLED and _VM_AVAILABLE


def vm_library_present() -> bool:
    """Return True if vm_py can be imported (ignores the feature flag)."""
    return _VM_AVAILABLE


def vm_version() -> str | None:
    """Return vm_py version string (if importable), else None."""
    if not _VM_AVAILABLE:
        return None
    ver = getattr(_vm_version_mod, "__version__", None)
    return str(ver) if ver is not None else None


# -----------------------------------------------------------------------------
# Execution entrypoints
# -----------------------------------------------------------------------------

def run_call(
    *,
    manifest: Mapping[str, Any],
    code: bytes | str | None,
    method: str,
    args: Sequence[Any] | Mapping[str, Any] | None,
    gas_limit: int,
    block_env: Mapping[str, Any],
    tx_env: Mapping[str, Any],
) -> VmExecResult:
    """
    Execute a contract call against the VM.

    Parameters
    ----------
    manifest : Mapping
        Contract manifest/ABI package (JSON-like structure).
    code : bytes|str|None
        Contract bytecode/source payload expected by vm_py.loader (None for
        “already deployed” programs if loader supports manifest-only).
    method : str
        Function name to invoke.
    args : Sequence|Mapping|None
        Positional or named arguments (VM ABI will validate/encode).
    gas_limit : int
        Maximum gas available to the VM execution.
    block_env, tx_env : Mapping
        Deterministic environment views provided by the execution layer.

    Returns
    -------
    VmExecResult

    Raises
    ------
    VmNotAvailable
    VmCallError
    """
    _ensure_enabled()

    # 1) Load/prepare program module via vm_py.runtime.loader
    program = _loader_load(manifest=manifest, code=code)

    # 2) Dispatch execution via vm_py.runtime.engine
    raw = _engine_run_call(
        program=program,
        method=method,
        args=args,
        gas_limit=gas_limit,
        block_env=block_env,
        tx_env=tx_env,
    )

    # 3) Normalize result
    return _coerce_result(raw)


# -----------------------------------------------------------------------------
# Internal vm_py shims
# -----------------------------------------------------------------------------

def _ensure_enabled() -> None:
    if not VM_FEATURE_ENABLED:
        raise VmNotAvailable(
            "vm_py execution is disabled by feature flag (set ANIMICA_VM_ENABLED=1)"
        )
    if not _VM_AVAILABLE:
        raise VmNotAvailable(
            "vm_py package not available. Install/enable the VM to execute contracts."
        )


def _loader_load(*, manifest: Mapping[str, Any], code: bytes | str | None) -> Any:
    """
    Call vm_py.runtime.loader.load(...) with some flexibility in parameter names.
    """
    loader = _vm_loader
    # Prefer 'load', fallback to alternate names if ever renamed.
    for fn_name in ("load", "load_manifest", "load_program"):
        fn = getattr(loader, fn_name, None)
        if fn:
            break
    else:
        raise VmCallError("vm_py.runtime.loader missing a load(...) entrypoint")

    try:
        # Attempt common parameter sets
        try:
            return fn(manifest=manifest, code=code)  # type: ignore[misc]
        except TypeError:
            # Some loaders might expect 'source' if code is textual.
            kw = {"manifest": manifest}
            if isinstance(code, str):
                kw["source"] = code
            else:
                kw["code"] = code
            return fn(**kw)  # type: ignore[misc]
    except Exception as e:  # pragma: no cover
        raise VmCallError(f"vm_py loader failed: {e}") from e


def _engine_run_call(
    *,
    program: Any,
    method: str,
    args: Sequence[Any] | Mapping[str, Any] | None,
    gas_limit: int,
    block_env: Mapping[str, Any],
    tx_env: Mapping[str, Any],
) -> Any:
    """
    Dispatch into vm_py.runtime.engine using either an Engine class or a module function.
    """
    eng_mod = _vm_engine_mod

    engine_cls = getattr(eng_mod, "Engine", None)
    if engine_cls is not None:
        engine = engine_cls(  # type: ignore[call-arg]
            gas_limit=gas_limit,
            block_env=dict(block_env),
            tx_env=dict(tx_env),
        )
        run_fn = getattr(engine, "run_call", None) or getattr(engine, "call", None)
        if not run_fn:
            raise VmCallError("vm_py Engine lacks run_call/call")
        try:
            return run_fn(program, method, args)  # type: ignore[misc]
        except Exception as e:  # pragma: no cover
            raise VmCallError(f"vm_py Engine.run_call failed: {e}") from e

    # Fallback to functional API
    for fn_name in ("run_call", "call"):
        fn = getattr(eng_mod, fn_name, None)
        if fn:
            try:
                return fn(  # type: ignore[misc]
                    program,
                    method,
                    args,
                    gas_limit=gas_limit,
                    block_env=dict(block_env),
                    tx_env=dict(tx_env),
                )
            except Exception as e:  # pragma: no cover
                raise VmCallError(f"vm_py engine.{fn_name} failed: {e}") from e

    raise VmCallError("vm_py.runtime.engine provides no Engine or run_call/call entrypoint")


def _coerce_result(raw: Any) -> VmExecResult:
    """
    Normalize various plausible vm_py return shapes into VmExecResult.
    """
    if raw is None:
        # Treat as success with no return/logs and zero gas (unlikely)
        return VmExecResult(return_data=b"", gas_used=0, logs=[])

    # Raw dict-like?
    if isinstance(raw, dict):
        ret = raw.get("return_data", raw.get("ret", raw.get("output", b"")))
        gas = int(raw.get("gas_used", raw.get("gas", 0)))
        logs = raw.get("logs", [])
        return VmExecResult(return_data=_as_bytes(ret), gas_used=gas, logs=list(logs))

    # Object with attributes?
    ret = getattr(raw, "return_data", None) or getattr(raw, "ret", None) or getattr(raw, "output", b"")
    gas = getattr(raw, "gas_used", None) or getattr(raw, "gas", 0)
    logs = getattr(raw, "logs", []) or []
    return VmExecResult(return_data=_as_bytes(ret), gas_used=int(gas), logs=list(logs))


def _as_bytes(x: Any) -> bytes:
    if x is None:
        return b""
    if isinstance(x, bytes):
        return x
    if isinstance(x, str):
        try:
            # Heuristics: hex string like "0x..." → decode; else utf-8
            s = x.strip()
            if s.startswith("0x"):
                return bytes.fromhex(s[2:])
            return s.encode("utf-8")
        except Exception:
            return x.encode("utf-8", errors="replace")
    if isinstance(x, (bytearray, memoryview)):
        return bytes(x)
    # Fallback to repr for unknown types to keep determinism
    return repr(x).encode("utf-8")


__all__ = [
    "VmNotAvailable",
    "VmCallError",
    "VmExecResult",
    "VM_FEATURE_ENABLED",
    "vm_is_available",
    "vm_library_present",
    "vm_version",
    "run_call",
]
