from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Callable, List, Optional

from . import Exit


class Result:
    def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
        self.exit_code = exit_code
        self.output = stdout + stderr


class CliRunner:
    def invoke(
        self,
        app: Callable[[Optional[List[str]]], Any],
        args: Optional[List[str]] = None,
    ) -> Result:
        stdout = io.StringIO()
        stderr = io.StringIO()
        exit_code = 0
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                app(args or [])
        except Exit as exc:  # pragma: no cover - control flow
            exit_code = exc.exit_code
        return Result(exit_code, stdout.getvalue(), stderr.getvalue())
