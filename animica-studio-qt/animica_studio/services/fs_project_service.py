"""Headless sandboxed filesystem service for the agent sidecar.

A Qt-free, DB-free port of :class:`ProjectService` (see ``project_service.py``)
bound to a single root directory (``REPO_DIR`` inside the container). Every
filesystem operation flows through :meth:`FsProjectService.resolve`, which
rejects absolute paths, ``..`` traversal escapes, and symlinks that resolve
outside the root.

This module imports ONLY the standard library so it is safe to load inside the
slim agent image (no PySide6, no database).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Directory/file names skipped when walking the tree.
IGNORE_GLOBS: tuple[str, ...] = (
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".idea",
    ".DS_Store",
)

# Cap for reading a single file (1 MiB) per CONTRACT 1.
DEFAULT_MAX_READ_BYTES = 1_000_000

# Bounds for tree walking so a pathological repo cannot exhaust memory.
MAX_TREE_DEPTH = 24
MAX_TREE_NODES = 20_000


class SandboxError(ValueError):
    """Raised when a path escapes the project sandbox."""


def _is_ignored(name: str) -> bool:
    return name in IGNORE_GLOBS


class FsProjectService:
    """Sandboxed filesystem manager rooted at a single directory."""

    def __init__(self, root: str | Path) -> None:
        raw = Path(root).expanduser()
        raw.mkdir(parents=True, exist_ok=True)
        # Resolve to canonical form so sandbox checks compare apples to apples.
        self._root: Path = raw.resolve()

    @property
    def root(self) -> Path:
        return self._root

    # ------------------------------------------------------------------ #
    # Sandbox resolution (ported verbatim from project_service.resolve)
    # ------------------------------------------------------------------ #
    def resolve(self, rel_path: str) -> Path:
        """Resolve a *relative* path against the root, sandboxed.

        Raises :class:`SandboxError` if the resulting path is absolute and
        outside the root, escapes the root via ``..`` components, or resolves
        (through symlinks) to a location outside the root.
        """
        root = self._root
        rel = (rel_path or "").strip()

        candidate = Path(rel)
        if candidate.is_absolute():
            # Allow an absolute path only if it is inside the root.
            try:
                candidate_resolved = candidate.resolve()
                candidate_resolved.relative_to(root)
            except (ValueError, OSError) as exc:
                raise SandboxError(
                    f"Absolute path escapes project sandbox: {rel_path}"
                ) from exc
            return candidate_resolved

        # Reject explicit parent traversal before resolving.
        parts = [p for p in candidate.parts if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise SandboxError(f"Path traversal is not allowed: {rel_path}")

        target = root / candidate
        try:
            resolved = target.resolve()
        except OSError as exc:  # pragma: no cover - unusual fs error
            raise SandboxError(f"Cannot resolve path: {rel_path}") from exc

        # Confirm the resolved path stays within the (resolved) root.
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise SandboxError(f"Path escapes project sandbox: {rel_path}") from exc
        return resolved

    def relative(self, abs_path: Path) -> str:
        """Return the POSIX-style path of ``abs_path`` relative to the root."""
        return abs_path.resolve().relative_to(self._root).as_posix()

    # ------------------------------------------------------------------ #
    # File tree (CONTRACT 1 node shape)
    # ------------------------------------------------------------------ #
    def tree(self) -> dict[str, Any]:
        """Return the root node ``{"name","path":"","type":"dir","children":[...]}``."""
        counter = {"n": 0}
        children = self._walk_dir(self._root, depth=0, counter=counter)
        return {
            "name": self._root.name or "repo",
            "path": "",
            "type": "dir",
            "children": children,
        }

    def _walk_dir(
        self, directory: Path, *, depth: int, counter: dict[str, int]
    ) -> list[dict[str, Any]]:
        if depth >= MAX_TREE_DEPTH:
            return []
        nodes: list[dict[str, Any]] = []
        try:
            entries = sorted(
                os.scandir(directory),
                key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()),
            )
        except OSError as exc:  # pragma: no cover
            logger.warning("Cannot scan %s: %s", directory, exc)
            return nodes

        for entry in entries:
            if _is_ignored(entry.name):
                continue
            if counter["n"] >= MAX_TREE_NODES:
                break
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            try:
                rel = self.relative(Path(entry.path))
            except (ValueError, OSError):
                continue
            counter["n"] += 1
            node: dict[str, Any] = {
                "name": entry.name,
                "path": rel,
                "type": "dir" if is_dir else "file",
            }
            if is_dir:
                node["children"] = self._walk_dir(
                    Path(entry.path), depth=depth + 1, counter=counter
                )
            nodes.append(node)
        return nodes

    # ------------------------------------------------------------------ #
    # File read/write (sandboxed)
    # ------------------------------------------------------------------ #
    def read_file(
        self, rel_path: str, max_bytes: int = DEFAULT_MAX_READ_BYTES
    ) -> dict[str, Any]:
        """Read a text file; return ``{"path","content","truncated"}``.

        Raises :class:`FileNotFoundError` if missing, :class:`IsADirectoryError`
        for directories. Binary or oversized files yield ``truncated=True`` with
        an empty ``content``.
        """
        target = self.resolve(rel_path)
        if not target.exists():
            raise FileNotFoundError(f"File not found: {rel_path}")
        if target.is_dir():
            raise IsADirectoryError(f"Is a directory: {rel_path}")

        size = target.stat().st_size
        if size > max_bytes:
            return {"path": rel_path, "content": "", "truncated": True}

        with target.open("rb") as fh:
            raw = fh.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        if truncated:
            return {"path": rel_path, "content": "", "truncated": True}

        # Detect binary: NUL byte or undecodable UTF-8 => treat as binary.
        if b"\x00" in raw:
            return {"path": rel_path, "content": "", "truncated": True}
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {"path": rel_path, "content": "", "truncated": True}
        return {"path": rel_path, "content": text, "truncated": False}

    def write_file(self, rel_path: str, content: str) -> None:
        """Overwrite (or create) a file with ``content`` atomically.

        Parent directories are created as needed.
        """
        target = self.resolve(rel_path)
        if target.is_dir():
            raise IsADirectoryError(f"Is a directory: {rel_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        tmp.replace(target)


__all__ = [
    "FsProjectService",
    "SandboxError",
    "IGNORE_GLOBS",
    "DEFAULT_MAX_READ_BYTES",
]
