"""Git integration for Animica Studio Qt — **subprocess only**.

GitPython is intentionally *not* used (and not installed). All operations shell
out to the ``git`` binary via :func:`subprocess.run` with ``cwd`` pinned to the
project root and output captured. Mutating operations return ``(ok, message)``.

The service degrades gracefully when ``git`` is missing or the directory is not
a repository.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Bound for any single git invocation.
_GIT_TIMEOUT = 60.0


class GitService:
    """Thin, safe wrapper around the ``git`` CLI (never ``import git``)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------------ #
    # Low-level runner
    # ------------------------------------------------------------------ #
    @staticmethod
    def _git_available() -> bool:
        return shutil.which("git") is not None

    def _run(
        self, args: list[str], *, timeout: float = _GIT_TIMEOUT
    ) -> tuple[int, str, str]:
        """Run ``git <args>`` in the project root.

        Returns ``(returncode, stdout, stderr)``. A missing git binary or other
        OS error yields ``(-1, "", message)``.
        """
        if not self._git_available():
            return (-1, "", "git executable not found on PATH")
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return (proc.returncode, proc.stdout or "", proc.stderr or "")
        except subprocess.TimeoutExpired:
            return (-1, "", f"git {' '.join(args)} timed out after {timeout}s")
        except OSError as exc:  # pragma: no cover - rare
            logger.warning("git invocation failed: %s", exc)
            return (-1, "", str(exc))

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def is_repo(self) -> bool:
        """Return True if the root is inside a git work tree."""
        if not self.root.exists():
            return False
        code, out, _ = self._run(["rev-parse", "--is-inside-work-tree"])
        return code == 0 and out.strip() == "true"

    def status(self) -> str:
        """Return a porcelain status summary (empty string when clean)."""
        if not self.is_repo():
            return "Not a git repository."
        code, out, err = self._run(["status", "--porcelain=v1", "--branch"])
        if code != 0:
            return err.strip() or "git status failed."
        return out.rstrip("\n")

    def status_entries(self) -> list[dict[str, str]]:
        """Return parsed porcelain entries.

        Each entry: ``{"path", "index", "worktree"}`` where ``index`` and
        ``worktree`` are the two porcelain status characters.
        """
        if not self.is_repo():
            return []
        code, out, _ = self._run(["status", "--porcelain=v1"])
        if code != 0:
            return []
        entries: list[dict[str, str]] = []
        for line in out.splitlines():
            if not line:
                continue
            # Format: XY <path>  (or XY <orig> -> <path> for renames)
            xy = line[:2]
            rest = line[3:] if len(line) > 3 else ""
            index_st = xy[0]
            worktree_st = xy[1] if len(xy) > 1 else " "
            path = rest
            if " -> " in rest:
                path = rest.split(" -> ", 1)[1]
            entries.append(
                {
                    "path": path.strip(),
                    "index": index_st,
                    "worktree": worktree_st,
                }
            )
        return entries

    def diff(self, rel_path: Optional[str] = None, staged: bool = False) -> str:
        """Return a unified diff for the tree or a single relative path."""
        if not self.is_repo():
            return "Not a git repository."
        args = ["diff"]
        if staged:
            args.append("--cached")
        if rel_path:
            args.extend(["--", rel_path])
        code, out, err = self._run(args)
        if code != 0:
            return err.strip() or "git diff failed."
        return out

    def current_branch(self) -> Optional[str]:
        """Return the current branch name, or ``None`` if unavailable/detached."""
        if not self.is_repo():
            return None
        code, out, _ = self._run(["rev-parse", "--abbrev-ref", "HEAD"])
        if code != 0:
            return None
        name = out.strip()
        if not name or name == "HEAD":
            return None
        return name

    def log(self, limit: int = 20) -> str:
        """Return a short one-line log of the most recent commits."""
        if not self.is_repo():
            return "Not a git repository."
        code, out, err = self._run(
            ["log", f"-n{max(1, int(limit))}", "--oneline", "--decorate"]
        )
        if code != 0:
            return err.strip() or ""
        return out.rstrip("\n")

    # ------------------------------------------------------------------ #
    # Mutations
    # ------------------------------------------------------------------ #
    def init(self) -> tuple[bool, str]:
        """Initialize a new git repository at the root."""
        if not self.root.exists():
            return (False, f"Directory does not exist: {self.root}")
        if self.is_repo():
            return (True, "Already a git repository.")
        code, out, err = self._run(["init"])
        if code != 0:
            return (False, err.strip() or "git init failed.")
        return (True, (out or "Initialized empty Git repository.").strip())

    def add(self, paths: Optional[list[str]] = None) -> tuple[bool, str]:
        """Stage ``paths`` (or everything when ``None``)."""
        if not self.is_repo():
            return (False, "Not a git repository.")
        args = ["add"]
        if paths:
            args.extend(["--", *paths])
        else:
            args.append("-A")
        code, _, err = self._run(args)
        if code != 0:
            return (False, err.strip() or "git add failed.")
        return (True, "Staged changes.")

    def commit(self, message: str, add_all: bool = True) -> tuple[bool, str]:
        """Create a commit. When ``add_all`` is set, stage everything first."""
        if not self.is_repo():
            return (False, "Not a git repository.")
        if not (message or "").strip():
            return (False, "Commit message is required.")
        if add_all:
            ok, msg = self.add(None)
            if not ok:
                return (False, msg)
        code, out, err = self._run(["commit", "-m", message])
        combined = (out + ("\n" + err if err else "")).strip()
        if code != 0:
            # "nothing to commit" is a common, non-fatal outcome.
            return (False, combined or "git commit failed.")
        return (True, combined or "Committed.")


__all__ = ["GitService"]
