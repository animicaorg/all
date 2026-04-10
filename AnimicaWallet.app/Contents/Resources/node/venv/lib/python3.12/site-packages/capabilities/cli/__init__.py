"""
capabilities.cli
================

Unified CLI entrypoint for the Animica *capabilities* subsystem.

Usage (once other CLI modules are present):

    python -m capabilities.cli --help
    python -m capabilities.cli enqueue-ai --help
    python -m capabilities.cli enqueue-quantum --help
    python -m capabilities.cli list-jobs --help
    python -m capabilities.cli inject-result --help

This package auto-discovers and registers subcommands from sibling modules.
Each sub-module should expose a `register(app: typer.Typer) -> None` function
that attaches its commands to the provided Typer application.

Design goals:
- Zero hard dependency on CLI submodules (safe when some are missing).
- Friendly fallback if Typer is not installed.
- Can be embedded in larger apps by calling `get_app()`.

Environment:
- CAP_CLI_MODULES (optional): comma-separated list of extra modules to import
  that also expose `register(app)`.
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import List

log = logging.getLogger(__name__)

# Attempt to surface a version string if available
try:  # lazy/optional
    from capabilities.version import version as _cap_version  # type: ignore

    __version__ = _cap_version()
except Exception:  # pragma: no cover - optional
    __version__ = "0.0.0"

# Default subcommand providers (all optional)
_DEFAULT_MODULES: List[str] = [
    "capabilities.cli.enqueue_ai",
    "capabilities.cli.enqueue_quantum",
    "capabilities.cli.list_jobs",
    "capabilities.cli.inject_result",
]


def _iter_cli_modules() -> List[str]:
    mods = list(_DEFAULT_MODULES)
    extra = os.getenv("CAP_CLI_MODULES", "").strip()
    if extra:
        mods.extend([m.strip() for m in extra.split(",") if m.strip()])
    return mods


def _register_from_module(modname: str, app: "typer.Typer") -> None:
    try:
        mod = importlib.import_module(modname)
    except Exception as e:  # pragma: no cover - environment dependent
        log.debug("capabilities.cli: skip %s (%s)", modname, e)
        return

    register = getattr(mod, "register", None)
    if callable(register):
        register(app)
        log.debug("capabilities.cli: registered from %s", modname)
    else:
        # Optional: allow modules to export a Typer app as `app` and a `COMMAND_NAME`
        sub_app = getattr(mod, "app", None)
        name = getattr(mod, "COMMAND_NAME", None)
        if sub_app is not None and name:
            try:
                app.add_typer(sub_app, name=name)
                log.debug("capabilities.cli: mounted %s as '%s'", modname, name)
            except Exception as e:  # pragma: no cover
                log.debug("capabilities.cli: failed to mount %s: %s", modname, e)


def get_app():
    """
    Build and return the root Typer application for the capabilities CLI.

    Returns
    -------
    typer.Typer
        The configured Typer app. If Typer is not installed, raises ImportError
        with a clear message.
    """
    try:
        import typer  # type: ignore
    except Exception as e:  # pragma: no cover
        raise ImportError(
            "Typer is required for the capabilities CLI. "
            "Install with: pip install typer[all]"
        ) from e

    app = typer.Typer(
        add_completion=False,
        no_args_is_help=True,
        help="Animica Capabilities CLI — enqueue jobs, inspect queue/results, and admin tools.",
    )

    @app.callback(invoke_without_command=True)
    def _root_callback(
        version: bool = typer.Option(
            False, "--version", help="Print version and exit."
        ),
    ):
        if version:
            typer.echo(__version__)
            raise typer.Exit(0)

    # Dynamically register subcommands
    for modname in _iter_cli_modules():
        _register_from_module(modname, app)

    return app


def main() -> None:
    """
    Console entrypoint. Allows `python -m capabilities.cli`.
    """
    try:
        app = get_app()
    except ImportError as e:  # pragma: no cover
        print(str(e))
        raise SystemExit(2)
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
