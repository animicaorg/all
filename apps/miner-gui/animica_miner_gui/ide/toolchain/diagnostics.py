"""Diagnostics helpers for compiler and validation errors."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class Diagnostic:
    message: str
    path: Optional[Path] = None
    line: Optional[int] = None
    column: Optional[int] = None
    severity: str = "error"

    def display_text(self) -> str:
        location = ""
        if self.path:
            location = str(self.path)
            if self.line:
                location += f":{self.line}"
                if self.column:
                    location += f":{self.column}"
            location += ": "
        return f"{location}{self.message}"


_LINE_COL_PATTERN = re.compile(r"^(?P<path>.+?):(?P<line>\d+):(?P<col>\d+):\s*(?P<msg>.+)$")
_LINE_PATTERN = re.compile(r"^(?P<path>.+?):(?P<line>\d+):\s*(?P<msg>.+)$")
_PY_FILE_PATTERN = re.compile(r"File \"(?P<path>.+?)\", line (?P<line>\d+)")


def parse_diagnostics(text: str, workspace: Optional[Path] = None) -> List[Diagnostic]:
    diagnostics: List[Diagnostic] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _LINE_COL_PATTERN.match(line)
        if match:
            diagnostics.append(
                Diagnostic(
                    message=match.group("msg"),
                    path=_resolve_path(match.group("path"), workspace),
                    line=int(match.group("line")),
                    column=int(match.group("col")),
                )
            )
            continue
        match = _LINE_PATTERN.match(line)
        if match:
            diagnostics.append(
                Diagnostic(
                    message=match.group("msg"),
                    path=_resolve_path(match.group("path"), workspace),
                    line=int(match.group("line")),
                )
            )
            continue
        match = _PY_FILE_PATTERN.search(line)
        if match:
            diagnostics.append(
                Diagnostic(
                    message="Python error",
                    path=_resolve_path(match.group("path"), workspace),
                    line=int(match.group("line")),
                )
            )
            continue
    return diagnostics


def _resolve_path(path_str: str, workspace: Optional[Path]) -> Optional[Path]:
    path = Path(path_str)
    if path.is_absolute():
        return path
    if workspace:
        candidate = workspace / path
        if candidate.exists():
            return candidate
    return path


def merge_diagnostics(*diagnostic_sets: Iterable[Diagnostic]) -> List[Diagnostic]:
    merged: List[Diagnostic] = []
    for diag_set in diagnostic_sets:
        merged.extend(list(diag_set))
    return merged
