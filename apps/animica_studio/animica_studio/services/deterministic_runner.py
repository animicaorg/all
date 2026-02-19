"""DeterministicRunner — placeholder for the Animica deterministic Python VM runner.

TODO: Replace the placeholder implementation with the real Animica VM runner
      once the VM integration is available.  The interface (run_script) is
      intentionally stable so the swap-in is a one-line change in IdePage.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Callable

from animica_studio.models.exec_models import ExecResult
from animica_studio.models.exec_models import StreamEvent
from animica_studio.services.cli_runner import CliRunner
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)

_PLACEHOLDER_NOTE = (
    "[Placeholder runner: compile-only check via `python -m py_compile`. "
    "No deterministic VM execution — swap in Animica VM runner here when ready.]"
)


@dataclass
class RunResult:
    """Result of a DeterministicRunner.run_script call."""
    script_path: str
    exit_code: int | None
    duration_ms: int
    cancelled: bool
    error: str | None
    output_lines: list[str]

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.cancelled and self.error is None


class DeterministicRunner:
    """Run a Python script in a sandboxed / restricted environment.

    Current implementation: compile-only (``python -m py_compile``).
    This avoids arbitrary code execution while still surfacing syntax errors.

    Extension point
    ---------------
    To swap in the real Animica VM runner::

        class DeterministicRunner:
            def run_script(self, script_path, args, env, on_line, cancel_token) -> RunResult:
                return animica_vm.run(script_path, args, env, on_line, cancel_token)
    """

    def run_script(
        self,
        script_path: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        on_line: Callable[[str], None] | None = None,
        cancel_token: CancelToken | None = None,
    ) -> RunResult:
        """Compile-check *script_path* using ``python -m py_compile``.

        Parameters
        ----------
        script_path: Path to the Python script.
        args: Unused in placeholder (reserved for real VM).
        env: Unused in placeholder (reserved for real VM).
        on_line: Callback called with each output line.
        cancel_token: Cancellation token.
        """
        log.info("DeterministicRunner: compile-check %s", script_path)

        if on_line:
            on_line(_PLACEHOLDER_NOTE)

        lines: list[str] = [_PLACEHOLDER_NOTE]
        runner = CliRunner()

        def _cb(ev: StreamEvent) -> None:
            text = ev.line
            lines.append(text)
            if on_line:
                on_line(text)

        result: ExecResult = runner.run(
            [sys.executable, "-m", "py_compile", script_path],
            timeout_s=30.0,
            cancel_token=cancel_token,
            stream_cb=_cb,
        )

        if result.returncode == 0 and on_line:
            msg = f"Syntax OK: {script_path}"
            lines.append(msg)
            on_line(msg)

        return RunResult(
            script_path=script_path,
            exit_code=result.returncode,
            duration_ms=result.duration_ms,
            cancelled=result.cancelled,
            error=result.error,
            output_lines=lines,
        )
