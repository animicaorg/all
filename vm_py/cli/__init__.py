"""
vm_py.cli
---------

Command-line entrypoints for the Animica Python VM tools.

This package groups small CLIs that are exposed by console scripts (typically):
  - `omni-vm-compile`  -> vm_py.cli.compile:main
  - `omni-vm-run`      -> vm_py.cli.run:main
  - `omni-vm-inspect`  -> vm_py.cli.inspect_ir:main

To avoid import-time overhead, we lazy-load CLI modules via `resolve_entrypoint`.
"""

from __future__ import annotations

from importlib import import_module
from typing import Callable, Dict

# Declarative mapping for consumers (e.g., packaging console_scripts or internal dispatch)
ENTRYPOINTS: Dict[str, str] = {
    "compile": "vm_py.cli.compile:main",
    "run": "vm_py.cli.run:main",
    "inspect": "vm_py.cli.inspect_ir:main",
}

def resolve_entrypoint(name: str) -> Callable[[], int]:
    """
    Resolve a CLI name to its `main()` callable without importing all submodules.

    Parameters
    ----------
    name : str
        One of the keys in ENTRYPOINTS ("compile", "run", "inspect").

    Returns
    -------
    Callable[[], int]
        A zero-arg callable returning an integer exit code.

    Raises
    ------
    KeyError
        If `name` is not a known entrypoint.
    AttributeError
        If the target module lacks a `main` attribute.
    ImportError
        If the target module cannot be imported.
    """
    target = ENTRYPOINTS[name]  # may raise KeyError (intentional)
    module_path, _, attr = target.partition(":")
    if not module_path or not attr:
        raise ImportError(f"Malformed entrypoint target: {target!r}")
    module = import_module(module_path)
    main_fn = getattr(module, attr)
    if not callable(main_fn):
        raise AttributeError(f"Entrypoint {target!r} is not callable")
    return main_fn  # type: ignore[return-value]

__all__ = ["ENTRYPOINTS", "resolve_entrypoint"]
