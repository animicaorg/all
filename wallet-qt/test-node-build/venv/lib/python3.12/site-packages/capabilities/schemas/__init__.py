"""
capabilities.schemas
--------------------

Package data loader for the capabilities subsystem schemas.

Contents (shipped alongside this module):
- JSON Schema:
    * syscalls_abi.schema.json
    * zk_verify.schema.json
- CDDL (CBOR) specs:
    * job_request.cddl
    * job_receipt.cddl
    * result_record.cddl

This module provides small helpers to load those resources at runtime with
`importlib.resources`, without assuming any particular working directory.
It degrades gracefully on older Python versions.
"""

from __future__ import annotations

import json
from typing import Dict, Iterable, List

# --- Manifest ----------------------------------------------------------------------

_JSON_SCHEMAS: Dict[str, str] = {
    "syscalls_abi": "syscalls_abi.schema.json",
    "zk_verify": "zk_verify.schema.json",
}

_CDDL_SPECS: Dict[str, str] = {
    "job_request": "job_request.cddl",
    "job_receipt": "job_receipt.cddl",
    "result_record": "result_record.cddl",
}

# --- importlib.resources compatibility layer ---------------------------------------

try:  # Python ≥ 3.9
    from importlib.resources import \
        files as _files  # type: ignore[attr-defined]

    def _read_bytes(relpath: str) -> bytes:
        return (_files(__name__) / relpath).read_bytes()

    def _read_text(relpath: str) -> str:
        return (_files(__name__) / relpath).read_text(encoding="utf-8")

except Exception:  # pragma: no cover - used only when `files` is unavailable
    # Python 3.8 fallback
    from importlib.resources import open_binary, open_text  # type: ignore

    def _read_bytes(relpath: str) -> bytes:
        with open_binary(__name__, relpath) as fh:
            return fh.read()

    def _read_text(relpath: str) -> str:
        with open_text(__name__, relpath, encoding="utf-8") as fh:
            return fh.read()


# --- Public helpers ----------------------------------------------------------------


def list_json_schemas() -> List[str]:
    """Return the list of available JSON-Schema logical names."""
    return sorted(_JSON_SCHEMAS.keys())


def list_cddl() -> List[str]:
    """Return the list of available CDDL logical names."""
    return sorted(_CDDL_SPECS.keys())


def load_json_schema(name: str) -> dict:
    """
    Load and parse a JSON-Schema by logical name.

    Parameters
    ----------
    name : str
        One of: {names returned by `list_json_schemas()`}.

    Returns
    -------
    dict
        Parsed JSON object.

    Raises
    ------
    KeyError
        If the schema name is unknown.
    FileNotFoundError / JSONDecodeError
        If the packaged resource is missing or invalid.
    """
    try:
        rel = _JSON_SCHEMAS[name]
    except KeyError as e:
        raise KeyError(
            f"Unknown JSON schema {name!r}. Known: {list_json_schemas()}"
        ) from e
    data = _read_bytes(rel)
    return json.loads(data.decode("utf-8"))


def read_cddl(name: str) -> str:
    """
    Read a CDDL specification text by logical name.

    Parameters
    ----------
    name : str
        One of: {names returned by `list_cddl()`}.

    Returns
    -------
    str
        CDDL text.

    Raises
    ------
    KeyError
        If the spec name is unknown.
    FileNotFoundError
        If the packaged resource is missing.
    """
    try:
        rel = _CDDL_SPECS[name]
    except KeyError as e:
        raise KeyError(f"Unknown CDDL spec {name!r}. Known: {list_cddl()}") from e
    return _read_text(rel)


# Optional jsonschema validation sugar
try:  # pragma: no cover - exercised in environments with jsonschema installed
    import jsonschema  # type: ignore

    def validate_json(instance: object, schema_name: str) -> None:
        """Validate an instance against a packaged JSON-Schema by name."""
        schema = load_json_schema(schema_name)
        jsonschema.validate(instance=instance, schema=schema)

except Exception:  # pragma: no cover - if jsonschema is not available

    def validate_json(instance: object, schema_name: str) -> None:
        raise RuntimeError(
            "jsonschema is not installed; install it to use validate_json()."
        )


__all__ = [
    "list_json_schemas",
    "list_cddl",
    "load_json_schema",
    "read_cddl",
    "validate_json",
]
