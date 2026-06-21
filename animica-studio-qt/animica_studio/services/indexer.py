"""Lightweight project indexer for Animica Studio Qt.

The :class:`Indexer` walks an open project (respecting ignore globs), extracts a
cheap keyword/summary representation of each text file (language, size, a short
summary from the first non-trivial lines, and a handful of symbol names), and
persists :class:`~animica_studio.models.FileIndexEntry` rows via the database.

No embeddings / heavy models — search is keyword/LIKE based (delegated to
:meth:`Database.search_file_index`). Binary and oversized files are skipped.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Callable, Optional

from ..db import Database
from ..models import FileIndexEntry, new_id, now_ts
from .project_service import ProjectService

logger = logging.getLogger(__name__)

# Map of file extensions to a language label.
_LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".sql": "sql",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".md": "markdown",
    ".rst": "rst",
    ".txt": "text",
    ".xml": "xml",
    ".vue": "vue",
    ".svelte": "svelte",
    ".dockerfile": "dockerfile",
    ".qss": "css",
}

# Symbol extraction patterns per broad language family.
_SYMBOL_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "python": (
        re.compile(r"^\s*def\s+([A-Za-z_]\w*)", re.M),
        re.compile(r"^\s*class\s+([A-Za-z_]\w*)", re.M),
    ),
    "javascript": (
        re.compile(r"\bfunction\s+([A-Za-z_$]\w*)", re.M),
        re.compile(r"\bclass\s+([A-Za-z_$]\w*)", re.M),
        re.compile(r"\bconst\s+([A-Za-z_$]\w*)\s*=", re.M),
    ),
    "typescript": (
        re.compile(r"\bfunction\s+([A-Za-z_$]\w*)", re.M),
        re.compile(r"\bclass\s+([A-Za-z_$]\w*)", re.M),
        re.compile(r"\binterface\s+([A-Za-z_$]\w*)", re.M),
        re.compile(r"\btype\s+([A-Za-z_$]\w*)\s*=", re.M),
    ),
    "rust": (
        re.compile(r"\bfn\s+([A-Za-z_]\w*)", re.M),
        re.compile(r"\b(struct|enum|trait)\s+([A-Za-z_]\w*)", re.M),
    ),
    "go": (
        re.compile(r"\bfunc\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)", re.M),
        re.compile(r"\btype\s+([A-Za-z_]\w*)", re.M),
    ),
}
# typescript shares js family.
_SYMBOL_PATTERNS["jsx"] = _SYMBOL_PATTERNS["javascript"]
_SYMBOL_PATTERNS["tsx"] = _SYMBOL_PATTERNS["typescript"]


class Indexer:
    """Builds and queries a keyword index of project files."""

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
    )

    # Skip files larger than this (likely data/binaries; cheap to read otherwise).
    MAX_INDEX_BYTES = 512_000
    # Bytes read for the summary/symbol pass.
    READ_BYTES = 64_000

    def __init__(
        self,
        project_service: ProjectService,
        db: Optional[Database] = None,
    ) -> None:
        self._ps = project_service
        self._db = db

    # ------------------------------------------------------------------ #
    # Language / binary detection
    # ------------------------------------------------------------------ #
    @staticmethod
    def detect_language(rel_path: str) -> str:
        """Return a language label inferred from the file name/extension."""
        name = Path(rel_path).name.lower()
        if name == "dockerfile":
            return "dockerfile"
        if name == "makefile":
            return "make"
        ext = Path(name).suffix
        return _LANG_BY_EXT.get(ext, "")

    @staticmethod
    def _is_binary(sample: bytes) -> bool:
        if b"\x00" in sample:
            return True
        if not sample:
            return False
        # Heuristic: high proportion of non-text bytes.
        text_chars = bytes(range(32, 127)) + b"\n\r\t\f\b"
        nontext = sum(1 for b in sample if b not in text_chars)
        return (nontext / len(sample)) > 0.30

    def _ignored(self, name: str) -> bool:
        return name in self.IGNORE_GLOBS

    # ------------------------------------------------------------------ #
    # Scan
    # ------------------------------------------------------------------ #
    def scan(
        self,
        project_id: str,
        progress: Optional[Callable[[int, int], None]] = None,
    ) -> int:
        """Walk the project root, (re)building file-index rows.

        Returns the number of files indexed. ``progress(done, total)`` is called
        periodically when supplied.
        """
        root = self._ps.root
        if root is None:
            raise RuntimeError("No project is open; cannot scan.")

        # Collect candidate files first (so we can report a total).
        candidates: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not self._ignored(d)]
            for fn in filenames:
                if self._ignored(fn):
                    continue
                candidates.append(Path(dirpath, fn))

        total = len(candidates)
        indexed = 0
        if self._db is not None:
            try:
                self._db.clear_file_index(project_id)
            except Exception as exc:  # pragma: no cover
                logger.warning("clear_file_index failed: %s", exc)

        for i, abs_path in enumerate(candidates, start=1):
            try:
                rel = abs_path.resolve().relative_to(root).as_posix()
            except (ValueError, OSError):
                continue
            entry = self._build_entry(project_id, abs_path, rel)
            if entry is not None and self._db is not None:
                try:
                    self._db.upsert_file_index(entry)
                    indexed += 1
                except Exception as exc:  # pragma: no cover
                    logger.warning("upsert_file_index failed for %s: %s", rel, exc)
            elif entry is not None:
                indexed += 1
            if progress is not None and (i % 25 == 0 or i == total):
                try:
                    progress(i, total)
                except Exception:  # pragma: no cover - progress is best-effort
                    pass
        return indexed

    def index_file(self, project_id: str, rel_path: str) -> Optional[FileIndexEntry]:
        """Index (or re-index) a single file by relative path."""
        abs_path = self._ps.resolve(rel_path)
        if not abs_path.exists() or not abs_path.is_file():
            return None
        rel = self._ps.relative(abs_path)
        entry = self._build_entry(project_id, abs_path, rel)
        if entry is not None and self._db is not None:
            try:
                self._db.upsert_file_index(entry)
            except Exception as exc:  # pragma: no cover
                logger.warning("upsert_file_index failed for %s: %s", rel, exc)
        return entry

    # ------------------------------------------------------------------ #
    # Entry construction
    # ------------------------------------------------------------------ #
    def _build_entry(
        self, project_id: str, abs_path: Path, rel: str
    ) -> Optional[FileIndexEntry]:
        try:
            stat = abs_path.stat()
        except OSError:
            return None
        size = stat.st_size
        language = self.detect_language(rel)

        excerpt = ""
        symbols: list[str] = []
        summary = ""
        if size <= self.MAX_INDEX_BYTES:
            try:
                with abs_path.open("rb") as fh:
                    raw = fh.read(self.READ_BYTES)
            except OSError:
                raw = b""
            if raw and not self._is_binary(raw[:2048]):
                text = raw.decode("utf-8", errors="replace")
                excerpt = text[:2000]
                summary = self._summarize_text(text, rel)
                symbols = self._extract_symbols(text, language or rel)
        else:
            summary = f"{language or 'binary/large'} file ({size} bytes)"

        return FileIndexEntry(
            id=new_id(),
            project_id=project_id,
            path=rel,
            language=language,
            size=size,
            mtime=stat.st_mtime,
            summary=summary,
            symbols=symbols,
            content_excerpt=excerpt,
            indexed_at=now_ts(),
        )

    @staticmethod
    def _summarize_text(text: str, rel: str) -> str:
        """Build a short summary from the first meaningful lines/docstring."""
        lines = text.splitlines()
        meaningful: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Skip shebangs and bare fences.
            if stripped.startswith("#!"):
                continue
            cleaned = stripped.lstrip("#/*-;> \t\"'")
            if cleaned:
                meaningful.append(cleaned)
            if len(meaningful) >= 3:
                break
        head = " ".join(meaningful)[:240]
        if head:
            return head
        return Path(rel).name

    def _extract_symbols(self, text: str, language_or_path: str) -> list[str]:
        lang = language_or_path
        if lang not in _SYMBOL_PATTERNS:
            lang = self.detect_language(language_or_path)
        patterns = _SYMBOL_PATTERNS.get(lang)
        if not patterns:
            return []
        found: list[str] = []
        seen: set[str] = set()
        for pat in patterns:
            for match in pat.finditer(text):
                name = match.group(match.lastindex or 1)
                if name and name not in seen:
                    seen.add(name)
                    found.append(name)
                if len(found) >= 40:
                    return found
        return found

    # ------------------------------------------------------------------ #
    # Query
    # ------------------------------------------------------------------ #
    def search(
        self, project_id: str, query: str, limit: int = 50
    ) -> list[FileIndexEntry]:
        """Keyword search the index (delegates to the database LIKE search)."""
        if self._db is None:
            return []
        q = (query or "").strip()
        if not q:
            return []
        return self._db.search_file_index(project_id, q, limit=limit)

    def summarize(self, rel_path: str) -> str:
        """Produce a short per-file summary (reads the file directly)."""
        try:
            abs_path = self._ps.resolve(rel_path)
        except Exception:
            return ""
        if not abs_path.exists() or not abs_path.is_file():
            return ""
        try:
            with abs_path.open("rb") as fh:
                raw = fh.read(self.READ_BYTES)
        except OSError:
            return ""
        if not raw or self._is_binary(raw[:2048]):
            return f"binary file ({abs_path.stat().st_size} bytes)"
        text = raw.decode("utf-8", errors="replace")
        return self._summarize_text(text, rel_path)


__all__ = ["Indexer"]
