"""
capabilities.adapters.da
========================

Bridge utilities between the capabilities subsystem and the DA (Data Availability)
module. Provides a tiny, dependency-light facade for:

- pin_blob(ns, data, mime): store a blob and return its commitment/receipt
- get_blob(commitment): retrieve a blob by commitment

This adapter prefers, in order:
1) An explicitly supplied in-process store object (duck-typed).
2) A DA retrieval HTTP client (endpoint via arg or env `DA_ENDPOINT`).
3) A minimal local compute-only fallback that *only* returns the commitment
   (not persisted) so upstream callers can still proceed in dry-run/dev modes.

It uses dynamic imports so that importing `capabilities.*` does not hard-require
`da.*` to be installed in all environments.
"""

from __future__ import annotations

import os
import binascii
from dataclasses import asdict
from typing import Any, Dict, Optional, Tuple, Union
from importlib import import_module

HexLike = Union[str, bytes, bytearray, memoryview]

# ----------------------------
# Helpers
# ----------------------------

def _ensure_0x(h: str) -> str:
    return h if h.startswith("0x") else "0x" + h

def _to_hex(b: bytes) -> str:
    return "0x" + b.hex()

def _from_hex(x: HexLike) -> bytes:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    s = str(x).lower().strip()
    if s.startswith("0x"):
        s = s[2:]
    try:
        return binascii.unhexlify(s)
    except binascii.Error as e:
        raise ValueError(f"Invalid hex: {x}") from e

def _try_import(path: str) -> Any:
    try:
        return import_module(path)
    except Exception:
        return None

# ----------------------------
# Optional DA modules
# ----------------------------

_da_commitment = _try_import("da.blob.commitment")
_da_types = _try_import("da.blob.types")
_da_store_mod = _try_import("da.blob.store")
_da_client_mod = _try_import("da.retrieval.client")

# Identify a client class if available (best-effort)
_DA_CLIENT_CANDIDATES = ("DAClient", "Client", "RetrievalClient")
_DAClient = None
if _da_client_mod:
    for name in _DA_CLIENT_CANDIDATES:
        _DAClient = getattr(_da_client_mod, name, None)
        if _DAClient:
            break

# ----------------------------
# Local store duck-typing
# ----------------------------

def _store_put_bytes(store: Any, ns: int, data: bytes, mime: Optional[str]) -> Tuple[bytes, Dict[str, Any]]:
    """
    Try common method names on store to persist bytes, returning (commitment_bytes, receipt_dict).
    """
    # Common patterns we support (in this order):
    # - store.put_bytes(ns=int, data=bytes, mime=str|None) -> (commitment_bytes, receipt_obj|dict)
    # - store.put(data=bytes, namespace=int, mime=str|None) -> (commitment_bytes, receipt_obj|dict)
    # - store.put_blob(ns, data, mime) -> (commitment_bytes, receipt_obj|dict)
    candidates = [
        ("put_bytes", {"ns": ns, "data": data, "mime": mime}),
        ("put", {"data": data, "namespace": ns, "mime": mime}),
        ("put_blob", {"ns": ns, "data": data, "mime": mime}),
    ]
    for meth, kwargs in candidates:
        fn = getattr(store, meth, None)
        if callable(fn):
            commitment, receipt = fn(**kwargs)  # type: ignore[misc]
            # Normalize receipt to dict if it's a dataclass
            if hasattr(receipt, "__dataclass_fields__"):
                receipt = asdict(receipt)
            return commitment, dict(receipt)
    raise AttributeError(
        "Store object does not expose a supported API (tried put_bytes/put/put_blob)."
    )

def _store_get_bytes(store: Any, commitment: bytes) -> bytes:
    """
    Try common method names on store to read bytes by commitment.
    """
    candidates = [
        ("get_bytes", {"commitment": commitment}),
        ("read", {"commitment": commitment}),
        ("get_blob", {"commitment": commitment}),
        ("open", {"commitment": commitment}),  # if returns file-like, read it
    ]
    for meth, kwargs in candidates:
        fn = getattr(store, meth, None)
        if callable(fn):
            out = fn(**kwargs)  # type: ignore[misc]
            if hasattr(out, "read"):
                return out.read()
            return out
    raise AttributeError(
        "Store object does not expose a supported read API (tried get_bytes/read/get_blob/open)."
    )

# ----------------------------
# Retrieval client wrapper
# ----------------------------

