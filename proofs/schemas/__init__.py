"""
Animica • proofs.schemas
------------------------
Package marker + tiny loader helpers for local schema assets used by the
`proofs` module. This package ships CDDL and JSON-Schema files that describe
proof envelopes and per-proof bodies.

Use the helpers here if you need to read a schema’s bytes/text or list what’s
available. Validation logic lives in `proofs.utils.schema`, not here.

Shipped files (see repo listing):
  - proof_envelope.cddl
  - hashshare.cddl
  - ai_attestation.schema.json
  - quantum_attestation.schema.json
  - storage.cddl
  - vdf.cddl
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List
import hashlib
import json
import os

# importlib.resources works when package is zipped or installed as a wheel.
try:
    from importlib.resources import files as _res_files, as_file as _as_file
except Exception:  # pragma: no cover
    # Fallback to older API if needed (Py<3.9 environments).
    import importlib.resources as _ires  # type: ignore

    def _res_files(pkg: str):
        return _ires.files(pkg)  # type: ignore[attr-defined]

    def _as_file(resource):
        return _ires.as_file(resource)  # type: ignore[attr-defined]


# Canonical filenames we expect to ship in this package.
_CDDL_FILES: List[str] = [
    "proof_envelope.cddl",
    "hashshare.cddl",
    "storage.cddl",
    "vdf.cddl",
]
_JSON_FILES: List[str] = [
    "ai_attestation.schema.json",
    "quantum_attestation.schema.json",
]

_ALL: List[str] = _CDDL_FILES + _JSON_FILES


@dataclass(frozen=True)
class SchemaInfo:
    name: str
    kind: str  # "cddl" | "json"
    sha256: str
    size: int


def _pkg_root():
    # Returns a Traversable for this package directory.
    return _res_files(__name__)


def list_files(kind: str | None = None) -> List[str]:
    """
    List schema file names. If `kind` is "cddl" or "json", filter accordingly.
    """
    if kind is None:
        return list(_ALL)
    k = kind.lower()
    if k == "cddl":
        return list(_CDDL_FILES)
    if k == "json":
        return list(_JSON_FILES)
    raise ValueError("kind must be one of: None, 'cddl', 'json'")


def exists(name: str) -> bool:
    """Return True if a schema file with this name exists in the package."""
    return name in _ALL


def read_bytes(name: str) -> bytes:
    """
    Read raw bytes of a schema file from the package.
    Raises FileNotFoundError if name is unknown.
    """
    if name not in _ALL:
        raise FileNotFoundError(f"schema not found: {name} (available: {', '.join(_ALL)})")
    res = _pkg_root().joinpath(name)
    with _as_file(res) as fp:
        with open(fp, "rb") as f:
            return f.read()


def read_text(name: str, encoding: str = "utf-8") -> str:
    """Read schema text as UTF-8 (default)."""
    return read_bytes(name).decode(encoding)


def sha256(name: str) -> str:
    """Hex-encoded SHA-256 of the schema bytes."""
    return hashlib.sha256(read_bytes(name)).hexdigest()


def info(name: str) -> SchemaInfo:
    """Return a small descriptor (kind, sha256, size) for a schema file."""
    kind = "json" if name.endswith(".json") else "cddl" if name.endswith(".cddl") else "unknown"
    data = read_bytes(name)
    return SchemaInfo(name=name, kind=kind, sha256=hashlib.sha256(data).hexdigest(), size=len(data))


def info_all(kind: str | None = None) -> List[SchemaInfo]:
    """Return SchemaInfo for all (or filtered) files."""
    return [info(n) for n in list_files(kind)]


def load_json(name: str) -> object:
    """
    Parse and return a JSON object from a JSON-Schema file.
    Convenience wrapper for callers that need the schema object.
    """
    if not name.endswith(".json"):
        raise ValueError("load_json expects a .json schema filename")
    return json.loads(read_text(name))


__all__ = [
    "SchemaInfo",
    "list_files",
    "exists",
    "read_bytes",
    "read_text",
    "sha256",
    "info",
    "info_all",
    "load_json",
]
