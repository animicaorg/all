"""
Animica DA Schemas package.

Provides convenient loaders for the canonical DA schemas shipped with this
package:

- blob.cddl                    : Blob envelope & chunk layout
- nmt.cddl                     : Namespaced Merkle Tree nodes/leaves
- availability_proof.cddl      : Data-availability sampling proof
- retrieval_api.schema.json    : REST/OpenAPI-style schema for DA retrieval

Utilities here avoid heavy deps and work whether the package is installed
from a wheel or run from a checkout.

Example:

    from da.schemas import (
        load_blob_cddl,
        load_nmt_cddl,
        load_availability_proof_cddl,
        load_retrieval_api_schema,
        validate_retrieval_api,      # optional (requires jsonschema)
    )

    cddl_text = load_blob_cddl()
    api_schema = load_retrieval_api_schema()
"""

from __future__ import annotations

import json
import hashlib
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

try:  # Python 3.9+ preferred path
    from importlib import resources as _res
    _FILES = _res.files  # type: ignore[attr-defined]
    _HAVE_FILES = True
except Exception:  # pragma: no cover
    from importlib import resources as _res  # type: ignore[no-redef]
    _FILES = None  # type: ignore[assignment]
    _HAVE_FILES = False

# ------------------------------- registry ------------------------------------

# Logical names -> package-relative filenames
_SCHEMA_FILES = {
    "blob": "blob.cddl",
    "nmt": "nmt.cddl",
    "availability_proof": "availability_proof.cddl",
    "retrieval_api": "retrieval_api.schema.json",
}


# ----------------------------- resource access --------------------------------

def _read_text(filename: str) -> str:
    pkg = __package__
    if _HAVE_FILES:
        return _FILES(pkg).joinpath(filename).read_text(encoding="utf-8")  # type: ignore[misc]
    # Fallback for older Python/importlib behavior
    return _res.read_text(pkg, filename, encoding="utf-8")


def _read_bytes(filename: str) -> bytes:
    pkg = __package__
    if _HAVE_FILES:
        return _FILES(pkg).joinpath(filename).read_bytes()  # type: ignore[misc]
    return _res.read_binary(pkg, filename)


@contextmanager
def as_path(filename: str):
    """
    Context manager yielding a real filesystem path to an embedded resource.

    Example:
        with as_path("blob.cddl") as p:
            print(p.read_text())
    """
    pkg = __package__
    if not _HAVE_FILES:
        # Older API: extract to temp file under the hood
        with _res.path(pkg, filename) as p:
            yield p
        return
    ref = _FILES(pkg).joinpath(filename)  # type: ignore[misc]
    # On some importlib backends this might still be an installed file; ensure path
    with _res.as_file(ref) as p:
        yield p


def _sha3_256(b: bytes) -> str:
    return "0x" + hashlib.sha3_256(b).hexdigest()


# ------------------------------- public API ----------------------------------

def load_blob_cddl() -> str:
    """Return the text of `blob.cddl`."""
    return _read_text(_SCHEMA_FILES["blob"])


def load_nmt_cddl() -> str:
    """Return the text of `nmt.cddl`."""
    return _read_text(_SCHEMA_FILES["nmt"])


def load_availability_proof_cddl() -> str:
    """Return the text of `availability_proof.cddl`."""
    return _read_text(_SCHEMA_FILES["availability_proof"])


def load_retrieval_api_schema() -> Dict[str, Any]:
    """Return the parsed JSON object from `retrieval_api.schema.json`."""
    return json.loads(_read_text(_SCHEMA_FILES["retrieval_api"]))


def schema_checksum(logical: str) -> str:
    """
    Return a SHA3-256 checksum (hex with 0x prefix) of a schema resource.

    Args:
        logical: one of {"blob","nmt","availability_proof","retrieval_api"}.
    """
    if logical not in _SCHEMA_FILES:
        raise KeyError(f"unknown schema logical name: {logical}")
    return _sha3_256(_read_bytes(_SCHEMA_FILES[logical]))


def validate_retrieval_api(instance: Dict[str, Any]) -> Optional[Iterator[str]]:
    """
    Validate an object against `retrieval_api.schema.json`.

    Returns:
        None on success. If `jsonschema` is missing, raises ImportError.
        If you want an iterator of human messages, iterate over the returned
        errors from `iter_retrieval_api_errors()`.

    Raises:
        jsonschema.ValidationError on failure (if jsonschema is installed).
        ImportError if jsonschema is not available.
    """
    try:
        import jsonschema  # type: ignore
    except Exception as e:  # pragma: no cover - optional dependency
        raise ImportError("jsonschema is required for validation") from e

    schema = load_retrieval_api_schema()
    jsonschema.validate(instance=instance, schema=schema)
    return None


def iter_retrieval_api_errors(instance: Dict[str, Any]):
    """
    Yield validation error messages for `retrieval_api.schema.json`.

    Requires `jsonschema`. If the dependency is missing, raises ImportError.
    """
    try:
        import jsonschema  # type: ignore
    except Exception as e:  # pragma: no cover - optional dependency
        raise ImportError("jsonschema is required for validation") from e

    schema = load_retrieval_api_schema()
    validator = jsonschema.Draft202012Validator(schema)  # type: ignore[attr-defined]
    for err in validator.iter_errors(instance):
        yield err.message


__all__ = [
    "load_blob_cddl",
    "load_nmt_cddl",
    "load_availability_proof_cddl",
    "load_retrieval_api_schema",
    "schema_checksum",
    "as_path",
    "validate_retrieval_api",
    "iter_retrieval_api_errors",
]
