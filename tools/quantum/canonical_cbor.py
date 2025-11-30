{"content":"from __future__ import annotations\n\ntry:\n    import cbor2  # type: ignore\n    _HAS_CBOR2 = True\nexcept Exception:\n    cbor2 = None  # type: ignore\n    _HAS_CBOR2 = False\n\nimport json\nfrom typing import Any\n\ndef canonical_bytes(obj: Any) -> bytes:\n    \""\"Return deterministic canonical bytes for signing.\n\n    Prefer canonical CBOR if cbor2 is available with canonical option; otherwise fall back\n    to stable JSON ordering.\n    \""\"\n    if _HAS_CBOR2:\n        try:\n            # cbor2 dumps has canonical option in newer versions
            return cbor2.dumps(obj, canonical=True)  # type: ignore[arg-type]
        except TypeError:
            # Older cbor2 may not accept canonical kwarg; emulate by sorting maps
            return cbor2.dumps(_sort_obj(obj))
    # fallback
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')\n\ndef _sort_obj(obj: Any) -> Any:\n    if isinstance(obj, dict):\n        return {k: _sort_obj(obj[k]) for k in sorted(obj.keys())}\n    if isinstance(obj, list):\n        return [_sort_obj(e) for e in obj]\n    return obj\n"}{