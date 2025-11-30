from __future__ import annotations

try:
    import cbor2  # type: ignore
    _HAS_CBOR2 = True
except Exception:
    cbor2 = None  # type: ignore
    _HAS_CBOR2 = False

import json
from typing import Any


def canonical_bytes(obj: Any) -> bytes:
    """Return deterministic canonical bytes for signing.

    Prefer canonical CBOR if cbor2 is available with canonical option; otherwise fall back
    to stable JSON ordering.
    """
    if _HAS_CBOR2:
        try:
            # cbor2 dumps has canonical option in newer versions
            return cbor2.dumps(obj, canonical=True)  # type: ignore[arg-type]
        except TypeError:
            # Older cbor2 may not accept canonical kwarg; emulate by sorting maps
            return cbor2.dumps(_sort_obj(obj))
    # fallback
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sort_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _sort_obj(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, list):
        return [_sort_obj(e) for e in obj]
    return obj
