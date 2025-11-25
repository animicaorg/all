"""
Animica proofs.cli
------------------
Command-line entrypoints for working with proof envelopes:

- verify : Verify any proof file (auto-detect type, print ψ inputs)
- build-ai : Assemble AIProof from TEE quote + trap receipts + output digest
- build-quantum : Assemble QuantumProof from provider cert + trap outcomes
- build-hashshare : Build & test a HashShare from header template + nonce
- nullifier : Compute nullifier for a given proof body

This package exposes a lazy Typer app so importing doesn't hard-require all
submodules to be present. Each command module should export either:
  * `app: typer.Typer`  (preferred), or
  * `main: Callable[..., Any]`       (single-command fallback).

Usage:
  python -m proofs.cli           # runs the app
  python -m proofs.cli verify -h # help for a subcommand
"""
from __future__ import annotations

import importlib
import os
import sys
from types import ModuleType
from typing import Any, Optional

try:
    import typer  # type: ignore
except Exception:  # pragma: no cover
    typer = None  # type: ignore

from ..version import __version__  # re-exported

__all__ = ["build_app", "__version__"]


def _load_module(mod_name: str) -> Optional[ModuleType]:
    try:
        return importlib.import_module(mod_name)
    except ModuleNotFoundError:
        return None
    except Exception as e:  # pragma: no cover
        # Surface unexpected import errors clearly during CLI bring-up.
        raise RuntimeError(f"Failed loading CLI module '{mod_name}': {e}") from e


def _attach_command(app: "typer.Typer", mod_name: str, name: str) -> None:
    mod = _load_module(mod_name)
    if mod is None:
        return
    # Preferred: submodule exposes `app: typer.Typer`
    subapp = getattr(mod, "app", None)
    if subapp is not None and hasattr(subapp, "callback"):
        app.add_typer(subapp, name=name)
        return
    # Fallback: expose a single function `main`
    main = getattr(mod, "main", None)
    if callable(main):
        app.command(name=name)(main)  # type: ignore[misc]
        return
    # Otherwise: nothing to attach (module may only provide library helpers)


def build_app() -> "typer.Typer | None":
    """
    Build and return the root Typer app. Returns None if Typer is unavailable.
    """
    if typer is None:  # pragma: no cover
        return None

    app = typer.Typer(
        name="animica-proofs",
        help="Proof tools: verify/build/nullifier for Animica proofs",
        no_args_is_help=True,
        add_completion=False,
    )

    @app.callback()
    def _meta(
        version: bool = typer.Option(  # type: ignore[name-defined]
            False, "--version", "-V", help="Print version and exit", is_eager=True
        ),
    ) -> None:
        if version:
            typer.echo(f"animica-proofs {__version__}")  # type: ignore[name-defined]
            raise typer.Exit(0)  # type: ignore[name-defined]

    # Attach first-party subcommands (lazy)
    _attach_command(app, "proofs.cli.proof_verify", "verify")
    _attach_command(app, "proofs.cli.proof_build_ai", "build-ai")
    _attach_command(app, "proofs.cli.proof_build_quantum", "build-quantum")
    _attach_command(app, "proofs.cli.proof_build_hashshare", "build-hashshare")
    _attach_command(app, "proofs.cli.proof_nullifier", "nullifier")

    # Optional: load extra commands from environment (comma-separated module paths)
    extras = os.getenv("ANIMICA_PROOFS_CLI_EXTRAS", "").strip()
    if extras:
        for spec in [s.strip() for s in extras.split(",") if s.strip()]:
            # name can be given as module[:name]
            if ":" in spec:
                mod_name, name = spec.split(":", 1)
            else:
                mod_name, name = spec, spec.rsplit(".", 1)[-1].replace("_", "-")
            _attach_command(app, mod_name, name)

    return app


def _print_typer_missing() -> None:  # pragma: no cover
    msg = (
        "Typer is required for the proofs CLI but is not installed.\n"
        "Install extras and retry:\n"
        "  pip install 'typer[all]' 'rich'  # optional for nicer help\n"
        "Or call the underlying modules directly, e.g.:\n"
        "  python -m proofs.cli.proof_verify --help\n"
    )
    sys.stderr.write(msg)


def main(argv: Optional[list[str]] = None) -> int:
    """
    Entrypoint used by `python -m proofs.cli`. Returns process exit code.
    """
    app = build_app()
    if app is None:
        _print_typer_missing()
        return 2
    # Typer handles argv internally; we keep signature for symmetry/tests.
    app()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
