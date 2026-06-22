"""Git integration for Animica Studio Qt — **subprocess only**.

GitPython is intentionally *not* used (and not installed). All operations shell
out to the ``git`` binary via :func:`subprocess.run` with ``cwd`` pinned to the
project root and output captured. Mutating operations return ``(ok, message)``.

The service degrades gracefully when ``git`` is missing or the directory is not
a repository.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

# Bound for any single git invocation.
_GIT_TIMEOUT = 60.0
# Clone can take longer than a local op.
_CLONE_TIMEOUT = 600.0


def _normalize_https_url(url: str) -> str:
    """Return a token-less, normalized https remote URL.

    Strips any embedded ``user:pass@`` / ``x-access-token:...@`` credentials so
    a token never lands in ``.git/config`` or logs. Leaves non-https URLs
    (ssh, git://) untouched.
    """
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunparse((parsed.scheme, host, parsed.path, "", "", ""))


def _inject_token(url: str, token: str) -> str:
    """Embed ``token`` into an https github URL for a one-shot clone.

    Produces ``https://x-access-token:<token>@host/path``. Returns the URL
    unchanged for non-https schemes.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    netloc = f"x-access-token:{token}@{host}"
    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))


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

    # ------------------------------------------------------------------ #
    # Clone (token-safe) + structured status
    # ------------------------------------------------------------------ #
    @classmethod
    def clone(
        cls,
        url: str,
        dest: str | Path,
        *,
        token: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> "GitService":
        """Clone ``url`` into ``dest`` and return a :class:`GitService` for it.

        The ``dest`` directory is wiped first if it already contains anything
        (supports the "wipe + reclone" requirement). When ``token`` is given it
        is injected into the clone URL ONLY for the duration of the clone; the
        ``origin`` remote is then rewritten to the token-less https URL so the
        token never persists in ``.git/config``. The token is never logged.
        """
        if not cls._git_available():
            raise RuntimeError("git executable not found on PATH")
        if not url or not isinstance(url, str):
            raise ValueError("clone url is required")

        dest_path = Path(dest)
        # Wipe an existing directory's contents (including a prior repo).
        if dest_path.exists():
            for child in dest_path.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try:
                        child.unlink()
                    except OSError:
                        pass
        dest_path.mkdir(parents=True, exist_ok=True)

        clone_url = _inject_token(url, token) if token else url
        args = ["clone"]
        if branch:
            args.extend(["--branch", branch, "--single-branch"])
        args.extend([clone_url, str(dest_path)])

        try:
            proc = subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                timeout=_CLONE_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("git clone timed out") from exc
        except OSError as exc:  # pragma: no cover - rare
            raise RuntimeError(f"git clone failed: {exc}") from exc

        if proc.returncode != 0:
            # Scrub any token that might appear in git's error output.
            err = (proc.stderr or proc.stdout or "git clone failed").strip()
            if token:
                err = err.replace(token, "***")
            err = re.sub(r"x-access-token:[^@]*@", "x-access-token:***@", err)
            raise RuntimeError(err)

        svc = cls(dest_path)
        # Rewrite origin to the token-less https URL (no-op if no token).
        clean_url = _normalize_https_url(url)
        svc._run(["remote", "set-url", "origin", clean_url])
        return svc

    def status_dict(self) -> dict:
        """Return a structured status dict per CONTRACT 1.

        Shape: ``{"branch", "ahead", "behind", "files":[{"path","status"}]}``.
        ``status`` is a single porcelain code (``M``/``A``/``D``/``?`` etc.).
        """
        if not self.is_repo():
            return {"branch": None, "ahead": 0, "behind": 0, "files": []}

        code, out, _ = self._run(["status", "--porcelain=v1", "--branch"])
        if code != 0:
            return {"branch": self.current_branch(), "ahead": 0, "behind": 0, "files": []}

        branch: Optional[str] = self.current_branch()
        ahead = 0
        behind = 0
        files: list[dict[str, str]] = []

        for line in out.splitlines():
            if not line:
                continue
            if line.startswith("##"):
                header = line[2:].strip()
                # e.g. "main...origin/main [ahead 1, behind 2]"
                name_part = header.split("...", 1)[0].split(" ", 1)[0].strip()
                if name_part and name_part != "HEAD" and "(no branch)" not in header:
                    branch = name_part
                m_ahead = re.search(r"ahead (\d+)", header)
                m_behind = re.search(r"behind (\d+)", header)
                if m_ahead:
                    ahead = int(m_ahead.group(1))
                if m_behind:
                    behind = int(m_behind.group(1))
                continue
            xy = line[:2]
            rest = line[3:] if len(line) > 3 else ""
            path = rest
            if " -> " in rest:
                path = rest.split(" -> ", 1)[1]
            # Collapse the two porcelain chars into one meaningful code.
            index_st = xy[0]
            worktree_st = xy[1] if len(xy) > 1 else " "
            if index_st == "?" or worktree_st == "?":
                st = "?"
            elif index_st != " ":
                st = index_st
            else:
                st = worktree_st
            files.append({"path": path.strip(), "status": st})

        return {"branch": branch, "ahead": ahead, "behind": behind, "files": files}


__all__ = ["GitService"]
