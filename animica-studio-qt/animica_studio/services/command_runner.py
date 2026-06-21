"""Shell command execution with risk classification for Animica Studio Qt.

:class:`CommandRunner` streams a command's merged stdout/stderr via Qt signals
using :class:`QProcess` (non-blocking) and also offers a blocking variant for
agent tool loops. It *classifies* command risk (``is_risky`` / ``classify``)
but never decides policy: approval is the caller's responsibility. Destructive,
install, docker, deploy, wallet, network-pipe, and privilege-escalation commands
are flagged so the UI/agent can require explicit approval before running them.
"""
from __future__ import annotations

import logging
import re
import subprocess
from typing import Optional

from PySide6.QtCore import QObject, QProcess, Signal

logger = logging.getLogger(__name__)

# Risk categories, ordered roughly by severity. Stable strings — the agent and
# the database key approvals off these.
RISKY_CATEGORIES: tuple[str, ...] = (
    "destructive",   # rm -rf, del, format, mkfs, dd, truncate, > file
    "install",       # pip/npm/yarn/pnpm/cargo/apt/brew/go install
    "docker",        # docker/docker-compose/podman/kubectl
    "deploy",        # deploy/publish/push to prod, terraform apply, ssh
    "wallet",        # wallet/keygen/private key/seed/sign/transfer
    "network",       # curl|bash, wget|sh piping to a shell
    "privilege",     # sudo, su, chmod 777
)

# Compiled detection patterns per category. The first category that matches
# (in RISKY_CATEGORIES order) wins.
_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "destructive": (
        re.compile(r"\brm\s+(-\S+\s+)*-\S*[rf]", re.I),  # rm -rf / rm -fr / rm -r
        re.compile(r"\brm\s+.*\*", re.I),
        re.compile(r"\b(del|erase)\s+/", re.I),
        re.compile(r"\b(rmdir|rd)\s+/s", re.I),
        re.compile(r"\bmkfs\b", re.I),
        re.compile(r"\bformat\b", re.I),
        re.compile(r"\bdd\s+if=", re.I),
        re.compile(r"\btruncate\b", re.I),
        re.compile(r"\bshred\b", re.I),
        re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|push\s+.*--force)", re.I),
        re.compile(r"(^|\s)>\s*\S", re.M),  # output redirection clobber
        re.compile(r":\s*\(\)\s*\{.*\}\s*;", re.I),  # fork bomb-ish
    ),
    "install": (
        re.compile(r"\bpip3?\s+install\b", re.I),
        re.compile(r"\b(npm|pnpm|yarn)\s+(install|add|i)\b", re.I),
        re.compile(r"\bcargo\s+(install|add)\b", re.I),
        re.compile(r"\bgo\s+(install|get)\b", re.I),
        re.compile(r"\b(apt|apt-get|yum|dnf|pacman|apk)\s+(install|add)\b", re.I),
        re.compile(r"\bbrew\s+install\b", re.I),
        re.compile(r"\bpoetry\s+(add|install)\b", re.I),
        re.compile(r"\bgem\s+install\b", re.I),
        re.compile(r"\buv\s+(add|pip\s+install)\b", re.I),
    ),
    "docker": (
        re.compile(r"\bdocker(-compose)?\b", re.I),
        re.compile(r"\bpodman\b", re.I),
        re.compile(r"\bkubectl\b", re.I),
        re.compile(r"\bhelm\b", re.I),
        re.compile(r"\bnerdctl\b", re.I),
    ),
    "deploy": (
        re.compile(r"\bdeploy\b", re.I),
        re.compile(r"\bpublish\b", re.I),
        re.compile(r"\bterraform\s+(apply|destroy)\b", re.I),
        re.compile(r"\bssh\b", re.I),
        re.compile(r"\bscp\b", re.I),
        re.compile(r"\brsync\b", re.I),
        re.compile(r"\b(vercel|netlify|fly|heroku|gcloud|aws|az)\s+\w", re.I),
        re.compile(r"\bgit\s+push\b.*\b(prod|production|main|master)\b", re.I),
        re.compile(r"\bansible(-playbook)?\b", re.I),
    ),
    "wallet": (
        re.compile(r"\bwallet\b", re.I),
        re.compile(r"\bkeygen\b", re.I),
        re.compile(r"\bprivate[_-]?key\b", re.I),
        re.compile(r"\bseed[_-]?phrase\b", re.I),
        re.compile(r"\bmnemonic\b", re.I),
        re.compile(r"\b(sign|transfer|send)\b.*\b(tx|transaction|coin|token|anm|sol)\b", re.I),
        re.compile(r"\banimica\s+(wallet|buy|otc|transfer)\b", re.I),
    ),
    "network": (
        re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba)?sh\b", re.I),
        re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?python", re.I),
        re.compile(r"\bcurl\b.*\|\s*\w+\s+install", re.I),
    ),
    "privilege": (
        re.compile(r"(^|\s)sudo\s", re.I),
        re.compile(r"(^|\s)su\s+-", re.I),
        re.compile(r"\bchmod\s+(-R\s+)?0?777\b", re.I),
        re.compile(r"\bchown\s+-R\b", re.I),
        re.compile(r"\bsetcap\b", re.I),
    ),
}


