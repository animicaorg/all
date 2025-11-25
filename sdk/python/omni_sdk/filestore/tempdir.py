"""
omni_sdk.filestore.tempdir
==========================

Utilities for safely creating/cleaning temporary working directories and
storing artifacts in a content-addressed layout.

Features
--------
- `TempDir` context manager with robust cleanup (even on Windows)
- `temp_dir()` convenience context manager that yields a `pathlib.Path`
- `ensure_dir()` idempotent directory creation
- `atomic_write()` same-dir temp + fsync + atomic replace
- `content_addressed_path()` deterministic path from bytes and hash algo
- `write_blob_ca()` write bytes into content-addressed store (idempotent)

No third-party dependencies. Uses `hashlib.sha3_256/512` by default, and will
optionally use `blake3` if installed and selected via `algo="blake3"`.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional, Tuple, Union

# ------------------------------------------------------------
# Hash selection
# ------------------------------------------------------------

_Hasher = Callable[[bytes], bytes]


def _sha3_256(b: bytes) -> bytes:
    import hashlib

    return hashlib.sha3_256(b).digest()


def _sha3_512(b: bytes) -> bytes:
    import hashlib

    return hashlib.sha3_512(b).digest()


def _blake3(b: bytes) -> bytes:
    try:
        import blake3  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("blake3 requested but module not available") from e
    return blake3.blake3(b).digest()


def _select_hash(algo: str) -> _Hasher:
    a = algo.lower().replace("_", "-")
    if a in ("sha3-256", "sha3_256"):
        return _sha3_256
    if a in ("sha3-512", "sha3_512"):
        return _sha3_512
    if a == "blake3":
        return _blake3
    raise ValueError(f"Unsupported hash algo: {algo!r} (expected sha3-256|sha3-512|blake3)")


# ------------------------------------------------------------
# Directory helpers
# ------------------------------------------------------------

def ensure_dir(path: Union[str, Path], *, mode: int = 0o755) -> Path:
    """
    Create a directory (and parents) if missing. Safe under races.

    Returns the absolute `Path`.
    """
    p = Path(path).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    # Best-effort set mode (POSIX); harmless on Windows
    with contextlib.suppress(Exception):
        os.chmod(p, mode)
    return p


@dataclass
class TempDir:
    """
    Context-managed temporary directory with robust cleanup.

    Examples
    --------
    >>> with TempDir(prefix="animica_") as td:
    ...     (td.path / "hello.txt").write_text("hi")
    ...     # use td.path
    >>> # directory removed unless keep=True
    """
    prefix: str = "omni_sdk_"
    suffix: str = ""
    dir: Optional[Union[str, Path]] = None
    keep: bool = False

    _path: Optional[Path] = None

    @property
    def path(self) -> Path:
        if self._path is None:
            raise RuntimeError("TempDir has not been entered yet")
        return self._path

    def __enter__(self) -> "TempDir":
        base = Path(self.dir).expanduser().resolve() if self.dir else None
        if base:
            ensure_dir(base)
        d = tempfile.mkdtemp(prefix=self.prefix, suffix=self.suffix, dir=str(base) if base else None)
        self._path = Path(d)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        p = self._path
        if not p:
            return
        if self.keep:
            return
        # Robust rmtree: ignore errors, handle readonly files, Windows quirks
        def _onerror(fn, path, exc_info):  # pragma: no cover
            # Try to make file writable then remove
            with contextlib.suppress(Exception):
                os.chmod(path, 0o700)
                fn(path)
        with contextlib.suppress(Exception):
            shutil.rmtree(p, onerror=_onerror)
        self._path = None


@contextlib.contextmanager
def temp_dir(
    *, prefix: str = "omni_sdk_", suffix: str = "", dir: Optional[Union[str, Path]] = None, keep: bool = False
) -> Iterator[Path]:
    """
    Convenience context manager that yields the `Path` of a temporary directory.

    Example
    -------
    >>> with temp_dir() as d:
    ...     (d / "file.bin").write_bytes(b"ok")
    """
    with TempDir(prefix=prefix, suffix=suffix, dir=dir, keep=keep) as td:
        yield td.path


# ------------------------------------------------------------
# File helpers
# ------------------------------------------------------------

def _fsync_dir(dirpath: Path) -> None:
    # Best-effort directory fsync for durability; harmless if fails on some FS
    try:
        fd = os.open(str(dirpath), os.O_DIRECTORY)  # type: ignore[attr-defined]
    except (AttributeError, FileNotFoundError, NotADirectoryError, PermissionError, OSError):  # pragma: no cover
        return
    try:
        os.fsync(fd)
    except Exception:  # pragma: no cover
        pass
    finally:
        os.close(fd)


def atomic_write(path: Union[str, Path], data: Union[bytes, bytearray, memoryview], *, mode: int = 0o644) -> Path:
    """
    Atomically write `data` to `path` with a temp file + replace.

    Ensures parent directory exists. fsyncs the file and (best-effort) the
    directory. Sets POSIX mode if possible.

    Returns the absolute `Path` written.
    """
    target = Path(path).expanduser().resolve()
    parent = ensure_dir(target.parent)
    # Create temp file in the same directory to ensure atomic rename
    fd, tmpname = tempfile.mkstemp(prefix=".tmp.", dir=str(parent))
    tmp = Path(tmpname)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(memoryview(data))
            f.flush()
            os.fsync(f.fileno())
        with contextlib.suppress(Exception):
            os.chmod(tmp, mode)
        # Atomic replace (os.replace is atomic on POSIX/Windows)
        os.replace(str(tmp), str(target))
        _fsync_dir(parent)
    finally:
        # If replace failed, ensure temp file is gone
        if tmp.exists():
            with contextlib.suppress(Exception):
                tmp.unlink()
    return target


# ------------------------------------------------------------
# Content-addressed storage
# ------------------------------------------------------------

def _digest_bytes(data: Union[bytes, bytearray, memoryview], *, algo: str) -> bytes:
    hasher = _select_hash(algo)
    return hasher(memoryview(data))

def _hex(b: bytes) -> str:
    return "0x" + b.hex()


def content_addressed_path(
    root: Union[str, Path],
    data: Union[bytes, bytearray, memoryview],
    *,
    algo: str = "sha3-256",
    fanout: Tuple[int, int] = (2, 2),
    ext: Optional[str] = None,
) -> Tuple[Path, str]:
    """
    Compute the deterministic path for `data` under `root` using the given hash.

    Layout:  <root>/<algo>/<f1>/<f2>/<digest>[.<ext>]

    where f1/f2 are fanout buckets built from the hex digest with `fanout` bytes.
    Returns (path, hex_digest_with_0x).
    """
    digest = _digest_bytes(data, algo=algo)
    hex_digest = digest.hex()
    # fanout in BYTES (2 hex chars each)
    f1_bytes, f2_bytes = fanout
    f1 = hex_digest[0 : 2 * max(0, f1_bytes)]
    f2 = hex_digest[2 * max(0, f1_bytes) : 2 * (max(0, f1_bytes) + max(0, f2_bytes))]

    base = ensure_dir(Path(root).expanduser().resolve())
    algo_dir = ensure_dir(base / algo.lower().replace("_", "-"))
    d1 = ensure_dir(algo_dir / f1) if f1 else algo_dir
    d2 = ensure_dir(d1 / f2) if f2 else d1

    filename = hex_digest
    if ext:
        e = ext if ext.startswith(".") else f".{ext}"
        filename = f"{filename}{e}"
    return (d2 / filename, _hex(digest))


@dataclass(frozen=True)
class CAWriteResult:
    """Result of `write_blob_ca` for convenience."""
    path: Path
    digest_hex: str
    algo: str
    created: bool
    size: int


def write_blob_ca(
    root: Union[str, Path],
    data: Union[bytes, bytearray, memoryview],
    *,
    algo: str = "sha3-256",
    ext: Optional[str] = None,
    allow_replace: bool = False,
) -> CAWriteResult:
    """
    Write `data` into a content-addressed store rooted at `root`.

    - If the target file already exists, it is left as-is unless `allow_replace=True`,
      in which case the write is performed atomically (useful for repairing partial files).
    - Returns metadata including whether a new file was created.

    The file name is the hex digest; caller can choose `ext` if desired.
    """
    target, hex_digest = content_addressed_path(root, data, algo=algo, ext=ext)
    created = False
    size = len(memoryview(data))
    if target.exists():
        if allow_replace:
            atomic_write(target, data)
        # else leave existing file untouched
    else:
        # Ensure parent exists (content_addressed_path already did)
        atomic_write(target, data)
        created = True
    return CAWriteResult(path=target, digest_hex=hex_digest, algo=algo, created=created, size=size)


__all__ = [
    "TempDir",
    "temp_dir",
    "ensure_dir",
    "atomic_write",
    "content_addressed_path",
    "write_blob_ca",
    "CAWriteResult",
]
