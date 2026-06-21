"""Project workspace service for Animica Studio Qt.

:class:`ProjectService` opens a folder as a sandboxed project, walks the file
tree (respecting ignore globs), and performs read/write/create/delete/rename
operations that are strictly confined to the project root. Every filesystem
operation flows through :meth:`ProjectService.resolve`, which rejects absolute
paths, ``..`` traversal escapes, and symlinks that point outside the root.

The service is a :class:`QObject` so it can emit Qt signals (``fileTreeChanged``,
``projectOpened``). It also persists opened projects to the :class:`Database`
when one is supplied.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QObject, Signal

from ..db import Database
from ..models import Project, new_id, now_ts

logger = logging.getLogger(__name__)

# Directory/file names ignored when walking, scanning, or indexing a project.
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

# Default cap for reading a single file into context.
DEFAULT_MAX_READ_BYTES = 1_000_000


class SandboxError(ValueError):
    """Raised when a path escapes the project sandbox."""


class ProjectNotOpenError(RuntimeError):
    """Raised when an operation requires an open project but none is set."""


def _is_ignored(name: str) -> bool:
    """Return True if a single path component should be ignored."""
    return name in IGNORE_GLOBS


class ProjectService(QObject):
    """Sandboxed project workspace manager."""

    fileTreeChanged = Signal()
    projectOpened = Signal(str)  # root_path

    def __init__(self, db: Optional[Database] = None) -> None:
        super().__init__()
        self._db = db
        self._root: Optional[Path] = None
        self._project: Optional[Project] = None

    # ------------------------------------------------------------------ #
    # Open / state
    # ------------------------------------------------------------------ #
    def open_project(self, path: str) -> Project:
        """Open ``path`` as the active (sandboxed) project root.

        Persists/updates the project record in the database when available and
        emits :data:`projectOpened` and :data:`fileTreeChanged`.
        """
        raw = Path(path).expanduser()
        try:
            root = raw.resolve(strict=True)
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Project path does not exist: {path}") from exc
        if not root.is_dir():
            raise NotADirectoryError(f"Project path is not a directory: {path}")

        self._root = root
        project = self._build_project_record(root)
        self._project = project
        if self._db is not None:
            try:
                self._project = self._db.upsert_project(project)
            except Exception as exc:  # pragma: no cover - db best-effort
                logger.warning("Failed to persist project: %s", exc)

        self.projectOpened.emit(str(root))
        self.fileTreeChanged.emit()
        return self._project

    def _build_project_record(self, root: Path) -> Project:
        existing: Optional[Project] = None
        if self._db is not None:
            try:
                existing = self._db.get_project_by_path(str(root))
            except Exception:  # pragma: no cover
                existing = None
        ptype = self.detect_project_type(root)
        if existing is not None:
            return Project(
                id=existing.id,
                name=existing.name or root.name,
                root_path=str(root),
                project_type=ptype,
                description=existing.description,
                last_opened_at=now_ts(),
                created_at=existing.created_at or now_ts(),
                metadata=existing.metadata,
            )
        return Project(
            id=new_id(),
            name=root.name,
            root_path=str(root),
            project_type=ptype,
            description="",
            last_opened_at=now_ts(),
            created_at=now_ts(),
        )

    @property
    def root(self) -> Optional[Path]:
        return self._root

    @property
    def project(self) -> Optional[Project]:
        return self._project

    def is_open(self) -> bool:
        return self._root is not None

    def _require_root(self) -> Path:
        if self._root is None:
            raise ProjectNotOpenError("No project is currently open.")
        return self._root

    # ------------------------------------------------------------------ #
    # Sandbox resolution
    # ------------------------------------------------------------------ #
    def resolve(self, rel_path: str) -> Path:
        """Resolve a *relative* path against the project root, sandboxed.

        Raises :class:`SandboxError` if the resulting path is absolute, escapes
        the root via ``..`` components, or resolves (through symlinks) to a
        location outside the project root.
        """
        root = self._require_root()
        rel = (rel_path or "").strip()

        # Normalize separators; treat empty / "." as the root itself.
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

        target = (root / candidate)
        # Resolve symlinks where possible without requiring existence.
        try:
            resolved = target.resolve()
        except OSError as exc:  # pragma: no cover - unusual fs error
            raise SandboxError(f"Cannot resolve path: {rel_path}") from exc

        # Confirm the resolved path stays within the (resolved) root.
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise SandboxError(
                f"Path escapes project sandbox: {rel_path}"
            ) from exc
        return resolved

    def relative(self, abs_path: Path) -> str:
        """Return the POSIX-style path of ``abs_path`` relative to the root."""
        root = self._require_root()
        return abs_path.resolve().relative_to(root).as_posix()

    # ------------------------------------------------------------------ #
    # File tree
    # ------------------------------------------------------------------ #
    def file_tree(self, rel_dir: str = "") -> list[dict[str, Any]]:
        """Return a nested list of file-tree nodes under ``rel_dir``.

        Each node: ``{"name", "path", "is_dir", "size", "children"?}`` where
        ``path`` is relative to the project root. Ignore globs are skipped.
        """
        base = self.resolve(rel_dir) if rel_dir else self._require_root()
        if not base.is_dir():
            raise NotADirectoryError(f"Not a directory: {rel_dir}")
        return self._walk_dir(base)

    def _walk_dir(self, directory: Path) -> list[dict[str, Any]]:
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
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            try:
                rel = self.relative(Path(entry.path))
            except (ValueError, OSError):
                continue
            node: dict[str, Any] = {
                "name": entry.name,
                "path": rel,
                "is_dir": is_dir,
                "size": 0,
            }
            if is_dir:
                node["children"] = self._walk_dir(Path(entry.path))
            else:
                try:
                    node["size"] = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    node["size"] = 0
            nodes.append(node)
        return nodes

    def list_dir(self, rel_dir: str = "") -> list[dict[str, Any]]:
        """Return a *shallow* listing (one level) of ``rel_dir``."""
        base = self.resolve(rel_dir) if rel_dir else self._require_root()
        if not base.is_dir():
            raise NotADirectoryError(f"Not a directory: {rel_dir}")
        nodes: list[dict[str, Any]] = []
        try:
            entries = sorted(
                os.scandir(base),
                key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()),
            )
        except OSError:  # pragma: no cover
            return nodes
        for entry in entries:
            if _is_ignored(entry.name):
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                rel = self.relative(Path(entry.path))
            except (OSError, ValueError):
                continue
            size = 0
            if not is_dir:
                try:
                    size = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    size = 0
            nodes.append({"name": entry.name, "path": rel, "is_dir": is_dir, "size": size})
        return nodes

    # ------------------------------------------------------------------ #
    # File operations (sandboxed)
    # ------------------------------------------------------------------ #
    def read_file(self, rel_path: str, max_bytes: int = DEFAULT_MAX_READ_BYTES) -> str:
        """Read a text file relative to the root, capped at ``max_bytes``."""
        target = self.resolve(rel_path)
        if not target.exists():
            raise FileNotFoundError(f"File not found: {rel_path}")
        if target.is_dir():
            raise IsADirectoryError(f"Is a directory: {rel_path}")
        size = target.stat().st_size
        with target.open("rb") as fh:
            raw = fh.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        if truncated:
            raw = raw[:max_bytes]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        if truncated:
            text += (
                f"\n\n... [truncated: file is {size} bytes, "
                f"showing first {max_bytes}]"
            )
        return text

    def write_file(self, rel_path: str, content: str) -> None:
        """Overwrite (or create) a file with ``content`` atomically."""
        target = self.resolve(rel_path)
        if target.is_dir():
            raise IsADirectoryError(f"Is a directory: {rel_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        tmp.replace(target)
        self.fileTreeChanged.emit()

    def create_file(self, rel_path: str, content: str = "") -> None:
        """Create a new file, failing if it already exists."""
        target = self.resolve(rel_path)
        if target.exists():
            raise FileExistsError(f"File already exists: {rel_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        self.fileTreeChanged.emit()

    def create_dir(self, rel_path: str) -> None:
        """Create a directory (and parents) within the sandbox."""
        target = self.resolve(rel_path)
        target.mkdir(parents=True, exist_ok=True)
        self.fileTreeChanged.emit()

    def delete_file(self, rel_path: str) -> None:
        """Delete a file or directory tree within the sandbox."""
        target = self.resolve(rel_path)
        if target == self._require_root():
            raise SandboxError("Refusing to delete the project root.")
        if not target.exists():
            raise FileNotFoundError(f"Path not found: {rel_path}")
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
        self.fileTreeChanged.emit()

    def rename_file(self, src_rel: str, dst_rel: str) -> None:
        """Rename/move a file or directory within the sandbox."""
        src = self.resolve(src_rel)
        dst = self.resolve(dst_rel)
        if not src.exists():
            raise FileNotFoundError(f"Source not found: {src_rel}")
        if dst.exists():
            raise FileExistsError(f"Destination already exists: {dst_rel}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)
        self.fileTreeChanged.emit()

    def exists(self, rel_path: str) -> bool:
        try:
            return self.resolve(rel_path).exists()
        except SandboxError:
            return False

    # ------------------------------------------------------------------ #
    # Project type detection + summary
    # ------------------------------------------------------------------ #
    def detect_project_type(self, root: Optional[Path] = None) -> str:
        """Heuristically detect the project type.

        Returns one of: ``python``, ``fastapi``, ``qt``, ``react``, ``node``,
        ``rust``, ``go``, ``static``, ``animica``, or ``unknown``.
        """
        base = root or self._root
        if base is None:
            return "unknown"

        names = set()
        try:
            for entry in os.scandir(base):
                names.add(entry.name)
        except OSError:  # pragma: no cover
            return "unknown"

        def has(name: str) -> bool:
            return name in names

        # Animica-specific markers.
        if has("animica.toml") or has(".animica") or has("animica_agent.py"):
            return "animica"

        # Rust / Go.
        if has("Cargo.toml"):
            return "rust"
        if has("go.mod"):
            return "go"

        # Node / React.
        if has("package.json"):
            kind = self._classify_node(base)
            return kind

        # Python flavors.
        py_markers = {
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "requirements.txt",
            "Pipfile",
        }
        if names & py_markers or self._has_python_files(base):
            flavor = self._classify_python(base)
            return flavor

        # Static site.
        if has("index.html"):
            return "static"

        return "unknown"

    def _classify_node(self, base: Path) -> str:
        pkg = base / "package.json"
        try:
            import json

            data = json.loads(pkg.read_text(encoding="utf-8"))
        except Exception:
            return "node"
        deps = {}
        deps.update(data.get("dependencies", {}) or {})
        deps.update(data.get("devDependencies", {}) or {})
        if "react" in deps or "next" in deps or "react-dom" in deps:
            return "react"
        return "node"

    def _classify_python(self, base: Path) -> str:
        text_blobs: list[str] = []
        for fname in ("pyproject.toml", "requirements.txt", "setup.py", "Pipfile"):
            fp = base / fname
            if fp.exists():
                try:
                    text_blobs.append(fp.read_text(encoding="utf-8", errors="ignore").lower())
                except OSError:
                    pass
        blob = "\n".join(text_blobs)
        if "fastapi" in blob or "uvicorn" in blob or "starlette" in blob:
            return "fastapi"
        if "pyside6" in blob or "pyqt" in blob or "qt" in blob and "pyside" in blob:
            return "qt"
        # Lightweight scan of a few python files for Qt imports.
        if self._scan_python_imports(base, ("PySide6", "PyQt5", "PyQt6")):
            return "qt"
        if self._scan_python_imports(base, ("fastapi",)):
            return "fastapi"
        return "python"

    @staticmethod
    def _has_python_files(base: Path) -> bool:
        try:
            for entry in os.scandir(base):
                if entry.is_file() and entry.name.endswith(".py"):
                    return True
        except OSError:  # pragma: no cover
            return False
        return False

    def _scan_python_imports(self, base: Path, needles: tuple[str, ...]) -> bool:
        count = 0
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not _is_ignored(d)]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                count += 1
                if count > 200:  # bound the scan
                    return False
                try:
                    head = Path(dirpath, fn).read_text(
                        encoding="utf-8", errors="ignore"
                    )[:4000]
                except OSError:
                    continue
                for needle in needles:
                    if needle in head:
                        return True
        return False

    def summarize_project(self) -> str:
        """Return a short human-readable overview of the open project."""
        if self._root is None:
            return "No project is open."
        root = self._root
        ptype = self.detect_project_type(root)

        file_count = 0
        dir_count = 0
        total_bytes = 0
        lang_counts: dict[str, int] = {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not _is_ignored(d)]
            dir_count += len(dirnames)
            for fn in filenames:
                if _is_ignored(fn):
                    continue
                file_count += 1
                ext = Path(fn).suffix.lower() or "(none)"
                lang_counts[ext] = lang_counts.get(ext, 0) + 1
                try:
                    total_bytes += Path(dirpath, fn).stat().st_size
                except OSError:
                    pass
            if file_count > 20000:  # safety bound
                break

        top_exts = sorted(lang_counts.items(), key=lambda kv: kv[1], reverse=True)[:6]
        ext_str = ", ".join(f"{ext} ({n})" for ext, n in top_exts) or "none"

        # Notable top-level config files.
        markers: list[str] = []
        for marker in (
            "pyproject.toml",
            "package.json",
            "Cargo.toml",
            "go.mod",
            "requirements.txt",
            "Dockerfile",
            "README.md",
        ):
            if (root / marker).exists():
                markers.append(marker)

        size_mb = total_bytes / (1024 * 1024)
        lines = [
            f"Project: {root.name}",
            f"Path: {root}",
            f"Type: {ptype}",
            f"Files: {file_count} (in {dir_count} dirs), ~{size_mb:.2f} MB",
            f"File types: {ext_str}",
        ]
        if markers:
            lines.append(f"Config: {', '.join(markers)}")
        return "\n".join(lines)

    def suggest_verify_command(self) -> Optional[str]:
        """Best-effort build/test command to confirm the project still works.

        Returns a shell command string suited to the detected project type, or
        ``None`` when no meaningful verification exists (e.g. a static site).
        Used by the agent's ``run_tests`` tool and the engine's auto-verify loop.
        """
        root = self._root
        if root is None:
            return None
        ptype = self.detect_project_type(root)

        def _exists(*names: str) -> bool:
            return any((root / n).exists() for n in names)

        if ptype in ("python", "fastapi", "qt", "animica"):
            if _exists("tests", "test") or self._has_test_files(root):
                return "python3 -m pytest -q"
            return "python3 -m compileall -q ."
        if ptype in ("node", "react"):
            script = self._npm_script(root)
            if script == "test":
                return "npm test --silent"
            if script == "build":
                return "npm run build"
            if (root / "index.js").exists():
                return "node --check index.js"
            return None
        if ptype == "rust":
            return "cargo build"
        if ptype == "go":
            return "go build ./..."
        return None

    @staticmethod
    def _has_test_files(root: Path) -> bool:
        try:
            for entry in os.scandir(root):
                if entry.is_file() and (
                    entry.name.startswith("test_") and entry.name.endswith(".py")
                    or entry.name.endswith("_test.py")
                ):
                    return True
        except OSError:  # pragma: no cover
            return False
        return False

    @staticmethod
    def _npm_script(root: Path) -> Optional[str]:
        pkg = root / "package.json"
        if not pkg.exists():
            return None
        try:
            import json

            scripts = (json.loads(pkg.read_text(encoding="utf-8")) or {}).get("scripts", {})
        except Exception:
            return None
        if not isinstance(scripts, dict):
            return None
        if scripts.get("test") and "no test specified" not in str(scripts.get("test")):
            return "test"
        if scripts.get("build"):
            return "build"
        return None

    # ------------------------------------------------------------------ #
    # Snapshots (rollback support for the diff viewer)
    # ------------------------------------------------------------------ #
    def _snapshots_root(self) -> Path:
        """Base directory for this service's snapshots."""
        try:
            from ..config import Paths

            base = Paths.resolve().ensure().snapshots_dir
        except Exception:  # pragma: no cover - fallback
            base = self._require_root() / ".animica_snapshots"
        base.mkdir(parents=True, exist_ok=True)
        return base

    def snapshot(self, rel_paths: list[str]) -> str:
        """Snapshot the current contents of ``rel_paths``; return a snapshot id."""
        snap_id = new_id()
        snap_dir = self._snapshots_root() / snap_id
        snap_dir.mkdir(parents=True, exist_ok=True)

        manifest: list[dict[str, Any]] = []
        for rel in rel_paths:
            target = self.resolve(rel)
            existed = target.exists() and target.is_file()
            entry: dict[str, Any] = {"path": rel, "existed": existed}
            if existed:
                # Mirror the relative path inside the snapshot dir.
                dest = snap_dir / "files" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, dest)
            manifest.append(entry)

        import json

        meta = {
            "id": snap_id,
            "root": str(self._require_root()),
            "created_at": time.time(),
            "entries": manifest,
        }
        (snap_dir / "manifest.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        return snap_id

    def restore_snapshot(self, snapshot_id: str) -> None:
        """Restore files captured by :meth:`snapshot`."""
        import json

        snap_dir = self._snapshots_root() / snapshot_id
        manifest_path = snap_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Snapshot not found: {snapshot_id}")
        meta = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in meta.get("entries", []):
            rel = entry["path"]
            target = self.resolve(rel)
            if entry.get("existed"):
                src = snap_dir / "files" / rel
                if src.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, target)
            else:
                # File did not exist at snapshot time -> remove if present.
                if target.exists() and target.is_file():
                    target.unlink()
        self.fileTreeChanged.emit()


__all__ = [
    "ProjectService",
    "SandboxError",
    "ProjectNotOpenError",
    "IGNORE_GLOBS",
    "DEFAULT_MAX_READ_BYTES",
]
