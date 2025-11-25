"""
templates.tests
================

Lightweight, zero-dependency helpers shared by template tests. These utilities
are intentionally minimal and standard-library only so they can be imported by
both unit tests and quick smoke scripts without additional tooling.

Provided helpers
----------------
- TESTS_DIR / REPO_ROOT / ASSETS_DIR: canonical paths for locating test data.
- temp_dir(): ephemeral directory context manager (auto-cleaned).
- temp_cwd(): temporarily change working directory for a block.
- write(): create parent dirs and write text/bytes atomically-ish.
- read_text(): convenience wrapper with UTF-8 default.
- read_json() / write_json(): stable-encoded JSON I/O (sorted keys).
- run(): subprocess runner with sane defaults (captures text, raises on fail).

Notes
-----
* These helpers avoid any pytest-specific APIs on purpose.
* All functions are annotated and safe to import in production scripts if needed.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Sequence

# Canonical paths
TESTS_DIR: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = TESTS_DIR.parent
ASSETS_DIR: Path = TESTS_DIR / "assets"


@contextmanager
def temp_dir(prefix: str = "animica-templates-") -> Iterator[Path]:
    """
    Create and yield a temporary directory, ensuring cleanup afterwards.

    Example
    -------
    >>> with temp_dir() as d:
    ...     (d / "foo.txt").write_text("ok", encoding="utf-8")
    """
    d = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield d
    finally:
        # Best-effort cleanup; ignore errors so failures don't mask test results
        shutil.rmtree(d, ignore_errors=True)


@contextmanager
def temp_cwd(path: Path) -> Iterator[None]:
    """
    Temporarily switch to *path* as current working directory.

    Example
    -------
    >>> with temp_dir() as d, temp_cwd(d):
    ...     Path("here.txt").write_text("hi", encoding="utf-8")
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def write(path: Path | str, data: str | bytes, mode: int = 0o644) -> Path:
    """
    Write *data* to *path*, creating parent directories as needed.

    - If *data* is str, it's written as UTF-8.
    - If *data* is bytes, it's written verbatim.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        with open(p, "wb") as f:
            f.write(data)
    else:
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(data)
    try:
        os.chmod(p, mode)
    except OSError:
        # Non-POSIX or restricted FS; ignore permission setting errors.
        pass
    return p


def read_text(path: Path | str, *, strip: bool = False) -> str:
    """
    Read UTF-8 text from *path*. If *strip* is True, a trailing newline is stripped.
    """
    text = Path(path).read_text(encoding="utf-8")
    return text.rstrip("\n") if strip else text


def read_json(path: Path | str) -> Any:
    """
    Read JSON from *path* using UTF-8.
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path | str, obj: Any) -> Path:
    """
    Write JSON to *path* with stable formatting:
      - indent=2
      - ensure_ascii=False (keep unicode)
      - sort_keys=True (deterministic)
      - trailing newline for POSIX friendliness
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Use StringIO to ensure we control newline behavior consistently.
    buf = io.StringIO()
    json.dump(obj, buf, indent=2, ensure_ascii=False, sort_keys=True)
    buf.write("\n")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(buf.getvalue())
    return p


def dedent(text: str) -> str:
    """
    textwrap.dedent + leading newline trim for inline docstrings / templates.
    """
    return textwrap.dedent(text).lstrip("\n")


def run(
    cmd: Sequence[str] | str,
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    capture: bool = True,
    shell: bool = False,
) -> subprocess.CompletedProcess[str]:
    """
    Run a subprocess with sensible defaults for tests.

    Parameters
    ----------
    cmd : list[str] | str
        Command (list form preferred). Set shell=True if you pass a string with pipes.
    cwd : Path | str | None
        Working directory (optional).
    env : Mapping[str, str] | None
        Extra environment variables to overlay on os.environ.
    check : bool
        If True, raise CalledProcessError on non-zero exit.
    capture : bool
        If True, capture stdout/stderr as text.
    shell : bool
        If True, use shell execution (be cautious).

    Returns
    -------
    subprocess.CompletedProcess[str]
    """
    # Merge environments without mutating the process env
    proc_env = dict(os.environ)
    if env:
        proc_env.update(env)

    return subprocess.run(
        cmd,  # type: ignore[arg-type]
        cwd=str(cwd) if cwd is not None else None,
        env=proc_env,
        check=check,
        capture_output=capture,
        text=True,
        shell=shell,
    )


__all__ = [
    "TESTS_DIR",
    "REPO_ROOT",
    "ASSETS_DIR",
    "temp_dir",
    "temp_cwd",
    "write",
    "read_text",
    "read_json",
    "write_json",
    "dedent",
    "run",
]
