"""
Offline compiler adapter for Animica Python VM (vm_py).

This module provides a small, defensive wrapper around the vm_py toolchain so
studio-services can compile contract sources without talking to a node.

It tolerates minor API drift by probing a few common entrypoints exposed by
`vm_py.runtime.loader` and normalizes outputs to a stable `CompileArtifact`.

Typical usage:
    from studio_services.adapters.vm_compile import compile_source

    artifact = compile_source(source_text, manifest_dict)
    # -> artifact.code (bytes), artifact.code_hash ("0x…"), artifact.abi (dict)

If vm_py is not present, a clear VmCompileError is raised.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple, Callable


# ----------------------------- Errors & Result -------------------------------


class VmCompileError(Exception):
    """Raised when the offline compiler cannot run (missing vm_py or fatal compile error)."""


@dataclass
class CompileArtifact:
    """
    Normalized compile output used by studio-services.

    Attributes
    ----------
    code : bytes
        The compiled IR/bytecode blob (CBOR/msgpack as produced by vm_py).
    code_hash : str
        0x-prefixed SHA3-256 over `code`.
    abi : Dict[str, Any]
        ABI dictionary (functions/events/errors) suitable for clients.
    gas_upper_bound : int | None
        Optional static upper-bound estimate if available.
    manifest_out : Dict[str, Any] | None
        Normalized/filled manifest returned by the compiler (if provided).
    diagnostics : List[str]
        Human-friendly warnings/notes produced by the compiler, if any.
    """
    code: bytes
    code_hash: str
    abi: Dict[str, Any]
    gas_upper_bound: Optional[int] = None
    manifest_out: Optional[Dict[str, Any]] = None
    diagnostics: List[str] = None  # type: ignore[assignment]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # bytes → hex for JSON-friendliness
        d["code"] = "0x" + self.code.hex()
        return d


# ----------------------------- vm_py discovery -------------------------------

_vm_loader = None  # module-like object with compile/load helpers
_vm_gas_estimator = None  # optional static estimator
_vm_encode = None  # optional encode helpers (if needed)


def _import_vm() -> None:
    global _vm_loader, _vm_gas_estimator, _vm_encode
    if _vm_loader is not None:
        return
    try:
        # Primary entrypoint that "loads/validates/compiles" (per repo layout)
        from vm_py.runtime import loader as vm_loader  # type: ignore
        _vm_loader = vm_loader
    except Exception as e:
        raise VmCompileError(
            "vm_py is not installed or failed to import. Install the vm package in this environment."
        ) from e

    # Optional helpers; absence is fine.
    try:
        from vm_py.compiler import gas_estimator as vm_gas_estimator  # type: ignore
        _vm_gas_estimator = vm_gas_estimator
    except Exception:
        _vm_gas_estimator = None

    try:
        from vm_py.compiler import encode as vm_encode  # type: ignore
        _vm_encode = vm_encode
    except Exception:
        _vm_encode = None


# ----------------------------- Utilities -------------------------------------


def _sha3_256_hex(data: bytes) -> str:
    return "0x" + hashlib.sha3_256(data).hexdigest()


def code_hash_bytes(build: CompileArtifact | Dict[str, Any]) -> bytes:
    """Extract code hash bytes from a CompileArtifact or mapping."""

    if isinstance(build, CompileArtifact):
        h = build.code_hash
    elif isinstance(build, dict):
        h = build.get("code_hash") or build.get("codeHash") or build.get("code")
    else:
        raise VmCompileError("Unsupported build object for code_hash_bytes")

    if isinstance(h, (bytes, bytearray)):
        return bytes(h)

    if isinstance(h, str):
        hs = h.strip().lower()
        if hs.startswith("0x"):
            try:
                return bytes.fromhex(hs[2:])
            except ValueError as e:
                raise VmCompileError(f"Invalid code hash hex: {h!r}") from e
    raise VmCompileError("Could not extract code hash bytes")


def estimate_gas_for_deploy(build: CompileArtifact | Dict[str, Any]) -> Optional[int]:
    """Best-effort gas estimate accessor for deploy artifacts."""

    if isinstance(build, CompileArtifact):
        return build.gas_upper_bound
    if isinstance(build, dict):
        gv = build.get("gas_upper_bound") or build.get("gasUpperBound") or build.get("gas_estimate")
        try:
            return int(gv) if gv is not None else None
        except Exception:
            return None
    return None


def simulate_deploy_locally(build: CompileArtifact | Dict[str, Any], call_data: Dict[str, Any]):
    """Placeholder local simulation; returns None when unavailable."""

    # If vm_py exposes a simulator, wire it here in the future.
    return None


def _first_attr(obj: Any, names: Tuple[str, ...]) -> Any:
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


def _pick_abi(manifest: Dict[str, Any], compiler_value: Any) -> Dict[str, Any]:
    # Prefer ABI embedded in manifest, otherwise accept compiler-returned ABI.
    if isinstance(manifest, dict) and isinstance(manifest.get("abi"), dict):
        return manifest["abi"]  # type: ignore[return-value]
    if isinstance(compiler_value, dict):
        return compiler_value
    # Last resort: empty ABI (caller should validate earlier)
    return {}


def _as_bytes(maybe_bytes_or_hex: Any) -> Optional[bytes]:
    if isinstance(maybe_bytes_or_hex, (bytes, bytearray)):
        return bytes(maybe_bytes_or_hex)
    if isinstance(maybe_bytes_or_hex, str):
        s = maybe_bytes_or_hex.strip().lower()
        if s.startswith("0x"):
            try:
                return bytes.fromhex(s[2:])
            except ValueError:
                return None
    return None


# ----------------------------- Core compile ----------------------------------


def _call_vm_loader(source: str, manifest: Dict[str, Any]) -> Tuple[bytes, Dict[str, Any], Optional[int], Dict[str, Any], List[str]]:
    """
    Invoke vm_loader using several tolerant signatures and normalize the result.

    Returns
    -------
    (code_bytes, abi, gas_upper_bound, manifest_out, diagnostics)
    """
    _import_vm()
    loader = _vm_loader

    # Candidate callables and kwargs (probe in order)
    candidates: List[Tuple[str, Dict[str, Any]]] = [
        ("compile_source", {"source": source, "manifest": manifest}),
        ("load_from_source", {"manifest": manifest, "source": source}),
        ("load", {"manifest": manifest, "source": source}),
        ("compile", {"manifest": manifest, "source": source}),
    ]
    last_err: Optional[Exception] = None

    for name, kwargs in candidates:
        if not hasattr(loader, name):
            continue
        fn: Callable[..., Any] = getattr(loader, name)  # type: ignore
        try:
            try:
                result = fn(**kwargs)
            except TypeError:
                # Try positional (manifest, source)
                result = fn(manifest, source)

            # Normalize variety of shapes commonly seen:
            # 1) Dict with keys like {'ir_bytes'|'code'|'bytecode', 'abi', 'gas_upper_bound', 'manifest', 'diagnostics'}
            if isinstance(result, dict):
                code_b = None
                for k in ("ir_bytes", "code", "bytecode", "module_bytes", "ir"):
                    code_b = code_b or _as_bytes(result.get(k))
                if code_b is None:
                    # Sometimes code may already be bytes under some unknown key
                    for v in result.values():
                        vb = _as_bytes(v)
                        if vb is not None:
                            code_b = vb
                            break
                if code_b is None:
                    raise VmCompileError(f"{name} returned no recognizable code bytes")

                abi = _pick_abi(manifest, result.get("abi"))
                gas = None
                gv = result.get("gas_upper_bound") or result.get("gasUpperBound") or result.get("gas_estimate")
                if isinstance(gv, int):
                    gas = gv
                manifest_out = result.get("manifest") if isinstance(result.get("manifest"), dict) else None
                diags = result.get("diagnostics") if isinstance(result.get("diagnostics"), list) else []
                return code_b, abi, gas, (manifest_out or None), (diags or [])

            # 2) Object with attributes (common in internal loaders)
            code_b = _as_bytes(_first_attr(result, ("ir_bytes", "code", "bytecode", "module_bytes", "ir")))
            if code_b is None:
                raise VmCompileError(f"{name} produced an object without code bytes")
            abi_attr = _first_attr(result, ("abi",))
            gas_attr = _first_attr(result, ("gas_upper_bound", "gasUpperBound", "gas_estimate"))
            manifest_attr = _first_attr(result, ("manifest",))
            diags_attr = _first_attr(result, ("diagnostics",))
            abi = _pick_abi(manifest, abi_attr)
            gas = int(gas_attr) if isinstance(gas_attr, int) else None
            manifest_out = manifest_attr if isinstance(manifest_attr, dict) else None
            diags = diags_attr if isinstance(diags_attr, list) else []
            return code_b, abi, gas, (manifest_out or None), (diags or [])

        except Exception as e:
            last_err = e
            continue

    raise VmCompileError(f"vm_loader did not accept known signatures or failed: {last_err!r}")


def compile_source(source: str, manifest: Dict[str, Any]) -> CompileArtifact:
    """
    Compile Python contract source against a manifest using vm_py,
    returning a normalized CompileArtifact.

    Parameters
    ----------
    source : str
        Python contract source code.
    manifest : dict
        Contract manifest including ABI and metadata (validated by vm_py).

    Raises
    ------
    VmCompileError
        When vm_py is missing or compilation fails.
    """
    code_b, abi, gas, manifest_out, diags = _call_vm_loader(source, manifest)

    # Compute code hash (stable identifier used by verification)
    code_hash = _sha3_256_hex(code_b)

    # If gas upper bound wasn't provided, try static estimator (optional)
    if gas is None and _vm_gas_estimator is not None and hasattr(_vm_gas_estimator, "estimate_upper_bound"):
        try:
            # Some estimators accept IR bytes directly; others want a decoded IR/module.
            gas = int(_vm_gas_estimator.estimate_upper_bound(code_b))  # type: ignore[attr-defined]
        except Exception:
            # Best-effort only; keep None on failure
            gas = None

    return CompileArtifact(
        code=code_b,
        code_hash=code_hash,
        abi=abi,
        gas_upper_bound=gas,
        manifest_out=manifest_out,
        diagnostics=diags or [],
    )


# ----------------------------- File helpers ----------------------------------


def compile_files(source_path: str, manifest_path: str, encoding: str = "utf-8") -> CompileArtifact:
    """
    Convenience wrapper to compile from filesystem paths (used by tests/tools).
    """
    try:
        with open(source_path, "r", encoding=encoding) as f:
            src = f.read()
        import json
        with open(manifest_path, "r", encoding=encoding) as f:
            manifest = json.load(f)
    except Exception as e:
        raise VmCompileError(f"Failed to read inputs: {e!r}") from e

    return compile_source(src, manifest)


# Compatibility shim: some call sites import ``compile_package``.
def compile_package(source_path: str, manifest_path: str, encoding: str = "utf-8") -> CompileArtifact:
    return compile_files(source_path, manifest_path, encoding=encoding)


__all__ = [
    "VmCompileError",
    "CompileArtifact",
    "code_hash_bytes",
    "estimate_gas_for_deploy",
    "simulate_deploy_locally",
    "compile_source",
    "compile_files",
    "compile_package",
]
