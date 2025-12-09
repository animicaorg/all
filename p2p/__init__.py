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


# ---- Global service registry for RPC access ----

_global_service: "P2PService | None" = None  # type: ignore


def register_service(service: "P2PService") -> None:  # type: ignore
    """
    Register a global P2P service instance for access by RPC and other subsystems.
    
    This is called by the node startup code to make the P2P service accessible
    to the RPC layer for peer management endpoints.
    """
    global _global_service
    _global_service = service


def get_service() -> "P2PService | None":  # type: ignore
    """
    Get the globally registered P2P service instance, if any.
    
    Returns None if no service has been registered (e.g., P2P not started).
    """
    return _global_service


def get_connection_manager():
    """
    Get the ConnectionManager from the global P2P service, if available.
    
    Returns None if P2P service is not running.
    """
    svc = get_service()
    if svc is not None and hasattr(svc, "connmgr"):
        return svc.connmgr
    return None
