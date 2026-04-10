"""Developer-facing command line tools for Animica.

These CLIs are convenience wrappers intended for local development and
experimentation. They should not be treated as production-grade wallet or
operations tooling.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


def __getattr__(name: str) -> Any:
    if name in {"app", "main"}:
        module = import_module(".main", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["app", "main", "wallet", "node", "mining"]
