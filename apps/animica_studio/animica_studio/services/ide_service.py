"""IdeService — workspace selection and safe file operations for the IDE page."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB guard


def _safe_path(workspace: Path, rel_or_abs: str) -> Path | None:
    """Resolve *rel_or_abs* under *workspace*.

    Returns ``None`` if the resolved path escapes the workspace root.
    """
    target = (workspace / rel_or_abs).resolve()
    try:
        target.relative_to(workspace.resolve())
        return target
    except ValueError:
        return None


class IdeService:
    """Workspace management and safe file I/O for the IDE.

    All path arguments from the web UI must pass through :meth:`_check_path`
    before any filesystem operation.
    """

    def __init__(self, workspace_root: str | None = None) -> None:
        self._workspace: Path | None = Path(workspace_root).resolve() if workspace_root else None

    # -- Workspace -------------------------------------------------------------

    @property
    def workspace(self) -> Path | None:
        return self._workspace

    def set_workspace(self, path: str) -> None:
        p = Path(path).resolve()
        if not p.is_dir():
            raise ValueError(f"Not a directory: {path}")
        self._workspace = p
        log.info("IdeService: workspace set to %s", self._workspace)

    def _require_workspace(self) -> Path:
        if self._workspace is None:
            raise RuntimeError("No workspace selected")
        return self._workspace

    def _check_path(self, raw: str) -> Path:
        ws = self._require_workspace()
        p = _safe_path(ws, raw)
        if p is None:
            raise PermissionError(f"Path escapes workspace: {raw!r}")
        return p

    # -- Directory listing -----------------------------------------------------

    def list_dir(self, rel_path: str = ".") -> list[dict[str, Any]]:
        p = self._check_path(rel_path)
        if not p.exists():
            raise FileNotFoundError(f"Not found: {rel_path}")
        if not p.is_dir():
            raise NotADirectoryError(f"Not a directory: {rel_path}")

        entries = []
        try:
            for child in sorted(p.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
                entries.append({
                    "name": child.name,
                    "is_dir": child.is_dir(),
                    "size": child.stat().st_size if child.is_file() else 0,
                    "path": str(child.relative_to(self._workspace)),  # type: ignore[arg-type]
                })
        except OSError as exc:
            raise OSError(f"Cannot list directory: {exc}") from exc
        return entries

    # -- File read/write -------------------------------------------------------

    def read_file(self, rel_path: str) -> str:
        p = self._check_path(rel_path)
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {rel_path}")
        size = p.stat().st_size
        if size > _MAX_FILE_BYTES:
            raise ValueError(f"File too large to open in IDE ({size} bytes, max {_MAX_FILE_BYTES})")
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise OSError(f"Cannot read file: {exc}") from exc

    def write_file(self, rel_path: str, content: str) -> None:
        p = self._check_path(rel_path)
        encoded = content.encode("utf-8", errors="replace")
        if len(encoded) > _MAX_FILE_BYTES:
            raise ValueError(f"Content too large ({len(encoded)} bytes, max {_MAX_FILE_BYTES})")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        try:
            tmp.write_bytes(encoded)
            tmp.replace(p)
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise OSError(f"Cannot write file: {exc}") from exc
        log.debug("IdeService: wrote %s (%d bytes)", p, len(encoded))

    def create_file(self, rel_path: str) -> None:
        p = self._check_path(rel_path)
        if p.exists():
            raise FileExistsError(f"Already exists: {rel_path}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()

    def create_dir(self, rel_path: str) -> None:
        p = self._check_path(rel_path)
        if p.exists():
            raise FileExistsError(f"Already exists: {rel_path}")
        p.mkdir(parents=True)

    def rename_path(self, old_rel: str, new_rel: str) -> None:
        old = self._check_path(old_rel)
        new = self._check_path(new_rel)
        if not old.exists():
            raise FileNotFoundError(f"Not found: {old_rel}")
        if new.exists():
            raise FileExistsError(f"Destination already exists: {new_rel}")
        old.rename(new)

    def delete_path(self, rel_path: str) -> None:
        import shutil  # noqa: PLC0415
        p = self._check_path(rel_path)
        if not p.exists():
            raise FileNotFoundError(f"Not found: {rel_path}")
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        log.info("IdeService: deleted %s", p)