class _HttpDA:
    """
    Minimal shim over the DA retrieval client to keep this adapter decoupled.
    """

    def __init__(self, endpoint: str):
        if not _DAClient:
            raise ImportError(
                "da.retrieval.client not available (could not find a DA client class)."
            )
        self._client = _DAClient(endpoint)  # type: ignore[call-arg]

    def post_blob(self, ns: int, data: bytes, mime: Optional[str]) -> Dict[str, Any]:
        """
        Expect the client to expose one of:
          - post_blob(ns, data, mime) -> dict
          - put_blob(ns, data, mime) -> dict
          - post(ns=, data=, mime=) -> dict
        Receipt must include a 'commitment' hex/string or bytes.
        """
        for name in ("post_blob", "put_blob", "post"):
            fn = getattr(self._client, name, None)
            if callable(fn):
                rec = fn(ns=ns, data=data, mime=mime)  # type: ignore[misc]
                if hasattr(rec, "__dataclass_fields__"):
                    rec = asdict(rec)
                return dict(rec)
        raise AttributeError("DA client missing post_blob/put_blob/post method")

    def get_blob(self, commitment: bytes) -> bytes:
        for name in ("get_blob", "fetch_blob", "get"):
            fn = getattr(self._client, name, None)
            if callable(fn):
                out = fn(commitment=commitment)  # type: ignore[misc]
                if hasattr(out, "read"):
                    return out.read()
                return out
        raise AttributeError("DA client missing get_blob/fetch_blob/get method")

# ----------------------------
# Public API
# ----------------------------

def pin_blob(
    ns: int,
    data: bytes,
    mime: Optional[str] = None,
    *,
    store: Optional[Any] = None,
    endpoint: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Pin a blob to DA and return a normalized result:

    {
      "namespace": <int>,
      "size": <int>,
      "commitment": "0x…",
      "receipt": {...}    # may be empty if only computed
    }

    Strategy:
      - If `store` is provided, write through it.
      - Else if endpoint/DA_ENDPOINT set, POST via HTTP client.
      - Else compute commitment only (no persistence).
    """
    if not isinstance(ns, int) or ns < 0 or ns > 2**32 - 1:
        raise ValueError("namespace must be a 32-bit unsigned integer")

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes-like")
    data = bytes(data)

    # 1) In-process store
    if store is not None:
        commitment_b, receipt = _store_put_bytes(store, ns, data, mime)
        return {
            "namespace": ns,
            "size": len(data),
            "commitment": _to_hex(commitment_b),
            "receipt": receipt or {},
            "persistence": "store",
        }

    # 2) HTTP client
    endpoint = endpoint or os.getenv("DA_ENDPOINT")
    if endpoint:
        client = _HttpDA(endpoint)
        receipt = client.post_blob(ns, data, mime)
        # Normalize commitment to hex
        com = receipt.get("commitment")
        if isinstance(com, (bytes, bytearray, memoryview)):
            com_hex = _to_hex(bytes(com))
        else:
            com_hex = _ensure_0x(str(com))
        return {
            "namespace": ns,
            "size": len(data),
            "commitment": com_hex,
            "receipt": receipt,
            "persistence": "remote",
        }

    # 3) Compute-only fallback (not persisted)
    if not _da_commitment:
        raise RuntimeError(
            "DA commitment utils are unavailable and no store/endpoint provided."
        )
    root, meta = _commitment_only(ns, data)
    return {
        "namespace": ns,
        "size": len(data),
        "commitment": _to_hex(root),
        "receipt": {"meta": meta, "warning": "not persisted"},
        "persistence": "none",
    }

def get_blob(
    commitment: HexLike,
    *,
    store: Optional[Any] = None,
    endpoint: Optional[str] = None,
) -> bytes:
    """
    Retrieve a blob by commitment. Tries store → HTTP client. Raises on failure.
    """
    commit_b = _from_hex(commitment)

    # 1) In-process store
    if store is not None:
        return _store_get_bytes(store, commit_b)

    # 2) HTTP client
    endpoint = endpoint or os.getenv("DA_ENDPOINT")
    if endpoint:
        client = _HttpDA(endpoint)
        return client.get_blob(commit_b)

    raise RuntimeError("No DA store or endpoint configured for get_blob()")

# ----------------------------
# Internal: compute-only path
# ----------------------------

def _commitment_only(ns: int, data: bytes) -> Tuple[bytes, Dict[str, Any]]:
    """
    Compute the commitment root using da.blob.commitment but do not persist.
    Returns (root_bytes, meta_dict)
    """
    if not _da_commitment:
        raise ImportError("da.blob.commitment module not available")
    # Expected: commit(data, ns) -> (root_bytes, meta_dict|BlobMeta)
    commit_fn = getattr(_da_commitment, "commit", None)
    if not callable(commit_fn):
        raise AttributeError("da.blob.commitment.commit not found")
    root, meta = commit_fn(data=data, namespace=ns) if "data" in commit_fn.__code__.co_varnames else commit_fn(data, ns)  # type: ignore[misc]
    if hasattr(meta, "__dataclass_fields__"):
        meta = asdict(meta)
    return root, dict(meta)

__all__ = ["pin_blob", "get_blob"]