class CommandRunner(QObject):
    """Streams a command via QProcess; classifies risk statically."""

    outputReceived = Signal(str)   # streamed merged stdout/stderr chunk
    started = Signal(str)          # command string
    finished = Signal(int)         # exit code
    errorOccurred = Signal(str)

    def __init__(self, cwd: Optional[str] = None) -> None:
        super().__init__()
        self._cwd: Optional[str] = cwd
        self._proc: Optional[QProcess] = None
        self._last_cmd: str = ""

    # ------------------------------------------------------------------ #
    # Configuration
    # ------------------------------------------------------------------ #
    def set_cwd(self, cwd: str) -> None:
        self._cwd = cwd

    @property
    def cwd(self) -> Optional[str]:
        return self._cwd

    # ------------------------------------------------------------------ #
    # Risk classification (static)
    # ------------------------------------------------------------------ #
    @staticmethod
    def classify(cmd: str) -> Optional[str]:
        """Return the risk category for ``cmd`` or ``None`` if it looks safe."""
        text = (cmd or "").strip()
        if not text:
            return None
        for category in RISKY_CATEGORIES:
            for pat in _PATTERNS[category]:
                if pat.search(text):
                    return category
        return None

    @staticmethod
    def is_risky(cmd: str) -> bool:
        """Return True when ``cmd`` matches any risky category."""
        return CommandRunner.classify(cmd) is not None

    # Backwards/alternate name used in some specs.
    @staticmethod
    def risk_category(cmd: str) -> Optional[str]:
        return CommandRunner.classify(cmd)

    # ------------------------------------------------------------------ #
    # Async execution via QProcess
    # ------------------------------------------------------------------ #
    def run(
        self,
        cmd: str,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Start ``cmd`` asynchronously, streaming output via signals.

        Approval is NOT enforced here — the caller must gate risky commands.
        """
        if self.is_running():
            self.errorOccurred.emit("A command is already running.")
            return

        self._last_cmd = cmd
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        workdir = cwd or self._cwd
        if workdir:
            proc.setWorkingDirectory(workdir)

        proc.readyReadStandardOutput.connect(self._on_ready_read)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc

        program, arguments = self._shell_invocation(cmd)
        self.started.emit(cmd)
        proc.start(program, arguments)

        if timeout is not None and timeout > 0:
            # QProcess timeout: kill after timeout seconds without blocking.
            from PySide6.QtCore import QTimer

            QTimer.singleShot(int(timeout * 1000), self._on_timeout)

    def _on_timeout(self) -> None:
        if self.is_running():
            self.errorOccurred.emit("Command timed out; terminating.")
            self.stop()

    @staticmethod
    def _shell_invocation(cmd: str) -> tuple[str, list[str]]:
        """Return the (program, args) to run ``cmd`` through a shell."""
        import os

        if os.name == "nt":
            return ("cmd.exe", ["/c", cmd])
        return ("/bin/sh", ["-c", cmd])

    def _on_ready_read(self) -> None:
        if self._proc is None:
            return
        data = self._proc.readAllStandardOutput().data()
        if data:
            try:
                text = bytes(data).decode("utf-8", errors="replace")
            except Exception:  # pragma: no cover
                text = repr(data)
            self.outputReceived.emit(text)

    def _on_finished(self, exit_code: int, _status: object = None) -> None:
        # Drain any remaining buffered output.
        self._on_ready_read()
        self.finished.emit(int(exit_code))
        self._proc = None

    def _on_error(self, _err: object) -> None:
        if self._proc is None:
            return
        msg = self._proc.errorString() if hasattr(self._proc, "errorString") else "process error"
        self.errorOccurred.emit(str(msg))

    def is_running(self) -> bool:
        return (
            self._proc is not None
            and self._proc.state() != QProcess.ProcessState.NotRunning
        )

    def stop(self) -> None:
        """Terminate the running command (graceful then forced)."""
        proc = self._proc
        if proc is None:
            return
        try:
            proc.terminate()
            if not proc.waitForFinished(2000):
                proc.kill()
                proc.waitForFinished(1000)
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to stop process: %s", exc)
        finally:
            self._proc = None

    # ------------------------------------------------------------------ #
    # Blocking execution (for agent tool loop / tests)
    # ------------------------------------------------------------------ #
    def run_blocking(
        self,
        cmd: str,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> tuple[int, str]:
        """Run ``cmd`` synchronously; return ``(exit_code, merged_output)``.

        Approval is NOT enforced here — the caller must gate risky commands.
        """
        workdir = cwd or self._cwd
        program, arguments = self._shell_invocation(cmd)
        try:
            proc = subprocess.run(
                [program, *arguments],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return (124, f"Command timed out after {timeout}s")
        except OSError as exc:  # pragma: no cover
            return (127, str(exc))
        output = (proc.stdout or "") + (proc.stderr or "")
        return (int(proc.returncode), output)


__all__ = ["CommandRunner", "RISKY_CATEGORIES"]
