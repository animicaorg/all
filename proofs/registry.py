"""
Animica | proofs.registry

- A tiny registry that maps ProofType → verifier callable.
- Lazy autoload of built-in verifiers (hashshare, ai, quantum, storage, vdf).
- Stable checksum mapping from proof type → schema root (hash of schema files)
  so headers can bind to *which exact schema set* was in force.

Verifiers
---------
A verifier is a callable:

    def verify(env: ProofEnvelope, *, context: dict | None = None) -> ProofMetrics: ...

It must raise ProofError (or a subtype) on failure. On success it returns
ProofMetrics (consumed by consensus/policy mapping later).

Schema roots
------------
Each proof type has one or more schema files under proofs/schemas/. We expose:

- get_schema_root(pt) -> bytes                 # raw 32-byte SHA3-256 root
- get_schema_hex_map() -> dict[int, str]       # {type_id: hex}
- get_schema_detail() -> dict[int, {...}]      # human/debug view with per-file hashes

All hashing uses SHA3-256 over the raw file bytes; multi-file roots are computed
as sha3_256( b"type_id=<n>|" + b"|".join( sha3_256(file_i) ) ).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Any, Protocol, Iterable

from .errors import ProofError
from .types import ProofType, ProofEnvelope
from . import version as proofs_version
from .utils.hash import sha3_256
from .metrics import ProofMetrics

# ------------------------------------------------------------------------------
# Verifier protocol and registry
# ------------------------------------------------------------------------------

class Verifier(Protocol):
    def __call__(self, env: ProofEnvelope, *, context: Optional[dict] = None) -> ProofMetrics: ...

_VERIFIERS: Dict[ProofType, Verifier] = {}
_LAZY_MODULES: Dict[ProofType, str] = {
    ProofType.HASH_SHARE: "proofs.hashshare",
    ProofType.AI:         "proofs.ai",
    ProofType.QUANTUM:    "proofs.quantum",
    ProofType.STORAGE:    "proofs.storage",
    ProofType.VDF:        "proofs.vdf",
}

def register(pt: ProofType, verifier: Verifier) -> None:
    """
    Register or replace a verifier for pt. Idempotent (same object) allowed.
    """
    _VERIFIERS[pt] = verifier

def is_registered(pt: ProofType) -> bool:
    return pt in _VERIFIERS

def _lazy_load(pt: ProofType) -> None:
    """
    Attempt to import and auto-register the built-in verifier for pt, if available.
    The imported module must expose a top-level `verify` callable with the Verifier signature.
    """
    mod_name = _LAZY_MODULES.get(pt)
    if not mod_name:
        return
    # Local import to avoid import-time cycles while the rest of proofs/ is being created.
    import importlib
    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:  # pragma: no cover (import errors are surfaced on use)
        raise ProofError(f"failed to import verifier module {mod_name!r} for type {int(pt)}: {e}") from e
    if not hasattr(mod, "verify"):
        raise ProofError(f"module {mod_name!r} has no `verify(env, *, context=...)`")
    register(pt, getattr(mod, "verify"))

def get_verifier(pt: ProofType) -> Verifier:
    """
    Return the verifier for pt, loading built-ins lazily if needed.
    """
    if pt not in _VERIFIERS:
        _lazy_load(pt)
    try:
        return _VERIFIERS[pt]
    except KeyError:
        raise ProofError(f"no verifier registered for proof type {int(pt)}")

def verify(env: ProofEnvelope, *, context: Optional[dict] = None) -> ProofMetrics:
    """
    Dispatch to the registered verifier for env.type_id and return ProofMetrics.
    """
    vrf = get_verifier(env.type_id)
    return vrf(env, context=context or {}).ensure_bounds()

# ------------------------------------------------------------------------------
# Schema hashing (type → root)
# ------------------------------------------------------------------------------

# Relative to this file
_THIS_DIR = Path(__file__).resolve().parent
_SCHEMAS_DIR = _THIS_DIR / "schemas"

@dataclass(frozen=True)
class SchemaEntry:
    """File and its individual digest (sha3-256 over bytes)."""
    path: Path
    digest: bytes

# Per-type schema file lists. Keep in sync with proofs/schemas/* from the spec.
# JSON schemas are hashed as raw bytes (no canonicalization beyond file bytes).
_SCHEMA_FILES: Dict[ProofType, tuple[str, ...]] = {
    ProofType.HASH_SHARE: ("hashshare.cddl",),
    ProofType.AI: ("ai_attestation.schema.json",),
    ProofType.QUANTUM: ("quantum_attestation.schema.json",),
    ProofType.STORAGE: ("storage.cddl",),
    ProofType.VDF: ("vdf.cddl",),
}

# The generic envelope schema is also useful to expose (not keyed by ProofType).
_ENVELOPE_SCHEMA = "proof_envelope.cddl"

# Lazy caches
_SCHEMA_ENTRIES_CACHE: Dict[ProofType, tuple[SchemaEntry, ...]] = {}
_SCHEMA_ROOT_CACHE: Dict[ProofType, bytes] = {}
_ENVELOPE_DIGEST: Optional[bytes] = None

def _read_bytes(p: Path) -> bytes:
    with p.open("rb") as f:
        return f.read()

def _entries_for(pt: ProofType) -> tuple[SchemaEntry, ...]:
    """
    Load and hash all schema files for pt (cached). Raises ProofError if a file is missing.
    """
    if pt in _SCHEMA_ENTRIES_CACHE:
        return _SCHEMA_ENTRIES_CACHE[pt]
    file_names = _SCHEMA_FILES.get(pt)
    if not file_names:
        raise ProofError(f"no schema file list for proof type {int(pt)}")
    entries: list[SchemaEntry] = []
    for name in file_names:
        fp = _SCHEMAS_DIR / name
        if not fp.exists():
            raise ProofError(f"schema file not found for type {int(pt)}: {fp}")
        data = _read_bytes(fp)
        entries.append(SchemaEntry(path=fp, digest=sha3_256(data)))
    _SCHEMA_ENTRIES_CACHE[pt] = tuple(entries)
    return _SCHEMA_ENTRIES_CACHE[pt]

def _envelope_digest() -> bytes:
    global _ENVELOPE_DIGEST
    if _ENVELOPE_DIGEST is None:
        fp = _SCHEMAS_DIR / _ENVELOPE_SCHEMA
        if not fp.exists():
            raise ProofError(f"envelope schema file not found: {fp}")
        _ENVELOPE_DIGEST = sha3_256(_read_bytes(fp))
    return _ENVELOPE_DIGEST

def get_schema_root(pt: ProofType) -> bytes:
    """
    Compute a stable root digest for all schemas covering the given proof type.

    root = sha3_256( b"env=" + envelope_digest
                     + b"|type_id=" + str(int(pt)).encode()
                     + b"|" + b"|".join(child_digests_in_declared_order) )

    This binds the generic envelope schema and the type-specific files together.
    """
    if pt in _SCHEMA_ROOT_CACHE:
        return _SCHEMA_ROOT_CACHE[pt]
    env = _envelope_digest()
    children = _entries_for(pt)
    buf = bytearray()
    buf.extend(b"env=")
    buf.extend(env)
    buf.extend(b"|type_id=")
    buf.extend(str(int(pt)).encode())
    for e in children:
        buf.extend(b"|")
        buf.extend(e.digest)
    root = sha3_256(bytes(buf))
    _SCHEMA_ROOT_CACHE[pt] = root
    return root

def get_schema_hex_map() -> Dict[int, str]:
    """
    { type_id(int) : root_hex }
    """
    out: Dict[int, str] = {}
    for pt in _SCHEMA_FILES.keys():
        out[int(pt)] = get_schema_root(pt).hex()
    return out

def get_schema_detail() -> Dict[int, Dict[str, Any]]:
    """
    Human-friendly mapping including per-file digests. Example:

    {
      0: {
        "root": "ab12..",
        "envelope": "cd34..",
        "files": [
          {"name": "hashshare.cddl", "digest": "ef56.."}
        ],
        "module_version": "0.1.0+gitabcdef"
      },
      ...
    }
    """
    env_hex = _envelope_digest().hex()
    detail: Dict[int, Dict[str, Any]] = {}
    for pt, names in _SCHEMA_FILES.items():
        entries = _entries_for(pt)
        detail[int(pt)] = {
            "root": get_schema_root(pt).hex(),
            "envelope": env_hex,
            "files": [{"name": e.path.name, "digest": e.digest.hex()} for e in entries],
            "module_version": proofs_version.__version__,
        }
    return detail

# ------------------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------------------

def list_registered_types() -> Iterable[ProofType]:
    return tuple(_VERIFIERS.keys())

def list_known_types() -> Iterable[ProofType]:
    return tuple(_SCHEMA_FILES.keys())

# ------------------------------------------------------------------------------
# Optional: eager registration helper (safe to call after all modules exist)
# ------------------------------------------------------------------------------

def register_builtins() -> None:
    """
    Eagerly import and register all built-in verifiers. It is safe to call multiple times.
    """
    for pt in list_known_types():
        if not is_registered(pt):
            _lazy_load(pt)

__all__ = [
    "Verifier",
    "register",
    "is_registered",
    "get_verifier",
    "verify",
    "get_schema_root",
    "get_schema_hex_map",
    "get_schema_detail",
    "list_registered_types",
    "list_known_types",
    "register_builtins",
]
