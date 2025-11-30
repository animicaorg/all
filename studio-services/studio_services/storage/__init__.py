"""
studio_services.storage
=======================

Storage subsystem facades for Animica Studio Services.

This package exposes:
- A minimal `ArtifactStore` protocol used by the app (FS or S3 backends).
- A factory `build_artifact_store(...)` that chooses the right backend.
- Thin wrappers `get_db()` / `run_migrations()` for the SQLite metadata store.

Backends implemented in sibling modules:
- fs.py : local, content-addressed, write-once artifact store on the filesystem
- s3.py : optional S3-compatible store (feature-gated; imported lazily)
- sqlite.py : DB connection & migrations for metadata/queues/rate counters
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional, Protocol, runtime_checkable

# ----------------------------- Artifact Store API -----------------------------


@runtime_checkable
class ArtifactStore(Protocol):
    """
    Minimal interface the service uses to persist and serve artifacts.

    Implementations MUST be content-addressed and write-once (idempotent insert).
    The content hash MUST be a 0x-prefixed lowercase hex string.
    """

    def put_bytes(self, content: bytes) -> str:
        """Store bytes, returning the content-hash hex (0x...). Idempotent."""
        ...

    def exists(self, content_hash: str) -> bool:
        """True if the given content-hash is present."""
        ...

    def open(self, content_hash: str, mode: str = "rb") -> BinaryIO:
        """Open a file-like handle for the stored blob. Read-only for consumers."""
        ...

    def get_path(self, content_hash: str) -> Optional[Path]:
        """Return a local filesystem path if available (FS backend), else None."""
        ...

    def size(self, content_hash: str) -> Optional[int]:
        """Return blob size in bytes if known, else None."""
        ...


@dataclass(frozen=True)
class StorageConfig:
    """Resolved storage configuration used by the factory."""

    storage_dir: Path
    s3_bucket: Optional[str] = None
    s3_region: Optional[str] = None
    s3_endpoint: Optional[str] = None
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    s3_prefix: str = "artifacts/"


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name, default)
    return v if v not in ("", None) else None


def storage_config_from_env() -> StorageConfig:
    """
    Build a StorageConfig directly from environment variables.
    These correlate with `.env.example`:

      STORAGE_DIR
      S3_BUCKET, S3_REGION, S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, S3_PREFIX
    """
    storage_dir = Path(_env("STORAGE_DIR", "./.studio-services/artifacts")).resolve()
    return StorageConfig(
        storage_dir=storage_dir,
        s3_bucket=_env("S3_BUCKET"),
        s3_region=_env("S3_REGION"),
        s3_endpoint=_env("S3_ENDPOINT"),
        s3_access_key=_env("S3_ACCESS_KEY"),
        s3_secret_key=_env("S3_SECRET_KEY"),
        s3_prefix=_env("S3_PREFIX", "artifacts/") or "artifacts/",
    )


def build_artifact_store(
    cfg: StorageConfig | None = None,
    *,
    prefer_s3: Optional[bool] = None,
    extra_s3_kwargs: Optional[Dict[str, Any]] = None,
) -> ArtifactStore:
    """
    Construct an ArtifactStore. Defaults to FS unless S3 is configured or requested.

    Args:
      cfg: StorageConfig; if None, read from environment.
      prefer_s3: If True, prefer S3 when S3 settings exist; if False, force FS.
      extra_s3_kwargs: Extra kwargs forwarded to S3ArtifactStore constructor.

    Returns:
      ArtifactStore implementation (FS or S3).

    Raises:
      RuntimeError if S3 requested but s3 backend/deps are unavailable.
    """
    if cfg is None:
        cfg = storage_config_from_env()

    # Local FS backend is always available
    from .fs import \
        FSArtifactStore  # local import to avoid cycles during packaging

    s3_wanted = (prefer_s3 is True) or (prefer_s3 is None and cfg.s3_bucket is not None)

    if s3_wanted and cfg.s3_bucket:
        try:
            # Import lazily so deployments without S3 deps still work
            from .s3 import S3ArtifactStore  # type: ignore
        except Exception as e:  # pragma: no cover - feature gated path
            raise RuntimeError(
                "S3 storage requested but the S3 backend is not available"
            ) from e

        kwargs: Dict[str, Any] = dict(
            bucket=cfg.s3_bucket,
            region=cfg.s3_region,
            endpoint=cfg.s3_endpoint,
            access_key=cfg.s3_access_key,
            secret_key=cfg.s3_secret_key,
            key_prefix=cfg.s3_prefix,
        )
        if extra_s3_kwargs:
            kwargs.update(extra_s3_kwargs)
        return S3ArtifactStore(**kwargs)  # type: ignore[misc]

    # Default to filesystem
    return FSArtifactStore(root=cfg.storage_dir)


# ------------------------------ DB helper facades -----------------------------


def get_db():
    """
    Acquire a DB connection/handle from sqlite backend.

    Imported lazily to avoid import-order issues while generating files.
    """
    from .sqlite import get_db as _get_db  # local import

    return _get_db()


def run_migrations():
    """
    Apply pending migrations (idempotent). Safe to call at startup.
    """
    from .sqlite import run_migrations as _run_migrations  # local import

    return _run_migrations()


__all__ = [
    "ArtifactStore",
    "StorageConfig",
    "storage_config_from_env",
    "build_artifact_store",
    "get_db",
    "run_migrations",
]
