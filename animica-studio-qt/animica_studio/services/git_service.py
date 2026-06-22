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


# --------------------------------------------------------------------------- #
# Sidecar contract helpers (Increment 2-3): diff / commit / push by dest path.
# --------------------------------------------------------------------------- #
# These module-level functions match the agent-sidecar contract exactly:
#   diff(dest, path=None) -> str
#   commit(dest, message, paths=None) -> dict{commit}
#   push(dest, token=None, remote='origin', branch=None) -> dict
# They reuse the safe subprocess runner above; tokens are injected at call time
# only and scrubbed from any error text — never persisted or logged.

# Default commit identity used when the repo has none configured.
_DEFAULT_GIT_NAME = "Animica Studio"
_DEFAULT_GIT_EMAIL = "studio@animica.org"


def _scrub_token(text: str, token: Optional[str]) -> str:
    """Remove a token (and any ``x-access-token:...@`` netloc) from ``text``."""
    out = text or ""
    if token:
        out = out.replace(token, "***")
    out = re.sub(r"x-access-token:[^@\s]*@", "x-access-token:***@", out)
    out = re.sub(r"//[^/@\s:]+:[^/@\s]+@", "//***:***@", out)
    return out


def diff(dest: str | Path, path: Optional[str] = None) -> str:
    """Return the git diff for the repo at ``dest`` (whole-repo, or one path).

    Returns ``""`` when the tree is clean. Combines worktree (unstaged) and
    staged changes so a freshly-staged-but-uncommitted change is still visible.
    """
    svc = GitService(dest)
    if not svc.is_repo():
        return ""

    # Surface untracked files in the diff too (a brand-new file the agent wrote
    # should be reviewable before commit). We mark them intent-to-add (`add -N`)
    # so `git diff` renders their content, then undo the intent so nothing is
    # actually staged — the working tree is left exactly as we found it.
    intent_added: list[str] = []
    code_u, untracked_out, _ = svc._run(
        ["ls-files", "--others", "--exclude-standard"]
        + (["--", path] if path else [])
    )
    if code_u == 0 and untracked_out.strip():
        intent_added = [p for p in untracked_out.splitlines() if p.strip()]
        if intent_added:
            svc._run(["add", "-N", "--", *intent_added])

    try:
        args = ["diff", "HEAD"]
        if path:
            args.extend(["--", path])
        code, out, err = svc._run(args)
        if code != 0:
            # No commits yet (HEAD missing): fall back to plain diff + staged.
            plain = ["diff"]
            if path:
                plain.extend(["--", path])
            _, out, _ = svc._run(plain)
            staged = ["diff", "--cached"]
            if path:
                staged.extend(["--", path])
            _, staged_out, _ = svc._run(staged)
            out = (out or "") + (staged_out or "")
    finally:
        # Undo intent-to-add so we never leave files half-staged.
        for p in intent_added:
            svc._run(["reset", "--quiet", "--", p])
    return out or ""


def _ensure_identity(svc: "GitService") -> None:
    """Configure a default local commit identity if the repo has none."""
    code, name, _ = svc._run(["config", "user.name"])
    if code != 0 or not name.strip():
        svc._run(["config", "user.name", _DEFAULT_GIT_NAME])
    code, email, _ = svc._run(["config", "user.email"])
    if code != 0 or not email.strip():
        svc._run(["config", "user.email", _DEFAULT_GIT_EMAIL])


def commit(
    dest: str | Path, message: str, paths: Optional[list[str]] = None
) -> dict:
    """Stage ``paths`` (or everything) and commit at ``dest``.

    Returns ``{"ok": True, "commit": "<short-sha>"}`` on success, or
    ``{"error": "..."}`` when there is nothing to commit / the commit fails.
    """
    svc = GitService(dest)
    if not svc.is_repo():
        return {"error": "Not a git repository."}
    if not (message or "").strip():
        return {"error": "Commit message is required."}
    _ensure_identity(svc)
    if paths:
        ok, msg = svc.add(list(paths))
    else:
        ok, msg = svc.add(None)
    if not ok:
        return {"error": msg}
    code, out, err = svc._run(["commit", "-m", message])
    if code != 0:
        combined = (out + ("\n" + err if err else "")).strip()
        return {"error": combined or "nothing to commit"}
    rc, sha, _ = svc._run(["rev-parse", "--short", "HEAD"])
    commit_sha = sha.strip() if rc == 0 else ""
    return {"ok": True, "commit": commit_sha}


def push(
    dest: str | Path,
    token: Optional[str] = None,
    remote: str = "origin",
    branch: Optional[str] = None,
) -> dict:
    """Push the repo at ``dest`` to ``remote``.

    When ``token`` is given it is injected into the https remote URL ONLY for the
    duration of the push (via ``-c http.extraheader`` is avoided in favor of a
    one-shot URL on the command line so nothing lands in ``.git/config``). The
    token is never persisted or logged; any error text is scrubbed.

    Returns ``{"ok": True, "branch": "<branch>"}`` on success or
    ``{"error": "...", "scrubbed": True}`` on failure.
    """
    svc = GitService(dest)
    if not svc.is_repo():
        return {"error": "Not a git repository.", "scrubbed": True}
    target_branch = branch or svc.current_branch()
    if not target_branch:
        return {"error": "Could not determine the current branch.", "scrubbed": True}

    # Resolve the remote's configured (token-less) URL.
    rc, remote_url, _ = svc._run(["remote", "get-url", remote])
    remote_url = remote_url.strip()
    if rc != 0 or not remote_url:
        return {"error": f"Remote {remote!r} is not configured.", "scrubbed": True}

    if token:
        push_url = _inject_token(_normalize_https_url(remote_url), token)
        # Push to the explicit URL so the token never touches .git/config.
        args = ["push", push_url, f"HEAD:{target_branch}"]
    else:
        args = ["push", remote, f"HEAD:{target_branch}"]

    code, out, err = svc._run(args, timeout=_CLONE_TIMEOUT)
    if code != 0:
        combined = (err or out or "git push failed").strip()
        return {"error": _scrub_token(combined, token), "scrubbed": True}
    return {"ok": True, "branch": target_branch}


__all__ = ["GitService", "diff", "commit", "push"]
