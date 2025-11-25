"""
Animica zk.integration
======================

High-level, *one-stop* entrypoints for zero-knowledge proof verification.

This package stitches together:
- **zk.registry**: maps stable verifier *kinds* → concrete Python callables.
- **zk.adapters.omni_bridge**: converts a canonical ProofEnvelope into the
  exact argument shape a verifier expects, and invokes it safely.

Typical usage
-------------
1) Verify from a full envelope (recommended):

    from zk.integration import verify
    ok = verify(envelope=proof_envelope_dict)

2) Verify by kind with explicit arguments:

    from zk.integration import verify
    ok = verify(kind="groth16_bn254", proof=..., vk=..., public_inputs=...)

3) Registry utilities:

    from zk.integration import list_kinds, register
    print(list_kinds())
    register("my_snark", "my_pkg.my_verifier", func="verify")

Utilities
---------
- `load_registry_snapshot()` returns (registry_yaml, vk_cache_json) as dicts.
- Re-exports most registry error/primitive types for convenience.

Notes
-----
- All verifiers must return a truthy value on success.
- This module does not perform signature verification of VK cache entries;
  use `zk/registry/update_vk.py verify ...` for that.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Re-export registry surface
from zk.registry import (
    VerifierSpec,
    RegistryError,
    AlreadyRegistered,
    NotRegistered,
    ImportFailure,
    MissingField,
    register,
    unregister,
    get,
    list_kinds,
    resolve,
    verify as verify_by_kind,
)

# Envelope bridge (canonical ProofEnvelope → specific verifier inputs)
try:
    # Only import the *stable* entrypoints to avoid tight coupling.
    from zk.adapters.omni_bridge import verify_envelope  # type: ignore
except Exception:  # pragma: no cover
    # Soft-fail import to keep this package importable in minimal envs
    def verify_envelope(*_args: Any, **_kwargs: Any) -> bool:  # type: ignore
        raise ImportError("zk.adapters.omni_bridge is not available in this build")


__all__ = [
    "__version__",
    # High-level API
    "verify",
    "verify_envelope",
    "load_registry_snapshot",
    # Registry re-exports
    "VerifierSpec",
    "RegistryError",
    "AlreadyRegistered",
    "NotRegistered",
    "ImportFailure",
    "MissingField",
    "register",
    "unregister",
    "get",
    "list_kinds",
    "resolve",
    "verify_by_kind",
]

__version__ = "0.1.0"


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def verify(
    *,
    envelope: Optional[Dict[str, Any]] = None,
    kind: Optional[str] = None,
    **kwargs: Any,
) -> bool:
    """
    Verify either a full ProofEnvelope **or** by (kind, kwargs).

    Exactly one of `envelope` or `kind` must be provided.

    When `envelope` is provided, this calls:
        zk.adapters.omni_bridge.verify_envelope(envelope)

    When `kind` is provided, this resolves the verifier via:
        fn = zk.registry.resolve(kind); then returns bool(fn(**kwargs))

    Returns:
        bool: True on successful verification.

    Raises:
        ValueError: if arguments are inconsistent.
        RegistryError / ImportFailure: if kind cannot be resolved.
        Adapter-specific exceptions if the envelope mapping fails.
    """
    if (envelope is None) == (kind is None):
        raise ValueError("Provide exactly one of: envelope OR kind")

    if envelope is not None:
        return bool(verify_envelope(envelope))

    # kind path
    fn = resolve(kind)  # type: ignore[arg-type]
    return bool(fn(**kwargs))


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def load_registry_snapshot(
    registry_path: Optional[Path] = None,
    vk_cache_path: Optional[Path] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Load (registry.yaml, vk_cache.json) into Python dicts.

    Args:
        registry_path: path to zk/registry/registry.yaml (optional)
        vk_cache_path: path to zk/registry/vk_cache.json (optional)

    Returns:
        (registry_dict, vk_cache_dict)

    Notes:
        - YAML requires PyYAML if the registry file is not JSON.
        - If a file is missing, an empty structure is returned for that part.
    """
    # Resolve defaults relative to this package
    base = Path(__file__).resolve().parents[1] / "registry"
    registry_path = registry_path or (base / "registry.yaml")
    vk_cache_path = vk_cache_path or (base / "vk_cache.json")

    # Lazy import yaml (optional)
    try:
        import json, yaml  # type: ignore
    except Exception:
        import json
        yaml = None  # type: ignore

    # registry.yaml: accept JSON or YAML
    registry: Dict[str, Any] = {}
    if registry_path.exists():
        text = registry_path.read_text(encoding="utf-8")
        if text.lstrip().startswith("{"):
            registry = json.loads(text)
        else:
            if yaml is None:
                raise ImportError(
                    "registry.yaml appears to be YAML; install PyYAML to load it."
                )
            registry = yaml.safe_load(text) or {}
    else:
        registry = {}

    # vk_cache.json: JSON only
    vk_cache: Dict[str, Any] = {}
    if vk_cache_path.exists():
        import json
        with vk_cache_path.open("r", encoding="utf-8") as f:
            vk_cache = json.load(f)
    else:
        vk_cache = {"schema_version": "1", "entries": {}}

    # Basic normalization
    registry.setdefault("kinds", {})
    registry.setdefault("circuits", {})
    vk_cache.setdefault("entries", {})

    return registry, vk_cache
