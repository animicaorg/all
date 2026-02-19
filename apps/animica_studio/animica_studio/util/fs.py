"""Filesystem utility helpers for Animica Studio."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

log = logging.getLogger(__name__)


def ensure_dir(path: str | Path) -> bool:
    """Create *path* (and parents) if it does not exist.

    Returns
    -------
    bool
        ``True`` on success, ``False`` if creation failed.
    """
    p = Path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
        return True
    except OSError as exc:
        log.warning("ensure_dir: could not create %s: %s", p, exc)
        return False


def check_writable_dir(path: str | Path) -> tuple[bool, str | None]:
    """Check whether *path* is a writable directory.

    If *path* does not exist, an attempt is made to create it first via
    :func:`ensure_dir`.

    Returns
    -------
    (ok, error)
        *ok* is ``True`` if the directory is confirmed writable.
        *error* is a human-readable description of the problem, or ``None``.
    """
    p = Path(path)

    if not p.exists():
        created = ensure_dir(p)
        if not created:
            return False, f"Directory does not exist and could not be created: {p}"

    if not p.is_dir():
        return False, f"Path exists but is not a directory: {p}"

    ok, err = atomic_write_test(p)
    return ok, err


def atomic_write_test(directory: str | Path) -> tuple[bool, str | None]:
    """Attempt to create, write, and delete a temporary file inside *directory*.

    This validates write permissions without permanently modifying the directory.

    Returns
    -------
    (ok, error)
        *ok* is ``True`` if the write test succeeded.
        *error* is a human-readable description, or ``None`` on success.
    """
    d = Path(directory)
    tmp_name = f".animica_write_test_{uuid.uuid4().hex[:8]}"
    tmp_path = d / tmp_name

    try:
        tmp_path.write_text("ok", encoding="utf-8")
        tmp_path.unlink()
        return True, None
    except PermissionError as exc:
        return False, f"Permission denied: {exc}"
    except OSError as exc:
        return False, f"Write test failed: {exc}"
    finally:
        # Best-effort cleanup
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
