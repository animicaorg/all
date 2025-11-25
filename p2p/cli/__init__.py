"""
Animica P2P CLI
----------------
Convenient entry points for P2P operations.

Subcommands (dispatched lazily so this module works even if optional deps are missing):
  - peer      : Inspect & manage peers (connect/disconnect/ban/list).
  - listen    : Start a standalone P2P node bound to an existing DB.
  - publish   : Publish a test tx/share/block on a topic (dev tool).

Usage examples:
  python -c "from p2p.cli import main; main()" --help
  python -c "from p2p.cli import main; main()" peer --help
  python -c "from p2p.cli import main; main()" listen --db sqlite:///animica.db
  python -c "from p2p.cli import main; main()" publish --topic txs --hex 0xdeadbeef

(When p2p/cli/peer.py, listen.py, and publish.py are present, this module will
discover and dispatch to their `main(argv: list[str] | None = None) -> int` entrypoints.)
"""
from __future__ import annotations

import argparse
import importlib
import sys
from types import ModuleType
from typing import Callable, Dict, Optional

try:
    # Best-effort version banner
    from p2p.version import __version__ as _VERSION
except Exception:  # pragma: no cover - defensive
    _VERSION = "0.0.0+unknown"

# Map subcommand -> "module:function" path
_SUBCOMMANDS: Dict[str, str] = {
    "peer": "p2p.cli.peer:main",
    "listen": "p2p.cli.listen:main",
    "publish": "p2p.cli.publish:main",
}

__all__ = ["get_parser", "main", "_SUBCOMMANDS"]


def _resolve_entry(entry: str) -> Callable[[Optional[list[str]]], int]:
    """
    Resolve "module:function" into a callable that takes argv (list[str]|None) and returns int.
    Raises ImportError or AttributeError with a friendly message if missing.
    """
    if ":" not in entry:
        raise ValueError(f"Bad entry '{entry}', expected 'module:function'")
    mod_name, func_name = entry.split(":", 1)
    try:
        mod: ModuleType = importlib.import_module(mod_name)
    except ImportError as e:  # pragma: no cover - environment dependent
        raise ImportError(
            f"Subcommand is unavailable because module '{mod_name}' could not be imported: {e}"
        ) from e
    func = getattr(mod, func_name, None)
    if not callable(func):  # pragma: no cover - trivial
        raise AttributeError(
            f"Module '{mod_name}' does not export callable '{func_name}'."
        )
    return func  # type: ignore[return-value]


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="animica-p2p",
        description="Animica P2P utilities (peer management, node listener, publisher).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_VERSION}",
        help="Show version and exit.",
    )

    subparsers = parser.add_subparsers(dest="subcommand", metavar="<command>")

    # We don't import submodules here; just mirror their help minimally.
    # The real subcommand parsers are owned by each module; we pass through unknown args.
    subparsers.add_parser(
        "peer",
        help="Inspect/manage peers (list/connect/disconnect/ban).",
        add_help=False,
    )
    subparsers.add_parser(
        "listen",
        help="Start a standalone P2P node bound to a DB.",
        add_help=False,
    )
    subparsers.add_parser(
        "publish",
        help="Publish a test tx/share/block on a topic (dev tool).",
        add_help=False,
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """
    Parse argv and dispatch to the selected subcommand.
    Each subcommand owns its own argument parsing; we forward the remainder untouched.
    """
    if argv is None:
        argv = sys.argv[1:]
    parser = get_parser()

    # Peek subcommand to route without pre-consuming its args
    if not argv:
        parser.print_help()
        return 0

    sub = argv[0]
    if sub not in _SUBCOMMANDS:
        parser.print_help()
        sys.stderr.write(f"\nUnknown command: {sub!r}\n")
        return 2

    try:
        entry = _resolve_entry(_SUBCOMMANDS[sub])
    except (ImportError, AttributeError, ValueError) as e:
        sys.stderr.write(f"Error: {e}\n")
        return 2

    # Forward everything after the subcommand to the target module
    return int(entry(argv[1:]) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
