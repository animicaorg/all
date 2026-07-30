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
answer, and never hands back a booby-trapped ``sys.executable -m`` command.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resolve_module_cmd(module: str) -> list[str]:
    """Return an argv prefix that runs `module` as `__main__`.

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


def resolve_animica_cli() -> list[str]:
    """Return an argv prefix that really runs the `animica` CLI.

    Resolution order:
      1. ``ANIMICA_CLI`` env override (an explicit path to the executable).
      2. When frozen: the **bundled** CLI, via ``--run-module``.
      3. Otherwise: an ``animica`` executable on PATH, else ``-m``.

    A frozen build deliberately prefers its own bundled CLI over whatever is on
    PATH. Two reasons: an unrelated `animica` install can be an entirely
    different version from the one this bundle was built and tested against,
    and on Windows ``shutil.which`` consults the current directory, so a file
    dropped next to the app could be executed instead of the real tool.

    Always returns a usable command — the bundled CLI is guaranteed present by
    the REQUIRED_MODULES gate in the PyInstaller spec — so callers do not need
    a "no CLI" branch.
    """
    override = os.environ.get("ANIMICA_CLI")
    if override:
        candidate = Path(override)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate)]

    if is_frozen():
        return resolve_module_cmd("animica.cli.main")

    found = shutil.which("animica")
    if found:
        return [found]

    return resolve_module_cmd("animica.cli.main")


#: Search paths that point inside the bundle and must not reach a foreign
#: child. The loader ones are set by PyInstaller's bootloader; the Qt ones are
#: set by this app (main._configure_qt_plugins) and by the PyInstaller runtime
#: hook — a child that happens to use Qt would otherwise load OUR plugins
#: against ITS Qt and abort with "could not load the platform plugin".
_BUNDLE_PATH_VARS = (
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH",
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
)


def child_env(overlay: dict[str, str] | None = None, *, same_binary: bool = False) -> dict[str, str]:
    """Build a child-process environment that is safe to hand to another program.

    A frozen process really does carry the bundle on its loader path — verified
    on a shipped build::

        $ tr '\\0' '\\n' < /proc/<pid>/environ | grep LD_LIBRARY_PATH
        LD_LIBRARY_PATH=/…/AnimicaMiner/_internal

    Inheriting that wholesale makes an unrelated child (an externally installed
    `animica`, xmrig, a system tool) resolve our bundled libstdc++/libssl/Qt
    ahead of its own, which crashes or subtly misbehaves. The old
    whitelist-style env avoided this by accident while breaking Windows; copying
    the environment fixes Windows but reintroduces this, so strip it explicitly.

    Only entries that point inside the bundle are removed — anything the user
    genuinely set is preserved. ``same_binary=True`` keeps them, for children
    that ARE this frozen binary (the ``--run-module`` re-entry), which need the
    bundle on the loader path.
    """
    env = os.environ.copy()

    meipass = getattr(sys, "_MEIPASS", None)
    if is_frozen() and meipass and not same_binary:
        bundle = os.path.realpath(meipass)
        bundle_prefix = bundle.rstrip(os.sep) + os.sep

        def _inside_bundle(part: str) -> bool:
            real = os.path.realpath(part)
            return real == bundle or real.startswith(bundle_prefix)

        for var in _BUNDLE_PATH_VARS:
            value = env.get(var)
            if not value:
                continue
            kept = [
                part
                for part in value.split(os.pathsep)
                if part and not _inside_bundle(part)
            ]
            if kept:
                env[var] = os.pathsep.join(kept)
            else:
                env.pop(var, None)

    if overlay:
        env.update(overlay)
    return env
