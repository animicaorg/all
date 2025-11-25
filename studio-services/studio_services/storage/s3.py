"""
S3-compatible artifact store (optional / feature-gated).

Write-once, content-addressed blobs:
- Hash: SHA3-256 over raw bytes → "0x" + hex digest
- Layout key: <PREFIX>/<aa>/<bb>/<0xhash>
- Metadata is mirrored in SQLite `artifacts` (same shape as fs backend)

Environment (typical):
  S3_BUCKET=animica-artifacts
  S3_PREFIX=artifacts/blobs/sha3-256
  S3_ENDPOINT_URL=https://s3.amazonaws.com         # or MinIO/other
  S3_REGION=us-east-1
  S3_SSE=                                         # '', 'AES256', or 'aws:kms'
  S3_SSE_KMS_KEY_ID=                              # required if S3_SSE='aws:kms'
  AWS_ACCESS_KEY_ID=...                           # standard AWS creds (if needed)
  AWS_SECRET_ACCESS_KEY=...

Notes:
- Requires boto3/botocore. If missing, importing any store function will raise
  an ImportError with a clear "pip install boto3" hint.
- We spool uploads to a temp file while hashing to ensure the address (hash) is
  known before the put; then we upload from disk using multipart-aware client.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Optional

from studio_services.storage.sqlite import transaction, get_db

try:
    import boto3
    from botocore.config import Config as _BotoConfig
    from botocore.exceptions import ClientError
    _HAS_BOTO = True
except Exception as _e:  # pragma: no cover
    boto3 = None
    ClientError = Exception  # type: ignore
    _BotoConfig = object      # type: ignore
    _HAS_BOTO = False
    _IMPORT_ERR = _e

# -----------------------------------------------------------------------------#
# Config helpers
# -----------------------------------------------------------------------------#

CHUNK = 1 << 20  # 1 MiB


def _req_boto():
    if not _HAS_BOTO:  # pragma: no cover
        raise ImportError(
            "The S3 storage backend requires boto3/botocore. "
            "Install with: pip install boto3"
        ) from _IMPORT_ERR


def _bucket() -> str:
    v = os.getenv("S3_BUCKET")
    if not v:
        raise RuntimeError("S3_BUCKET is required for S3 storage backend")
    return v


def _prefix() -> str:
    return os.getenv("S3_PREFIX", "artifacts/blobs/sha3-256").strip("/")


def _endpoint_url() -> Optional[str]:
    return os.getenv("S3_ENDPOINT_URL") or None


def _region() -> Optional[str]:
    return os.getenv("S3_REGION") or None


def _sse_args() -> dict:
    sse = (os.getenv("S3_SSE") or "").strip()
    if not sse:
        return {}
    if sse not in ("AES256", "aws:kms"):
        raise RuntimeError("S3_SSE must be '', 'AES256', or 'aws:kms'")
    args = {"ServerSideEncryption": sse}
    if sse == "aws:kms":
        key_id = os.getenv("S3_SSE_KMS_KEY_ID")
        if not key_id:
            raise RuntimeError("S3_SSE_KMS_KEY_ID is required when S3_SSE='aws:kms'")
        args["SSEKMSKeyId"] = key_id
    return args


@lru_cache(maxsize=1)
def _client():
    _req_boto()
    cfg = _BotoConfig(
        retries={"max_attempts": 10, "mode": "standard"},
        connect_timeout=10,
        read_timeout=120,
        tcp_keepalive=True,
        user_agent_extra="animica-studio-services/1 s3-backend",
    )
    return boto3.client(
        "s3",
        endpoint_url=_endpoint_url(),
        region_name=_region(),
        config=cfg,
    )


# -----------------------------------------------------------------------------#
# Types & DB helpers (mirror fs backend)
# -----------------------------------------------------------------------------#

@dataclass(frozen=True)
class ArtifactMeta:
    content_hash: str       # 0x + hex
    size: int
    mime: Optional[str]
    filename: Optional[str]
    storage_backend: str    # 's3'
    storage_locator: str    # "bucket/key" (no scheme)

    @property
    def bucket(self) -> str:
        return self.storage_locator.split("/", 1)[0]

    @property
    def key(self) -> str:
        return self.storage_locator.split("/", 1)[1]


def _to_0x(h: str) -> str:
    return "0x" + h if not h.startswith("0x") else h


def _key_for_hash(content_hash: str) -> str:
    h = content_hash[2:] if content_hash.startswith("0x") else content_hash
    a, b = h[:2], h[2:4]
    return f"{_prefix()}/{a}/{b}/{_to_0x(h)}"


def _upsert_artifact_row(meta: ArtifactMeta) -> None:
    with transaction() as db:
        db.execute(
            """
            INSERT INTO artifacts (content_hash, size, mime, filename, storage_backend, storage_locator)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(content_hash) DO UPDATE SET
                size=excluded.size,
                mime=COALESCE(excluded.mime, artifacts.mime),
                filename=COALESCE(excluded.filename, artifacts.filename),
                storage_backend=excluded.storage_backend,
                storage_locator=excluded.storage_locator
            """,
            (
                meta.content_hash,
                meta.size,
                meta.mime,
                meta.filename,
                meta.storage_backend,
                meta.storage_locator,
            ),
        )


def get_meta(content_hash: str) -> Optional[ArtifactMeta]:
    db = get_db()
    row = db.execute(
        "SELECT content_hash,size,mime,filename,storage_backend,storage_locator "
        "FROM artifacts WHERE content_hash = ?",
        (content_hash,),
    ).fetchone()
    if not row:
        return None
    return ArtifactMeta(
        content_hash=row["content_hash"],
        size=int(row["size"]),
        mime=row["mime"],
        filename=row["filename"],
        storage_backend=row["storage_backend"],
        storage_locator=row["storage_locator"],
    )


# -----------------------------------------------------------------------------#
# Errors
# -----------------------------------------------------------------------------#

class ArtifactMismatch(Exception):
    """Artifact exists in bucket with different size or contents."""


class ArtifactNotFound(FileNotFoundError):
    """Requested artifact blob not present in S3."""


# -----------------------------------------------------------------------------#
# Write paths
# -----------------------------------------------------------------------------#

def _sha3_hasher():
    return hashlib.sha3_256()


def _spool_and_hash(fp) -> tuple[str, int, Path]:
    """Stream from fp → temp file; compute sha3-256; return (0xhash, size, tmp_path)."""
    hasher = _sha3_hasher()
    total = 0
    with tempfile.NamedTemporaryFile("wb", delete=False) as tf:
        tmp_path = Path(tf.name)
        while True:
            chunk = fp.read(CHUNK)
            if not chunk:
                break
            tf.write(chunk)
            hasher.update(chunk)
            total += len(chunk)
    return _to_0x(hasher.hexdigest()), total, tmp_path


def store_fileobj(fp, mime: Optional[str] = None, filename: Optional[str] = None) -> ArtifactMeta:
    """
    Store content from a binary file-like object to S3 with content-addressed key.
    """
    _req_boto()
    content_hash, size, tmp = _spool_and_hash(fp)
    bucket = _bucket()
    key = _key_for_hash(content_hash)
    locator = f"{bucket}/{key}"

    client = _client()

    # If already exists, verify size and short-circuit (idempotent)
    try:
        head = client.head_object(Bucket=bucket, Key=key)
        remote_size = int(head.get("ContentLength", -1))
        if remote_size != size:
            tmp.unlink(missing_ok=True)
            raise ArtifactMismatch(
                f"Existing artifact {content_hash} has size {remote_size} but upload is {size}"
            )
        # OK: fill DB and return
        meta = ArtifactMeta(
            content_hash=content_hash,
            size=size,
            mime=mime or head.get("ContentType"),
            filename=filename,
            storage_backend="s3",
            storage_locator=locator,
        )
        _upsert_artifact_row(meta)
        tmp.unlink(missing_ok=True)
        return meta
    except ClientError as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code")
        if code not in ("404", "NoSuchKey", "NotFound"):
            tmp.unlink(missing_ok=True)
            raise

    extra = _sse_args()
    if mime:
        extra["ContentType"] = mime

    try:
        client.upload_file(
            Filename=str(tmp),
            Bucket=bucket,
            Key=key,
            ExtraArgs=extra or None,
        )
    finally:
        tmp.unlink(missing_ok=True)

    meta = ArtifactMeta(
        content_hash=content_hash,
        size=size,
        mime=mime or "application/octet-stream",
        filename=filename,
        storage_backend="s3",
        storage_locator=locator,
    )
    _upsert_artifact_row(meta)
    return meta


def store_bytes(data: bytes, mime: Optional[str] = None, filename: Optional[str] = None) -> ArtifactMeta:
    from io import BytesIO
    return store_fileobj(BytesIO(data), mime=mime, filename=filename)


def store_file(path: Path | str, mime: Optional[str] = None) -> ArtifactMeta:
    p = Path(path)
    with p.open("rb") as fp:
        return store_fileobj(fp, mime=mime, filename=p.name)


# -----------------------------------------------------------------------------#
# Read paths
# -----------------------------------------------------------------------------#

def _split_locator(locator: str) -> tuple[str, str]:
    parts = locator.split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Bad storage_locator for s3: {locator!r}")
    return parts[0], parts[1]


def exists(content_hash: str) -> bool:
    _req_boto()
    bucket = _bucket()
    key = _key_for_hash(content_hash)
    try:
        _client().head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def open_stream(content_hash: str, chunk_size: int = CHUNK) -> Iterator[bytes]:
    _req_boto()
    bucket = _bucket()
    key = _key_for_hash(content_hash)
    try:
        resp = _client().get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            raise ArtifactNotFound(content_hash)
        raise
    body = resp["Body"]
    while True:
        b = body.read(chunk_size)
        if not b:
            break
        yield b


def read_bytes(content_hash: str) -> bytes:
    _req_boto()
    bucket = _bucket()
    key = _key_for_hash(content_hash)
    try:
        resp = _client().get_object(Bucket=bucket, Key=key)
        return resp["Body"].read()
    except ClientError as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            raise ArtifactNotFound(content_hash)
        raise


# -----------------------------------------------------------------------------#
# Integrity helpers
# -----------------------------------------------------------------------------#

def rehash_verify(content_hash: str) -> bool:
    """
    Stream object from S3, recompute SHA3-256, and compare to expected hash.
    """
    hasher = hashlib.sha3_256()
    try:
        for chunk in open_stream(content_hash, chunk_size=CHUNK):
            hasher.update(chunk)
    except ArtifactNotFound:
        return False
    return _to_0x(hasher.hexdigest()) == content_hash


__all__ = [
    "ArtifactMeta",
    "ArtifactMismatch",
    "ArtifactNotFound",
    "store_bytes",
    "store_file",
    "store_fileobj",
    "exists",
    "open_stream",
    "read_bytes",
    "get_meta",
    "rehash_verify",
]
