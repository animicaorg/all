"""Git integration helpers for the IDE."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class GitStatus:
    available: bool
    repo_root: Optional[Path]
    branch: str
    upstream: Optional[str]
    dirty: bool
    message: str = ""


@dataclass(frozen=True)
class GitFileStatus:
    path: str
    staged: bool
    unstaged: bool
    status: str


@dataclass(frozen=True)
class GitCommandResult:
    ok: bool
    message: str
    stdout: str = ""
    stderr: str = ""


class GitRepo:
    """Convenience wrapper around git CLI for the IDE."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    @staticmethod
    def is_git_available() -> bool:
        return shutil.which("git") is not None

    @staticmethod
    def discover_repo_root(workspace: Path) -> Optional[Path]:
        if not workspace.exists():
            return None
        if not GitRepo.is_git_available():
            return None
        result = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        root = result.stdout.strip()
        return Path(root) if root else None

    def get_status(self) -> GitStatus:
        if not self.is_git_available():
            return GitStatus(False, None, "", None, False, "git is not installed")
        result = subprocess.run(
            ["git", "-C", str(self.repo_root), "status", "--porcelain=1"],
            capture_output=True,
            text=True,
            check=False,
        )
        dirty = result.returncode == 0 and bool(result.stdout.strip())
        branch = self._current_branch() or "detached"
        upstream = self._current_upstream()
        message = ""
        if result.returncode != 0:
            message = result.stderr.strip() or "Unable to read git status."
        return GitStatus(True, self.repo_root, branch, upstream, dirty, message)

    def list_files(self) -> list[GitFileStatus]:
        result = subprocess.run(
            ["git", "-C", str(self.repo_root), "status", "--porcelain=1", "-z"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return []
        entries = result.stdout.split("\x00")
        statuses: list[GitFileStatus] = []
        for entry in entries:
            if not entry:
                continue
            status = entry[:2]
            path = entry[3:]
            if "->" in path:
                path = path.split("->", 1)[-1].strip()
            staged = status[0] not in {" ", "?"}
            unstaged = status[1] != " "
            statuses.append(
                GitFileStatus(
                    path=path,
                    staged=staged,
                    unstaged=unstaged,
                    status=status.strip() or "??",
                )
            )
        return statuses

    def stage_files(self, paths: Iterable[str]) -> GitCommandResult:
        return self._run(["git", "-C", str(self.repo_root), "add", "--"] + list(paths))

    def stage_all(self) -> GitCommandResult:
        return self._run(["git", "-C", str(self.repo_root), "add", "-A"])

    def unstage_files(self, paths: Iterable[str]) -> GitCommandResult:
        result = self._run(["git", "-C", str(self.repo_root), "restore", "--staged", "--"] + list(paths))
        if result.ok:
            return result
        return self._run(["git", "-C", str(self.repo_root), "reset", "HEAD", "--"] + list(paths))

    def unstage_all(self) -> GitCommandResult:
        result = self._run(["git", "-C", str(self.repo_root), "restore", "--staged", "."])
        if result.ok:
            return result
        return self._run(["git", "-C", str(self.repo_root), "reset", "HEAD"])

    def commit(self, message: str) -> GitCommandResult:
        return self._run(["git", "-C", str(self.repo_root), "commit", "-m", message])

    def push(self, *, remote: Optional[str] = None, branch: Optional[str] = None) -> GitCommandResult:
        command = ["git", "-C", str(self.repo_root), "push"]
        if remote and branch:
            command.extend(["-u", remote, branch])
        return self._run(command)

    def list_remotes(self) -> list[str]:
        result = subprocess.run(
            ["git", "-C", str(self.repo_root), "remote"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return []
        return [r for r in result.stdout.splitlines() if r.strip()]

    def remote_url(self, remote: str) -> Optional[str]:
        result = subprocess.run(
            ["git", "-C", str(self.repo_root), "remote", "get-url", remote],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def _current_branch(self) -> Optional[str]:
        result = subprocess.run(
            ["git", "-C", str(self.repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def _current_upstream(self) -> Optional[str]:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(self.repo_root),
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{u}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        upstream = result.stdout.strip()
        return upstream or None

    def _run(self, command: list[str]) -> GitCommandResult:
        if not self.is_git_available():
            return GitCommandResult(False, "git is not installed")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        ok = result.returncode == 0
        message = result.stdout.strip() if ok else result.stderr.strip()
        if not message:
            message = "Command completed." if ok else "Command failed."
        return GitCommandResult(ok, message, stdout=result.stdout, stderr=result.stderr)


def build_pr_url(remote_url: str, branch: str) -> Optional[str]:
    if not remote_url:
        return None
    normalized = remote_url.strip()
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    github_match = _parse_host_repo(normalized, host="github.com")
    if github_match:
        org_repo = github_match
        return f"https://github.com/{org_repo}/compare/{branch}?expand=1"
    gitlab_match = _parse_host_repo(normalized, host="gitlab.com")
    if gitlab_match:
        org_repo = gitlab_match
        return f"https://gitlab.com/{org_repo}/-/merge_requests/new?merge_request[source_branch]={branch}"
    return None


def _parse_host_repo(remote_url: str, *, host: str) -> Optional[str]:
    ssh_pattern = re.compile(rf"git@{re.escape(host)}:(.+)")
    https_pattern = re.compile(rf"https?://{re.escape(host)}/(.+)")
    match = ssh_pattern.match(remote_url) or https_pattern.match(remote_url)
    if not match:
        return None
    return match.group(1)
