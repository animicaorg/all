"""Resolve the `animica` CLI for subprocess calls, safely under PyInstaller.

Several parts of the GUI shell out to the CLI with

    [sys.executable, "-m", "animica", ...]

which is correct from a source checkout and actively dangerous in a frozen
build. There, ``sys.executable`` is the GUI binary itself — PyInstaller's
bootloader ignores ``-m`` and hands the whole argv to the frozen entry script,
so the command launches *a second copy of the GUI*. That child sees the primary
instance already running and exits 0 without doing anything or printing
anything.

Exit code 0 with empty output is indistinguishable from success to a naive
caller, which is how the wallet's Send button came to report
"Transaction sent successfully!" for a transaction that was never built, let
alone broadcast.

This module centralises the resolution so every call site gets the same
answer, and returns None rather than a booby-trapped command when no usable
CLI exists.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resolve_module_cmd(module: str) -> list[str] | None:
    """Return an argv prefix that runs `module` as `__main__`, or None.

    From source this is ``[sys.executable, "-m", module]``. Frozen, the CLI
    packages are inside the bundle but there is no interpreter to call, so we
    re-enter the binary through its ``--run-module`` entry point (see
    animica_miner_gui.main). That runs the module in a fresh process without
    starting the GUI.
    """
    if not is_frozen():
        return [sys.executable, "-m", module]
    # sys.executable is the frozen binary here, which is exactly what we want.
    return [sys.executable, "--run-module", module]


def resolve_animica_cli() -> list[str] | None:
    """Return an argv prefix that really runs the `animica` CLI, or None.

    Resolution order:
      1. ``ANIMICA_CLI`` env override (an explicit path to the executable).
      2. An ``animica`` executable on PATH — preferred when present because it
         is the user's own, possibly newer, install.
      3. The bundled copy, via the frozen binary's ``--run-module`` re-entry
         (or ``-m`` from a source checkout).

    Never returns a command built from a bare ``sys.executable`` + ``-m`` under
    PyInstaller, which would relaunch the GUI and exit 0 having done nothing.
    """
    override = os.environ.get("ANIMICA_CLI")
    if override:
        candidate = Path(override)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate)]

    found = shutil.which("animica")
    if found:
        return [found]

    return resolve_module_cmd("animica.cli.main")


CLI_UNAVAILABLE_MESSAGE = (
    "The bundled app cannot run the `animica` command-line tool.\n\n"
    "Install it with:\n"
    "    pip install --upgrade animica\n\n"
    "or set ANIMICA_CLI to the full path of the `animica` executable, then "
    "restart Animica Miner."
)
