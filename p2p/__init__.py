"""
Animica P2P — package marker & lightweight public API.

- Exposes __version__ / git_describe
- Provides lazy re-exports for commonly used types to avoid heavy imports
  when the package is imported (PEP 562 __getattr__).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .version import __version__, git_describe

__all__ = [
    "__version__",
    "git_describe",
    # Lazy re-exports (see __getattr__)
    "P2PService",
    "HandshakeParams",
]

if TYPE_CHECKING:
    # Only for type-checkers; avoids import-time side effects at runtime.
    from .crypto.handshake import HandshakeParams
    from .node.service import P2PService


def __getattr__(name: str):
    """
    Lazy attribute loader for selected public symbols.
    This keeps top-level imports fast and side-effect free.
    """
    if name == "P2PService":
        from .node.service import P2PService  # type: ignore

        return P2PService
    if name == "HandshakeParams":
        from .crypto.handshake import HandshakeParams  # type: ignore

        return HandshakeParams
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_version() -> str:
    """Return the semantic version string."""
    return __version__
