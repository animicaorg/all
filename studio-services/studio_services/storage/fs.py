"""
Filesystem-backed, content-addressed artifact store (write-once).

- Uses SHA3-256 over the raw bytes; hex digest is stored with a 0x prefix.
- Fanout layout to avoid hot directories:
    <ROOT>/blobs/sha3-256/aa/bb/0xaabbcc... (no extension)
- Atomic writes via temp files + os.replace; concurrent-safe and idempotent.
- Metadata recorded in SQLite `artifacts` table.

Environment:
  STORAGE_DIR: base directory for blobs (default: ./.studio-services/storage)

Public API (write path):
  - store_bytes(data, mime=None, filename=None) -> ArtifactMeta
  - store_file(path, mime=None) -> ArtifactMeta
  - store_fileobj(fp, mime=None, filename=None) -> ArtifactMeta

Read path:
  - exists(content_hash) -> bool
  - file_path(content_hash) -> Path
  - open_stream(content_hash, chunk_size=...) -> Iterator[bytes]
  - read_bytes(content_hash) -> bytes
  - get_meta(content_hash) -> ArtifactMeta | None
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from io import BufferedReader, BytesIO, IOBase
from pathlib import Path
from typing import Iterator, Optional

from studio_services.storage.sqlite import get_db, transaction

# -----------------------------------------------------------------------------#
# Config
# -----------------------------------------------------------------------------#


def _storage_root() -> Path:
    base = os.getenv("STORAGE_DIR", "./.studio-services/storage")
    p = Path(base).expanduser().resolve()
    (p / "blobs" / "sha3-256").mkdir(parents=True, exist_ok=True)
    return p


ROOT = _storage_root()
BLOBS = ROOT / "blobs" / "sha3-256"
CHUNK = 1 << 20  # 1 MiB


# -----------------------------------------------------------------------------#
# Types & errors
# -----------------------------------------------------------------------------#


@dataclass(frozen=True)
class ArtifactMeta:
    content_hash: str  # 0x + hex
    size: int
    mime: Optional[str]
    filename: Optional[str]
    storage_backend: str  # 'fs'
    storage_locator: str  # relative path within ROOT (posix style)

    @property
    def abs_path(self) -> Path:
        return ROOT / Path(self.storage_locator)


class ArtifactMismatch(Exception):
    """Artifact exists with different size or contents."""


class ArtifactNotFound(FileNotFoundError):
    """Requested artifact blob not present on disk."""


# -----------------------------------------------------------------------------#
# Hashing & layout
# -----------------------------------------------------------------------------#


def _hex_hash_sha3_256() -> "hashlib._Hash":
    return hashlib.sha3_256()


def _to_0x(h: str) -> str:
    return "0x" + h if not h.startswith("0x") else h


def _layout_for_hash(content_hash: str) -> Path:
    """
    Map 0x… digest to relative path under BLOBS with two-level fanout.
    """
    h = content_hash[2:] if content_hash.startswith("0x") else content_hash
    a, b = h[:2], h[2:4]
    return Path("blobs") / "sha3-256" / a / b / _to_0x(h)


def file_path(content_hash: str) -> Path:
    """Absolute path for the blob file."""
    return ROOT / _layout_for_hash(content_hash)


def exists(content_hash: str) -> bool:
    return file_path(content_hash).exists()


# -----------------------------------------------------------------------------#
# DB helpers
# -----------------------------------------------------------------------------#


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
# Write paths (bytes / file / fileobj)
# -----------------------------------------------------------------------------#


@contextmanager
def _temp_under(path: Path) -> Iterator[Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent) as tf:
        tmp_path = Path(tf.name)
    try:
        yield tmp_path
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


def _finalize_write(tmp: Path, final_path: Path) -> None:
    # Atomic replace into place; ensure dir exists
    final_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, final_path)
    # Relaxed perms: 0644
    os.chmod(final_path, 0o644)


def _store_stream_and_hash(fp: IOBase) -> tuple[str, int, Path]:
    """
    Stream from fp → tmp file, compute SHA3-256, return (0xhash, size, final_abs_path).
    """
    hasher = _hex_hash_sha3_256()
    total = 0

    # We don't know final hash yet; stage under BLOBS
    # Use a temporary file under BLOBS to avoid cross-device moves.
    # We'll compute hash first, then compute final layout and rename.
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=BLOBS) as tf:
        tmp_path = Path(tf.name)
        while True:
            chunk = fp.read(CHUNK)
            if not chunk:
                break
            if isinstance(chunk, memoryview):
                chunk = chunk.tobytes()
            tf.write(chunk)
            hasher.update(chunk)
            total += len(chunk)

    content_hash = _to_0x(hasher.hexdigest())
    final_path = file_path(content_hash)
    if final_path.exists():
        # Already stored; verify size matches and remove temp
        tmp_path.unlink(missing_ok=True)
        if final_path.stat().st_size != total:
            raise ArtifactMismatch(
                f"Existing artifact {content_hash} size mismatch: "
                f"{final_path.stat().st_size} != {total}"
            )
        return content_hash, total, final_path

    # Move tmp into final destination (fanout path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp_path, final_path)
    os.chmod(final_path, 0o644)
    return content_hash, total, final_path


def store_fileobj(
    fp: IOBase,
    mime: Optional[str] = None,
    filename: Optional[str] = None,
) -> ArtifactMeta:
    """
    Store content from a binary file-like object. Does not rewind fp.
    """
    if isinstance(fp, BufferedReader):
        # fine
        pass
    elif hasattr(fp, "read"):
        # ensure binary mode-ish; leave as-is
        pass
    else:
        raise TypeError("fp must be a binary file-like object")

    h, size, final_path = _store_stream_and_hash(fp)
    locator = _layout_for_hash(h).as_posix()
    meta = ArtifactMeta(
        content_hash=h,
        size=size,
        mime=mime,
        filename=filename,
        storage_backend="fs",
        storage_locator=locator,
    )
    _upsert_artifact_row(meta)
    return meta


def store_bytes(
    data: bytes,
    mime: Optional[str] = None,
    filename: Optional[str] = None,
) -> ArtifactMeta:
    return store_fileobj(BytesIO(data), mime=mime, filename=filename)


def store_file(path: Path | str, mime: Optional[str] = None) -> ArtifactMeta:
    p = Path(path)
    with p.open("rb") as fp:
        return store_fileobj(fp, mime=mime, filename=p.name)


# -----------------------------------------------------------------------------#
# Read path
# -----------------------------------------------------------------------------#


def open_stream(content_hash: str, chunk_size: int = CHUNK) -> Iterator[bytes]:
    p = file_path(content_hash)
    if not p.exists():
        raise ArtifactNotFound(content_hash)
    with p.open("rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            yield b


def read_bytes(content_hash: str) -> bytes:
    p = file_path(content_hash)
    if not p.exists():
        raise ArtifactNotFound(content_hash)
    return p.read_bytes()


# -----------------------------------------------------------------------------#
# Integrity helpers
# -----------------------------------------------------------------------------#


def rehash_verify(content_hash: str) -> bool:
    """
    Recompute SHA3-256 and compare to the addressable name.
    Returns True on match; False if file missing or mismatched.
    """
    p = file_path(content_hash)
    if not p.exists():
        return False
    hasher = _hex_hash_sha3_256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            hasher.update(chunk)
    return _to_0x(hasher.hexdigest()) == content_hash


__all__ = [
    "ArtifactMeta",
    "ArtifactMismatch",
    "ArtifactNotFound",
    "store_bytes",
    "store_file",
    "store_fileobj",
    "exists",
    "file_path",
    "open_stream",
    "read_bytes",
    "get_meta",
    "rehash_verify",
]
