"""
Artifacts service: put/get metadata, serve blobs, and link artifacts to contract addresses.

This service is intentionally defensive against missing/variant adapter/storage APIs across
environments. It uses capability detection and falls back to safe defaults where possible.

Primary flows
-------------
- put(meta: ArtifactPut, data: bytes) -> ArtifactMeta
    * Validates inputs (address format, size caps)
    * Computes digest & (if applicable) code hash
    * Derives a deterministic artifact id
    * Stores blob into write-once store (FS or S3 adapter)
    * Persists metadata to SQLite store
    * Optionally posts blob to DA and records commitment

- get(artifact_id: str) -> (ArtifactMeta, bytes)
- get_meta(artifact_id: str) -> ArtifactMeta
- list_by_address(address: str) -> list[ArtifactMeta]
- link_to_address(address: str, artifact_id: str) -> ArtifactMeta

Dependencies (expected modules)
-------------------------------
- studio_services.models.artifacts: ArtifactPut, ArtifactMeta
- studio_services.storage.sqlite: metadata persistence helpers
- studio_services.storage.fs: write-once blob store (content-addressed)
- studio_services.storage.ids: deterministic id helpers
- studio_services.adapters.vm_hash: digest/code-hash utilities
- studio_services.adapters.da_client: optional DA client (feature-gated)
- studio_services.adapters.pq_addr: address validation
- studio_services.config: size caps and feature toggles

All of the above are accessed via duck-typed adapters with graceful fallback.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Tuple, List

from studio_services.errors import ApiError, BadRequest
from studio_services.models.artifacts import ArtifactPut, ArtifactMeta
from studio_services import config as cfg_mod

# storage/adapters
from studio_services.storage import sqlite as storage_sqlite  # type: ignore
from studio_services.storage import fs as storage_fs  # type: ignore
from studio_services.storage import ids as storage_ids  # type: ignore
from studio_services.adapters import vm_hash as vm_hash_adapter  # type: ignore
from studio_services.adapters import da_client as da_adapter  # type: ignore
from studio_services.adapters import pq_addr as addr_adapter  # type: ignore

log = logging.getLogger(__name__)


# ------------------------ Config helpers ------------------------

def _cfg(name: str, default: Optional[str] = None) -> Optional[str]:
    getter = getattr(cfg_mod, "get", None)
    if callable(getter):
        try:
            v = getter(name)
            if v is not None:
                return str(v)
        except Exception:  # pragma: no cover
            pass
    return os.getenv(name, default)


def _get_limits() -> Dict[str, int]:
    return {
        "ARTIFACT_MAX_BYTES": int(_cfg("ARTIFACT_MAX_BYTES", "4194304") or "4194304"),  # 4 MiB default
    }


def _da_enabled() -> bool:
    return str(_cfg("DA_ENABLED", "0") or "0") in ("1", "true", "yes", "on")


# ------------------------ Hashing helpers ------------------------

def _sha3_256(data: bytes) -> str:
    return hashlib.sha3_256(data).hexdigest()


def _compute_digest_and_code_hash(kind: str, data: bytes) -> Tuple[str, Optional[str]]:
    """
    Returns (digest_hex, code_hash_hex_or_none).

    digest_hex: always SHA3-256 of the raw artifact payload.
    code_hash:  for "code"/"bytecode" kinds, prefer adapter's compute_code_hash; else digest.
    """
    digest = None
    code_hash = None

    # Content digest (always)
    try:
        digest = getattr(vm_hash_adapter, "content_digest", None)
        if callable(digest):
            digest_hex = str(digest(data))
        else:
            digest_hex = _sha3_256(data)
    except Exception:
        digest_hex = _sha3_256(data)

    # Code hash (conditional)
    if kind.lower() in ("code", "bytecode", "program", "ir"):
        try:
            ckh = getattr(vm_hash_adapter, "compute_code_hash", None) or getattr(vm_hash_adapter, "code_hash", None)
            if callable(ckh):
                code_hash = str(ckh(data))
            else:
                code_hash = digest_hex
        except Exception:  # pragma: no cover
            code_hash = digest_hex
    else:
        code_hash = None

    return digest_hex, code_hash


# ------------------------ ID helper ------------------------

def _derive_artifact_id(meta: ArtifactPut, digest_hex: str) -> str:
    """
    Prefer deterministic id from storage.ids; otherwise derive from fields.
    """
    try:
        mk = getattr(storage_ids, "artifact_id", None) or getattr(storage_ids, "compute_artifact_id", None)
        if callable(mk):
            return str(mk({
                "kind": meta.kind,
                "address": (meta.address or "").lower(),
                "media_type": meta.media_type or "",
                "label": meta.label or "",
                "digest": digest_hex,
            }))
    except Exception:  # pragma: no cover
        pass

    # Fallback: SHA3-256 over canonical field concat
    base = f"animica|artifact|{meta.kind}|{(meta.address or '').lower()}|{meta.media_type or ''}|{meta.label or ''}|{digest_hex}"
    return hashlib.sha3_256(base.encode("utf-8")).hexdigest()


# ------------------------ Blob store adapter ------------------------

class _BlobStore:
    def __init__(self) -> None:
        self.fs = storage_fs

    def put(self, artifact_id: str, data: bytes, *, media_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Store blob idempotently. Returns a descriptor with at least:
            {"id": artifact_id, "size": len(data), "path": <optional>, "media_type": media_type}
        """
        size = len(data)
        # Try common function names
        for fname in ("put", "write", "store", "save", "put_blob"):
            fn = getattr(self.fs, fname, None)
            if callable(fn):
                try:
                    desc = fn(artifact_id, data, media_type=media_type)  # type: ignore
                    if isinstance(desc, dict):
                        return {"id": artifact_id, "size": size, "media_type": media_type, **desc}
                except TypeError:
                    # maybe signature without media_type
                    path = fn(artifact_id, data)  # type: ignore
                    return {"id": artifact_id, "size": size, "media_type": media_type, "path": path}
                except Exception as e:  # pragma: no cover
                    raise ApiError(f"artifact blob store failed: {e}")
        # Last resort: try to place under a conventional path
        # (the fs module should expose a "path_for" or "root_dir")
        root = getattr(self.fs, "root_dir", None) or _cfg("STORAGE_DIR", "./storage")
        path = os.path.join(str(root), "artifacts", artifact_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return {"id": artifact_id, "size": size, "media_type": media_type, "path": path}

    def get_bytes(self, artifact_id: str) -> bytes:
        for fname in ("get", "read", "get_bytes", "read_bytes", "load", "get_blob"):
            fn = getattr(self.fs, fname, None)
            if callable(fn):
                try:
                    out = fn(artifact_id)  # type: ignore
                    # Some implementations return (bytes, meta) or a stream; normalize
                    if isinstance(out, (bytes, bytearray)):
                        return bytes(out)
                    if hasattr(out, "read"):
                        return out.read()
                    if isinstance(out, tuple) and isinstance(out[0], (bytes, bytearray)):
                        return bytes(out[0])
                except Exception as e:  # pragma: no cover
                    raise ApiError(f"artifact blob fetch failed: {e}")
        # Try conventional path
        root = getattr(self.fs, "root_dir", None) or _cfg("STORAGE_DIR", "./storage")
        path = os.path.join(str(root), "artifacts", artifact_id)
        if not os.path.exists(path):
            raise BadRequest("artifact not found")
        with open(path, "rb") as f:
            return f.read()


# ------------------------ SQLite metadata store adapter ------------------------

class _MetaStore:
    """
    Small wrapper to work with various storage.sqlite helper shapes.

    Expected table columns (see schema.sql):
      id TEXT PRIMARY KEY,
      kind TEXT,
      address TEXT NULL,
      media_type TEXT NULL,
      label TEXT NULL,
      size INTEGER,
      digest TEXT,
      code_hash TEXT NULL,
      da_commitment TEXT NULL,
      created_at INTEGER
    """

    def __init__(self) -> None:
        self.db = storage_sqlite

    def insert(self, row: Dict[str, Any]) -> None:
        for fname in ("insert_artifact", "upsert_artifact", "put_artifact_meta", "record_artifact"):
            fn = getattr(self.db, fname, None)
            if callable(fn):
                fn(row)
                return
        # Fallback to explicit SQL if module exposes conn/execute
        conn = getattr(self.db, "connect", None)
        if callable(conn):
            import sqlite3
            cx = conn()  # type: ignore
            with cx:
                cx.execute(
                    """
                    INSERT OR REPLACE INTO artifacts
                    (id, kind, address, media_type, label, size, digest, code_hash, da_commitment, created_at)
                    VALUES (:id, :kind, :address, :media_type, :label, :size, :digest, :code_hash, :da_commitment, :created_at)
                    """,
                    row,
                )
            return
        raise ApiError("No usable artifact metadata persistence available")

    def get(self, artifact_id: str) -> Dict[str, Any]:
        for fname in ("get_artifact", "get_artifact_meta", "fetch_artifact", "fetch_artifact_meta"):
            fn = getattr(self.db, fname, None)
            if callable(fn):
                row = fn(artifact_id)
                if row is None:
                    raise BadRequest("artifact not found")
                if isinstance(row, dict):
                    return row
        conn = getattr(self.db, "connect", None)
        if callable(conn):
            cx = conn()  # type: ignore
            cur = cx.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,))
            row = cur.fetchone()
            if not row:
                raise BadRequest("artifact not found")
            # sqlite3.Row -> dict
            if hasattr(row, "keys"):
                return {k: row[k] for k in row.keys()}  # type: ignore
        raise ApiError("No usable artifact metadata reader available")

    def list_by_address(self, address: str) -> List[Dict[str, Any]]:
        for fname in ("list_artifacts_by_address", "fetch_artifacts_by_address", "list_by_address"):
            fn = getattr(self.db, fname, None)
            if callable(fn):
                res = fn(address.lower())
                if isinstance(res, list):
                    return [dict(r) if not isinstance(r, dict) and hasattr(r, "__iter__") else r for r in res]  # type: ignore
        conn = getattr(self.db, "connect", None)
        if callable(conn):
            cx = conn()  # type: ignore
            cur = cx.execute(
                "SELECT * FROM artifacts WHERE lower(address)=? ORDER BY created_at DESC",
                (address.lower(),),
            )
            rows = cur.fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                if hasattr(r, "keys"):
                    out.append({k: r[k] for k in r.keys()})  # type: ignore
            return out
        raise ApiError("No usable artifact metadata listing available")

    def link(self, address: str, artifact_id: str) -> Dict[str, Any]:
        # Prefer helper if present
        for fname in ("link_artifact_address", "set_artifact_address", "update_artifact_address"):
            fn = getattr(self.db, fname, None)
            if callable(fn):
                return fn(address.lower(), artifact_id)
        conn = getattr(self.db, "connect", None)
        if callable(conn):
            cx = conn()  # type: ignore
            with cx:
                cx.execute(
                    "UPDATE artifacts SET address=? WHERE id=?",
                    (address.lower(), artifact_id),
                )
                cur = cx.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
                row = cur.fetchone()
                if not row:
                    raise BadRequest("artifact not found after link")
                return {k: row[k] for k in row.keys()}  # type: ignore
        raise ApiError("No usable artifact metadata updater available")


