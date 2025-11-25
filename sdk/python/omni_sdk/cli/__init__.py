"""
omni_sdk.cli
============

Command-line interface for the Animica Python SDK.

This package provides a Typer-based CLI exposed via the console script
entrypoint (typically `omni-sdk`). To avoid importing Typer (and the full CLI)
on regular library imports, we lazy-load the CLI only when accessed or executed.

Quick usage
-----------
- From Python:
    >>> from omni_sdk.cli import main
    >>> main()  # runs the CLI

- From shell (installed as a console script):
    $ omni-sdk --help
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, List, Optional

# Re-export the SDK version for convenience
try:
    from ..version import __version__  # type: ignore
except Exception:  # pragma: no cover
    __version__ = "0.0.0"

__all__: List[str] = [
    "__version__",
    "main",
    "run",
    "app",      # Typer app (lazy)
]

_SUBMODULE = "omni_sdk.cli.main"
_EXPOSE = ("app", "main", "run")


def _load() -> Any:
    """
    Import and return the CLI submodule. Kept separate to avoid importing Typer
    unless the CLI is actually used.
    """
    return import_module(_SUBMODULE)


def __getattr__(name: str) -> Any:  # PEP 562 lazy attribute access
    if name in _EXPOSE:
        mod = _load()
        try:
            return getattr(mod, name)
        except AttributeError as e:  # pragma: no cover
            raise AttributeError(f"{_SUBMODULE} has no attribute {name!r}") from e
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> List[str]:
    base = list(globals().keys())
    try:
        mod = _load()
        pub = [n for n in getattr(mod, "__all__", []) or dir(mod) if not n.startswith("_")]
    except Exception:  # pragma: no cover
        pub = list(_EXPOSE)
    return sorted(set(base + pub + list(__all__)))


# Thin wrappers so users can call omni_sdk.cli.main() without importing Typer here.
def main(argv: Optional[list[str]] = None) -> int:
    """
    Execute the CLI `main` function.

    Parameters
    ----------
    argv : list[str] | None
        Optional argument vector (default: None → sys.argv is used)

    Returns
    -------
    int
        Process exit code.
    """
    mod = _load()
    return int(getattr(mod, "main")(argv))  # type: ignore[no-any-return]


def run(argv: Optional[list[str]] = None) -> int:
    """
    Alias for :func:`main` for ergonomic symmetry with other SDKs.
    """
    return main(argv)
