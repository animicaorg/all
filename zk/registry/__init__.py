"""
Animica zk.registry
===================

A tiny, threadsafe registry that maps **proof/verifier kinds** to concrete
Python callables, so the rest of the stack can select the right verifier
by a stable string key.

This is the single source of truth used by adapters (e.g. `zk.adapters.omni_bridge`)
to route a normalized ProofEnvelope → verifier implementation in `zk.verifiers.*`.

Design goals
------------
- Small surface: register, get, resolve, list, verify.
- Threadsafe updates (RLock).
- Helpful errors with explicit codes.
- Sensible defaults pre-registered for built-in verifiers.

Kinds (defaults)
----------------
- "groth16_bn254"     → zk.verifiers.groth16_bn254:verify
- "plonk_kzg_bn254"   → zk.verifiers.plonk_kzg_bn254:verify
- "kzg_bn254"         → zk.verifiers.kzg_bn254:verify
- "stark_fri_merkle"  → zk.verifiers.stark_fri:verify

(You can register more kinds at runtime if needed.)

License: MIT
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from threading import RLock
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

__version__ = "0.1.0"


# =============================================================================
# Errors
# =============================================================================


class RegistryError(Exception):
    """Base error for registry operations."""

    code: str = "REGISTRY_ERROR"

    def __init__(self, message: str, *, code: Optional[str] = None):
        super().__init__(message)
        if code:
            self.code = code
        self.message = message


class AlreadyRegistered(RegistryError):
    code = "ALREADY_REGISTERED"


class NotRegistered(RegistryError):
    code = "NOT_REGISTERED"


class ImportFailure(RegistryError):
    code = "IMPORT_FAILURE"


class MissingField(RegistryError):
    code = "MISSING_FIELD"


# =============================================================================
# Spec & storage
# =============================================================================


@dataclass(frozen=True)
class VerifierSpec:
    """Declarative binding of a kind → module:function (+ optional metadata)."""

    kind: str
    module: str
    func: str = "verify"
    description: str = ""
    tags: Tuple[str, ...] = ()
    version: str = "1"  # semantic for the *binding*, not cryptography


_REGISTRY: Dict[str, VerifierSpec] = {}
_LOCK = RLock()


# =============================================================================
# Core API
# =============================================================================


def register(
    kind: str,
    module: str,
    func: str = "verify",
    *,
    description: str = "",
    tags: Iterable[str] = (),
    version: str = "1",
    overwrite: bool = False,
) -> VerifierSpec:
    """
    Register or update a verifier kind mapping.

    Args:
        kind: stable identifier string (e.g., "groth16_bn254").
        module: import path (e.g., "zk.verifiers.groth16_bn254").
        func: callable name within the module (default "verify").
        description: human-friendly summary.
        tags: optional labels (scheme, curve, backend).
        version: binding version string (free-form).
        overwrite: if False, raising on duplicates; else replace existing.

    Returns:
        The frozen VerifierSpec stored in the registry.
    """
    if not kind:
        raise MissingField("kind must be a non-empty string")
    if not module:
        raise MissingField("module must be a non-empty string")
    if not func:
        raise MissingField("func must be a non-empty string")

    spec = VerifierSpec(
        kind=kind,
        module=module,
        func=func,
        description=description,
        tags=tuple(tags),
        version=version,
    )

    with _LOCK:
        if kind in _REGISTRY and not overwrite:
            raise AlreadyRegistered(f"Kind '{kind}' already registered")
        _REGISTRY[kind] = spec
    return spec


def unregister(kind: str, *, missing_ok: bool = False) -> None:
    """Remove a kind from the registry."""
    with _LOCK:
        if kind not in _REGISTRY:
            if missing_ok:
                return
            raise NotRegistered(f"Kind '{kind}' is not registered")
        _REGISTRY.pop(kind, None)


def get(kind: str) -> VerifierSpec:
    """Fetch the spec for a kind or raise NotRegistered."""
    with _LOCK:
        try:
            return _REGISTRY[kind]
        except KeyError:
            raise NotRegistered(f"Kind '{kind}' is not registered")


def list_kinds() -> List[str]:
    """List registered kinds (sorted)."""
    with _LOCK:
        return sorted(_REGISTRY.keys())


def resolve(kind: str) -> Callable[..., Any]:
    """
    Import and return the callable for a kind.
    Raises ImportFailure if import or attribute resolution fails.
    """
    spec = get(kind)
    try:
        mod = import_module(spec.module)
    except Exception as e:
        raise ImportFailure(
            f"Failed to import module '{spec.module}' for kind '{kind}': {e}"
        ) from e
    try:
        fn = getattr(mod, spec.func)
    except AttributeError as e:
        raise ImportFailure(
            f"Function '{spec.func}' not found in module '{spec.module}' for kind '{kind}'"
        ) from e
    if not callable(fn):
        raise ImportFailure(
            f"Resolved attribute '{spec.func}' in '{spec.module}' is not callable"
        )
    return fn


def verify(kind: str, /, **kwargs: Any) -> bool:
    """
    Convenience wrapper: resolve(kind)(**kwargs) → bool.

    The called verifier is expected to return a truthy value on success.
    """
    fn = resolve(kind)
    result = fn(**kwargs)
    return bool(result)


# =============================================================================
# Defaults
# =============================================================================


def _register_defaults() -> None:
    defaults = [
        VerifierSpec(
            kind="groth16_bn254",
            module="zk.verifiers.groth16_bn254",
            func="verify",
            description="Groth16 over BN254 (SnarkJS-compatible inputs)",
            tags=("groth16", "bn254"),
            version="1",
        ),
        VerifierSpec(
            kind="plonk_kzg_bn254",
            module="zk.verifiers.plonk_kzg_bn254",
            func="verify",
            description="PLONK with KZG over BN254 (PlonkJS/SnarkJS compatible)",
            tags=("plonk", "kzg", "bn254"),
            version="1",
        ),
        VerifierSpec(
            kind="kzg_bn254",
            module="zk.verifiers.kzg_bn254",
            func="verify",
            description="Minimal KZG opening verifier (BN254)",
            tags=("kzg", "bn254"),
            version="1",
        ),
        VerifierSpec(
            kind="stark_fri_merkle",
            module="zk.verifiers.stark_fri",
            func="verify",
            description="Toy STARK FRI verifier for Merkle-membership AIR",
            tags=("stark", "fri", "toy"),
            version="1",
        ),
    ]
    with _LOCK:
        for spec in defaults:
            _REGISTRY.setdefault(spec.kind, spec)


_register_defaults()


# =============================================================================
# Public exports
# =============================================================================

__all__ = [
    "__version__",
    # Spec & errors
    "VerifierSpec",
    "RegistryError",
    "AlreadyRegistered",
    "NotRegistered",
    "ImportFailure",
    "MissingField",
    # Core ops
    "register",
    "unregister",
    "get",
    "list_kinds",
    "resolve",
    "verify",
]
