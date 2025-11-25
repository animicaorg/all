# -*- coding: utf-8 -*-
"""
contracts.interfaces
====================

Lightweight registry for **contract interfaces** (reusable ABIs) used by
Animica standard libraries and application projects.

Why this exists
---------------
- Give contracts and tooling (e.g., `contracts/tools/*`, SDK codegen) a single
  canonical place to **look up** well-known ABIs by a stable name (e.g.,
  "Animica20", "Ownable", "Roles", "Permit").
- Keep things **pure-Python** and VM-friendly: simple dataclasses, no heavy
  deps, deterministic behavior.
- Allow projects to **extend or override** interfaces locally (e.g., add a
  project-specific interface) without patching the stdlib.

Conventions
-----------
- An interface is described by :class:`InterfaceSpec` with fields:
  - ``name``: stable identifier (string), e.g. "Animica20".
  - ``abi``: a JSON-like object (List[dict] or dict) matching
    ``contracts/schemas/abi.schema.json`` semantics. We accept "loose" shapes
    (either a list of entries or a dict with top-level { "functions": [...],
    "events": [...], "errors": [...] }).
  - ``version``: optional semver string for the interface itself.
  - ``description``: optional human text.

- Registry keys are case-sensitive. Prefer **UpperCamelCase** for canonical
  interface names. Use suffixes like "-Test" only for non-production variants.

- Minimal, defensive validation is performed to catch obvious mistakes without
  pulling in JSON-Schema:
  - ABI must be a list or dict with at least one of "functions"/"events"/"errors".
  - Each function entry should have "name" and "inputs"/"outputs" (if present)
    shaped as lists; we don't deeply validate every field here.

Usage
-----
Registering an interface (for example inside your project boot code or a
module import side-effect)::

    from contracts.interfaces import InterfaceSpec, register_interface

    MY_ERC20 = InterfaceSpec(
        name="Animica20",
        abi=[{"type": "function", "name": "balanceOf", "inputs": [{"name":"owner","type":"address"}], "outputs":[{"type":"uint256"}]}],
        version="1.0.0",
        description="Minimal Animica-20 read-only subset"
    )
    register_interface(MY_ERC20)

Querying later (e.g., in a tooling script)::

    from contracts.interfaces import get_abi
    erc20_abi = get_abi("Animica20")

Notes
-----
- This module intentionally **does not** auto-load files from disk; it is
  dependency-free. If you want to load interfaces from JSON files at runtime,
  do it in your app (read file → parse JSON → register_interface()).
- The contracts/tools helpers may register a set of common interfaces when
  invoked; if present, they'll appear in :func:`list_interfaces`.

"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple, Union, cast

__all__ = [
    "__version__",
    "InterfaceSpec",
    "InterfacesError",
    "register_interface",
    "register_many",
    "get_interface",
    "get_abi",
    "has_interface",
    "list_interfaces",
    "clear_registry",
]

__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Types & Errors
# ---------------------------------------------------------------------------

AbiEntry = Mapping[str, Any]
AbiList = List[AbiEntry]
AbiLike = Union[AbiList, Mapping[str, Any]]


class InterfacesError(ValueError):
    """Raised on invalid interface definitions or lookup failures."""


@dataclass(frozen=True)
class InterfaceSpec:
    """
    Description of a reusable contract interface (ABI bundle).

    Attributes
    ----------
    name : str
        Stable identifier used as the registry key.
    abi : AbiLike
        The ABI object—either a list of entries (preferred) or a dict containing
        "functions"/"events"/"errors" arrays. This is intentionally permissive.
    version : Optional[str]
        Semver of the interface (not the implementing contract).
    description : Optional[str]
        Short human-readable description.
    """
    name: str
    abi: AbiLike
    version: Optional[str] = None
    description: Optional[str] = None


# ---------------------------------------------------------------------------
# Internal registry
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, InterfaceSpec] = {}


# ---------------------------------------------------------------------------
# Validation (lightweight, non-exhaustive)
# ---------------------------------------------------------------------------

def _is_abi_list(obj: Any) -> bool:
    if not isinstance(obj, list):
        return False
    return all(isinstance(x, Mapping) and "type" in x for x in obj)


def _coerce_abi_to_list(abi: AbiLike) -> AbiList:
    """
    Accept either:
      - List[entry] where each entry has at least {"type": "..."}
      - Dict with any of {"functions","events","errors"} each a list of entries
    And return a single ABI list (concatenation order: functions, events, errors).
    """
    if _is_abi_list(abi):
        return cast(AbiList, list(abi))  # shallow copy

    if not isinstance(abi, Mapping):
        raise InterfacesError("ABI must be a list or dict")

    parts: AbiList = []
    for key in ("functions", "events", "errors"):
        v = abi.get(key)
        if v is None:
            continue
        if not isinstance(v, Sequence):
            raise InterfacesError(f"ABI field '{key}' must be a list when present")
        for entry in v:
            if not isinstance(entry, Mapping):
                raise InterfacesError(f"ABI '{key}' entries must be objects")
            if "type" not in entry:
                # infer if user forgot; be forgiving:
                inferred = "function" if key == "functions" else ("event" if key == "events" else "error")
                entry = dict(entry)
                entry.setdefault("type", inferred)
            parts.append(cast(AbiEntry, entry))
    if not parts:
        raise InterfacesError("ABI dict must contain at least one of functions/events/errors")
    return parts


def _validate_minimal(abi_list: AbiList) -> None:
    """
    Minimal shape checks. We deliberately avoid deep schema validation to keep
    this module dependency-free and VM-friendly.
    """
    for i, ent in enumerate(abi_list):
        t = ent.get("type")
        if t not in ("function", "event", "error", "constructor", "fallback", "receive"):
            raise InterfacesError(f"ABI entry #{i}: invalid 'type' {t!r}")
        if t == "function":
            if "name" not in ent or not isinstance(ent["name"], str):
                raise InterfacesError(f"ABI function #{i} missing/invalid 'name'")
            for key in ("inputs", "outputs"):
                if key in ent and not isinstance(ent[key], Sequence):
                    raise InterfacesError(f"ABI function #{i} '{key}' must be a list")
        elif t == "event":
            if "name" not in ent or not isinstance(ent["name"], str):
                raise InterfacesError(f"ABI event #{i} missing/invalid 'name'")
            if "inputs" in ent and not isinstance(ent["inputs"], Sequence):
                raise InterfacesError(f"ABI event #{i} 'inputs' must be a list")
        # 'error' is allowed to have name and optional inputs list; no strictness needed here.


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_interface(spec: InterfaceSpec, *, overwrite: bool = False) -> None:
    """
    Add or update an interface in the registry.

    Parameters
    ----------
    spec : InterfaceSpec
        Interface descriptor.
    overwrite : bool
        If False and name already exists, raise InterfacesError.

    Raises
    ------
    InterfacesError
        On invalid name/ABI shape or if overwrite is False and the name exists.
    """
    if not spec.name or not isinstance(spec.name, str):
        raise InterfacesError("register_interface: 'name' must be a non-empty string")

    abi_list = _coerce_abi_to_list(spec.abi)
    _validate_minimal(abi_list)

    if spec.name in _REGISTRY and not overwrite:
        raise InterfacesError(f"register_interface: '{spec.name}' already registered")

    # Store the original spec but ensure abi is the normalized list form for consumers.
    normalized = InterfaceSpec(
        name=spec.name,
        abi=abi_list,
        version=spec.version,
        description=spec.description,
    )
    _REGISTRY[spec.name] = normalized


def register_many(specs: Iterable[InterfaceSpec], *, overwrite: bool = False) -> None:
    """
    Register multiple interfaces; validation happens per-item.
    """
    for s in specs:
        register_interface(s, overwrite=overwrite)


def get_interface(name: str) -> InterfaceSpec:
    """
    Retrieve a registered interface by name.

    Raises
    ------
    InterfacesError
        If the name is not present.
    """
    try:
        return _REGISTRY[name]
    except KeyError as e:
        raise InterfacesError(f"get_interface: '{name}' not found") from e


def get_abi(name: str) -> AbiList:
    """
    Shortcut to fetch just the ABI list for a named interface.
    """
    return cast(AbiList, get_interface(name).abi)


def has_interface(name: str) -> bool:
    """Return True if an interface with *name* exists in the registry."""
    return name in _REGISTRY


def list_interfaces() -> List[str]:
    """
    Return all registered interface names (sorted).
    """
    return sorted(_REGISTRY.keys())


def clear_registry() -> None:
    """
    Remove all registered interfaces (mostly useful for tests).
    """
    _REGISTRY.clear()


# End of module