# ------------------------ DA client adapter ------------------------

class _DAClient:
    def __init__(self) -> None:
        self.client = da_adapter

    def post(self, data: bytes, *, namespace: Optional[int] = None) -> Dict[str, Any]:
        """
        Attempts to post to DA; returns dict with at least {"commitment": "..."}.
        """
        for fname in ("post_blob", "put_blob", "post", "put"):
            fn = getattr(self.client, fname, None)
            if callable(fn):
                return fn(data, namespace=namespace)  # type: ignore
        raise ApiError("DA client not available")


# ------------------------ Public service ------------------------

class ArtifactService:
    def __init__(self) -> None:
        self.blobs = _BlobStore()
        self.meta = _MetaStore()
        self.limits = _get_limits()
        self._da: Optional[_DAClient] = _DAClient() if _da_enabled() else None

    def _validate_address(self, address: Optional[str]) -> None:
        if not address:
            return
        validator = getattr(addr_adapter, "validate", None) or getattr(addr_adapter, "validate_address", None)
        if callable(validator):
            try:
                validator(address)
            except Exception as e:
                raise BadRequest(f"Invalid address: {e}")

    def put(self, meta: ArtifactPut, data: bytes) -> ArtifactMeta:
        """
        Store an artifact and return its metadata. Idempotent by (kind, address, digest).
        """
        if not isinstance(data, (bytes, bytearray)):
            raise BadRequest("artifact payload must be bytes")
        size = len(data)
        max_size = int(self.limits["ARTIFACT_MAX_BYTES"])
        if size <= 0:
            raise BadRequest("artifact payload is empty")
        if size > max_size:
            raise BadRequest(f"artifact exceeds max size ({size} > {max_size} bytes)")

        self._validate_address(meta.address)

        digest_hex, code_hash = _compute_digest_and_code_hash(meta.kind, bytes(data))
        artifact_id = _derive_artifact_id(meta, digest_hex)
        created_at = int(time.time())

        # Store blob (write-once)
        blob_desc = self.blobs.put(artifact_id, bytes(data), media_type=meta.media_type)

        # Optionally post to DA
        da_commitment: Optional[str] = None
        if self._da is not None:
            try:
                ns_val = None
                if hasattr(meta, "namespace") and meta.namespace is not None:  # type: ignore[attr-defined]
                    ns_val = int(meta.namespace)  # type: ignore[attr-defined]
                da_res = self._da.post(bytes(data), namespace=ns_val)
                da_commitment = str(da_res.get("commitment") or da_res.get("root") or "")
                if not da_commitment:
                    da_commitment = None
            except Exception as e:
                log.warning("DA post failed (non-fatal): %s", e)

        # Persist metadata row
        row = {
            "id": artifact_id,
            "kind": meta.kind,
            "address": (meta.address or "").lower() or None,
            "media_type": meta.media_type,
            "label": meta.label,
            "size": int(blob_desc.get("size", size)),
            "digest": digest_hex,
            "code_hash": code_hash,
            "da_commitment": da_commitment,
            "created_at": created_at,
        }
        self.meta.insert(row)

        out = ArtifactMeta(
            id=artifact_id,
            kind=meta.kind,
            address=meta.address,
            media_type=meta.media_type,
            label=meta.label,
            size=row["size"],
            digest=digest_hex,
            code_hash=code_hash,
            da_commitment=da_commitment,
            created_at=created_at,
        )
        return out

    def get(self, artifact_id: str) -> Tuple[ArtifactMeta, bytes]:
        """
        Fetch metadata and blob bytes for an artifact id.
        """
        if not artifact_id:
            raise BadRequest("artifact id is required")
        row = self.meta.get(artifact_id)
        payload = self.blobs.get_bytes(artifact_id)
        meta = ArtifactMeta(
            id=row["id"],
            kind=row.get("kind", ""),
            address=row.get("address"),
            media_type=row.get("media_type"),
            label=row.get("label"),
            size=int(row.get("size") or len(payload)),
            digest=row.get("digest"),
            code_hash=row.get("code_hash"),
            da_commitment=row.get("da_commitment"),
            created_at=int(row.get("created_at") or 0),
        )
        return meta, payload

    def get_meta(self, artifact_id: str) -> ArtifactMeta:
        """
        Fetch metadata only.
        """
        row = self.meta.get(artifact_id)
        return ArtifactMeta(
            id=row["id"],
            kind=row.get("kind", ""),
            address=row.get("address"),
            media_type=row.get("media_type"),
            label=row.get("label"),
            size=int(row.get("size") or 0),
            digest=row.get("digest"),
            code_hash=row.get("code_hash"),
            da_commitment=row.get("da_commitment"),
            created_at=int(row.get("created_at") or 0),
        )

    def list_by_address(self, address: str) -> List[ArtifactMeta]:
        """
        List artifacts linked to a given contract address.
        """
        self._validate_address(address)
        rows = self.meta.list_by_address(address)
        out: List[ArtifactMeta] = []
        for r in rows:
            out.append(
                ArtifactMeta(
                    id=r["id"],
                    kind=r.get("kind", ""),
                    address=r.get("address"),
                    media_type=r.get("media_type"),
                    label=r.get("label"),
                    size=int(r.get("size") or 0),
                    digest=r.get("digest"),
                    code_hash=r.get("code_hash"),
                    da_commitment=r.get("da_commitment"),
                    created_at=int(r.get("created_at") or 0),
                )
            )
        return out

    def link_to_address(self, address: str, artifact_id: str) -> ArtifactMeta:
        """
        Link an existing artifact to a contract address (e.g., after deployment/verification).
        """
        self._validate_address(address)
        row = self.meta.link(address, artifact_id)
        return ArtifactMeta(
            id=row["id"],
            kind=row.get("kind", ""),
            address=row.get("address"),
            media_type=row.get("media_type"),
            label=row.get("label"),
            size=int(row.get("size") or 0),
            digest=row.get("digest"),
            code_hash=row.get("code_hash"),
            da_commitment=row.get("da_commitment"),
            created_at=int(row.get("created_at") or 0),
        )


_SERVICE = ArtifactService()


def _decode_content_hex(content_hex: str) -> bytes:
    s = content_hex[2:] if content_hex.startswith("0x") else content_hex
    return bytes.fromhex(s)


def put_artifact(req: ArtifactPut) -> ArtifactMeta:
    data = _decode_content_hex(req.content)
    return _SERVICE.put(req, data)


def get_artifact(artifact_id: str) -> ArtifactMeta:
    meta, _ = _SERVICE.get(artifact_id)
    return meta


def list_artifacts_by_address(address: str) -> List[ArtifactMeta]:
    return _SERVICE.list_by_address(address)


__all__ = [
    "ArtifactService",
    "put_artifact",
    "get_artifact",
    "list_artifacts_by_address",
]
