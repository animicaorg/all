"""
Animica | proofs.utils.schema

Lightweight helpers to *load* and *validate* structured data for proofs:
- JSON Schema (Draft 2020-12 preferred, falls back gracefully)
- CDDL (for CBOR structures) with optional strict validation if a CDDL
  validator is available

Design goals
------------
- Zero hard runtime deps beyond stdlib; optional speed/strictness via extras.
- Deterministic "schema checksum" (SHA3-256 over canonical JSON) suitable for
  mapping proof-type -> schema id in registries.
- Friendly errors that raise `proofs.errors.SchemaError` with context.
- Works out-of-the-box for local dev even if `jsonschema`/`cddl`/`cbor2`
  aren't installed; you can enable strict modes via environment flags.

Environment flags
-----------------
- ANIMICA_STRICT_JSONSCHEMA=1  → require `jsonschema` to be present; otherwise raise
- ANIMICA_STRICT_CDDL=1        → require a CDDL validator; otherwise raise
- ANIMICA_SCHEMA_LOGLEVEL=DEBUG|INFO|... → control logger level (default WARNING)

Notes
-----
For CBOR decode we prefer `core.encoding.cbor.loads` (canonical implementation).
If unavailable, we try `cbor2`. If neither is present we error only when a CBOR
decode is actually requested.

CDDL validation:
- If a Python CDDL validator is available (e.g., `cddl` from PyPI or
  `cbor2tools.cddl`), we use it.
- Otherwise we do a "decode-only" check: ensure the CBOR bytes are well-formed.
  In strict mode this is an error.

"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple, Union, Mapping

# local error type
try:
    from proofs.errors import SchemaError
except Exception:  # pragma: no cover - during early bootstrap
    class SchemaError(Exception):
        pass

# --------------------------------------------------------------------------------------
# Logger
# --------------------------------------------------------------------------------------

_LOG = logging.getLogger("animica.proofs.schema")
_level = getattr(logging, os.getenv("ANIMICA_SCHEMA_LOGLEVEL", "WARNING").upper(), logging.WARNING)
if not _LOG.handlers:
    _LOG.setLevel(_level)
    _h = logging.StreamHandler()
    _h.setLevel(_level)
    _h.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    _LOG.addHandler(_h)

STRICT_JSON = os.getenv("ANIMICA_STRICT_JSONSCHEMA", "0") == "1"
STRICT_CDDL = os.getenv("ANIMICA_STRICT_CDDL", "0") == "1"

# --------------------------------------------------------------------------------------
# Canonical JSON helpers (sorted keys, no whitespace, stable encodings)
# --------------------------------------------------------------------------------------

def _canonical_json_dumps(obj: Any) -> str:
    """
    Deterministic JSON string: UTF-8, sorted keys, minimal whitespace.
    Tries `core.utils.serialization.json_dumps` first; falls back to builtin.
    """
    try:
        from core.utils.serialization import json_dumps as _cj
        return _cj(obj)
    except Exception:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def schema_sha3_256(schema: Mapping[str, Any]) -> bytes:
    """
    Hash a JSON schema deterministically for registry/checksum use.
    Returns raw 32-byte digest; use .hex() for hex string.
    """
    s = _canonical_json_dumps(schema).encode("utf-8")
    return hashlib.sha3_256(s).digest()


# --------------------------------------------------------------------------------------
# JSON Schema loader & validator (optional dependency on `jsonschema`)
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class JsonSchemaHandle:
    schema: Mapping[str, Any]
    checksum: bytes  # SHA3-256 over canonical JSON
    draft: Optional[str] = None  # e.g., "2020-12"


def load_json_schema(src: Union[str, Path, bytes, Mapping[str, Any]]) -> JsonSchemaHandle:
    """
    Load a JSON Schema from a path/bytes/dict and compute its checksum.
    """
    if isinstance(src, (str, Path)):
        p = Path(src)
        try:
            text = p.read_text(encoding="utf-8")
        except Exception as e:  # noqa: PERF203
            raise SchemaError(f"Failed to read JSON schema at {p}: {e}") from e
        try:
            obj = json.loads(text)
        except Exception as e:
            raise SchemaError(f"Failed to parse JSON schema at {p}: {e}") from e
    elif isinstance(src, (bytes, bytearray)):
        try:
            obj = json.loads(bytes(src).decode("utf-8"))
        except Exception as e:
            raise SchemaError(f"Failed to parse JSON schema from bytes: {e}") from e
    elif isinstance(src, Mapping):
        obj = src
    else:
        raise TypeError("load_json_schema: unsupported src type")

    # Detect draft (best-effort)
    draft = None
    if "$schema" in obj and isinstance(obj["$schema"], str):
        draft = obj["$schema"]

    return JsonSchemaHandle(schema=obj, checksum=schema_sha3_256(obj), draft=draft)


def validate_json(instance: Any, handle: JsonSchemaHandle, *, title: str = "instance") -> None:
    """
    Validate `instance` against JSON Schema in `handle`.
    Uses `jsonschema` (if present). Otherwise:
      - in NON-strict mode: no-op (logs a warning once)
      - in STRICT mode: raise SchemaError

    Raises SchemaError on validation failure.
    """
    try:
        jsonschema = importlib.import_module("jsonschema")
    except Exception:
        if STRICT_JSON:
            raise SchemaError("jsonschema package not available and ANIMICA_STRICT_JSONSCHEMA=1")  # noqa: TRY003
        _LOG.debug("jsonschema not available; skipping JSON Schema validation for %s", title)
        return

    try:
        # Prefer latest validator if present; fallback to Draft7
        DraftValidator = None
        for name in ("Draft202012Validator", "Draft201909Validator", "Draft7Validator"):
            DraftValidator = getattr(jsonschema, name, None) or DraftValidator
        if DraftValidator is None:  # pragma: no cover
            raise SchemaError("jsonschema installed, but no known Draft*Validator available")

        DraftValidator(handle.schema).validate(instance)
    except Exception as e:
        raise SchemaError(f"JSON Schema validation failed for {title}: {e}") from e


# --------------------------------------------------------------------------------------
# CBOR & CDDL helpers
# --------------------------------------------------------------------------------------

def _cbor_decode(data: bytes) -> Any:
    """
    Decode CBOR bytes using preferred implementations:
      1) core.encoding.cbor.loads (repo canonical)
      2) cbor2.loads (pip)
    """
    # Try repo canonical
    try:
        from core.encoding import cbor as _c
        return _c.loads(data)  # type: ignore[attr-defined]
    except Exception:
        pass
    # Try cbor2
    try:
        cbor2 = importlib.import_module("cbor2")
        return cbor2.loads(data)
    except Exception as e:
        raise SchemaError(
            "No CBOR decoder available; install `cbor2` or ensure core.encoding.cbor is importable"
        ) from e


@dataclass(frozen=True)
class CDDLHandle:
    """
    Lightweight wrapper for a CDDL spec.
    `compiled` may be an object from a third-party library or None.
    """
    text: str
    compiled: Optional[Any] = None
    source: Optional[Path] = None


def load_cddl(src: Union[str, Path]) -> CDDLHandle:
    """
    Load CDDL text and (if possible) compile it using an available backend.
    Supported backends (first one found is used):
      - `cddl` (https://pypi.org/project/cddl/)
      - `cbor2tools.cddl` (provides a CDDL parser/validator)
    """
    path = Path(src)
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        raise SchemaError(f"Failed to read CDDL at {path}: {e}") from e

    compiled = None
    backend = None

    # Try known backends
    for mod_name in ("cddl", "cbor2tools.cddl"):
        try:
            backend = importlib.import_module(mod_name)
            break
        except Exception:
            continue

    if backend is not None:
        # Best-effort compile; different backends expose different APIs.
        try:
            if hasattr(backend, "CDDL"):
                # cddl: CDDL(text).validate(obj)
                compiled = backend.CDDL(text)
            elif hasattr(backend, "parse_cddl"):
                # cbor2tools.cddl: returns a parser/ast; user calls validator separately
                compiled = backend.parse_cddl(text)
            else:  # pragma: no cover
                _LOG.warning("CDDL backend %s found but no known compile API; continuing decode-only", backend)
        except Exception as e:
            if STRICT_CDDL:
                raise SchemaError(f"Failed to compile CDDL with backend {backend}: {e}") from e
            _LOG.warning("CDDL compile failed (%s); will perform decode-only checks", e)

    elif STRICT_CDDL:
        raise SchemaError("No CDDL backend available and ANIMICA_STRICT_CDDL=1")

    return CDDLHandle(text=text, compiled=compiled, source=path)


def validate_cddl_cbor(cddl: CDDLHandle, cbor_bytes: bytes, *, title: str = "CBOR value") -> Any:
    """
    Validate `cbor_bytes` against the provided `cddl` handle.
    Returns the decoded CBOR Python object on success (so callers can reuse it).

    Behavior:
      - Always decode CBOR (raises SchemaError if invalid CBOR)
      - If a compiled CDDL backend is available, validate structure
      - If no backend, log and return decoded object (unless STRICT_CDDL)

    Raises SchemaError on failure.
    """
    obj = _cbor_decode(cbor_bytes)

    if cddl.compiled is None:
        if STRICT_CDDL:
            raise SchemaError("CDDL strict mode enabled but no backend compiled")
        _LOG.debug("No CDDL backend; decoded %s without structural validation", title)
        return obj

    # Backend-specific validation
    try:
        backend_mod = getattr(cddl.compiled, "__module__", "")  # type: ignore[attr-defined]

        if "cddl" in backend_mod and hasattr(cddl.compiled, "validate"):
            # `cddl` backend
            cddl.compiled.validate(obj)  # type: ignore[attr-defined]
            return obj

        if "cbor2tools" in backend_mod:
            # `cbor2tools.cddl` exposes a `validator` factory in some versions
            validator = None
            try:
                cbor2_cddl = importlib.import_module("cbor2tools.cddl")
                validator = getattr(cbor2_cddl, "CDDLValidator", None)
            except Exception:
                validator = None

            if validator is None:
                # No validator class; best we can do is return decoded obj
                _LOG.debug("Parsed CDDL AST but no validator API; returning decoded object")
                return obj

            v = validator(cddl.text)  # type: ignore[call-arg]
            ok, err = v.validate(obj)  # type: ignore[attr-defined]
            if not ok:
                raise SchemaError(f"CDDL validation failed for {title}: {err}")
            return obj

        # Unknown compiled type; assume success if no exception path
        _LOG.debug("Unknown CDDL backend (%s); returning decoded object", backend_mod)
        return obj

    except SchemaError:
        raise
    except Exception as e:
        raise SchemaError(f"CDDL validation error for {title}: {e}") from e


# --------------------------------------------------------------------------------------
# Convenience: validate JSON by schema path and CBOR by CDDL path
# --------------------------------------------------------------------------------------

def validate_json_by_path(instance: Any, schema_path: Union[str, Path], *, title: str = "instance") -> JsonSchemaHandle:
    h = load_json_schema(schema_path)
    validate_json(instance, h, title=title)
    return h


def validate_cbor_by_cddl_path(cbor_bytes: bytes, cddl_path: Union[str, Path], *, title: str = "CBOR value") -> CDDLHandle:
    h = load_cddl(cddl_path)
    validate_cddl_cbor(h, cbor_bytes, title=title)
    return h


# --------------------------------------------------------------------------------------
# Self-test (dev aid)
# --------------------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    # JSON sanity
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["a", "b"],
        "properties": {"a": {"type": "integer"}, "b": {"type": "string"}},
        "additionalProperties": False,
    }
    h = load_json_schema(schema)
    print("JSON schema checksum:", h.checksum.hex())
    try:
        validate_json({"a": 1, "b": "ok"}, h, title="demo-json")
        print("JSON validation OK")
    except SchemaError as e:
        print("JSON validation error:", e)

    # CBOR + CDDL smoke (decode-only if no backend)
    try:
        # Minimal CBOR map: {1: 2}
        demo_cbor = bytes.fromhex("A1 01 02")
        # Tiny CDDL that allows a map with integer keys/values (informal; backend-dependent)
        cddl_text = "demo = { int => int }"
        tmp = Path(".cddl.tmp")
        tmp.write_text(cddl_text, encoding="utf-8")
        ch = load_cddl(tmp)
        obj = validate_cddl_cbor(ch, demo_cbor, title="demo-cbor")
        print("CBOR decoded/validated:", obj)
        try:
            tmp.unlink()
        except Exception:
            pass
    except SchemaError as e:
        print("CDDL/CBOR error:", e)
