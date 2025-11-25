"""
{{ project_slug }} — Indexer Lite
=================================

Package marker & tiny utilities shared across the indexer modules.

What this template aims to provide
----------------------------------
- A *minimal* but production-friendly layout for an Animica indexer:
  - Pull historical blocks/transactions via HTTP (backfill).
  - Follow the head via WebSocket subscriptions (live tail).
  - Parse, normalize, and persist a compact subset of data to SQLite (or any DB
    your project wires later).
- Clear separation between *framework glue* and *business mapping* so you can
  swap storage or add new projections without touching the ingestion loop.

Conventions
-----------
- Environment is loaded from ``.env`` by the entrypoints (see your template's
  ``.env.example``). Common keys you’ll see used by other modules:
    RPC_URL          — Node RPC base URL (e.g., http://localhost:8545)
    WS_URL           — Node WebSocket URL (e.g., ws://localhost:8546/ws)
    CHAIN_ID         — Integer chain id (matches node/network)
    DB_PATH          — SQLite file path (e.g., indexer.db) or
    DATABASE_URL     — SQLAlchemy-style URL if you upgrade to a different DB
    METRICS_PORT     — Optional Prometheus metrics port
    WS_SUBSCRIBE     — "true"/"false" to enable live tail in certain commands
- Logging is opt-in basicConfig from here *only if* the root logger has no
  handlers. Your application may (and should) set up structured logging early.

This file keeps imports intentionally light and side-effect free so that simply
importing :mod:`indexer` will not pull heavy dependencies or touch I/O.
"""

from __future__ import annotations

import logging
from typing import Final

# --------------------------------------------------------------------------- #
# Version helpers
# --------------------------------------------------------------------------- #

try:  # Python 3.8+ stdlib
    from importlib.metadata import PackageNotFoundError, version  # type: ignore
except Exception:  # pragma: no cover - extremely defensive, older environments
    class PackageNotFoundError(Exception):
        pass

    def version(_: str) -> str:  # type: ignore
        return "0.0.0"


#: Distribution name (PEP 566). If you rename the project in pyproject,
#: consider templating this value as well.
DIST_NAME: Final[str] = "{{ project_slug }}"

#: Fallback version when metadata is unavailable (editable installs, zipapps, etc.)
__version__: Final[str] = "0.1.0"


def get_version() -> str:
    """
    Return the installed distribution version if available, otherwise the local
    fallback ``__version__``. Safe to call in any environment.

    Examples
    --------
    >>> isinstance(get_version(), str)
    True
    """
    try:
        return version(DIST_NAME)
    except PackageNotFoundError:
        return __version__
    except Exception:
        # Be extra conservative; never raise during import.
        return __version__


# --------------------------------------------------------------------------- #
# Logging helpers
# --------------------------------------------------------------------------- #

_DEFAULT_LOG_FORMAT: Final[str] = (
    "%(asctime)s %(levelname)s %(name)s — %(message)s"
)


def get_logger(name: str = "indexer") -> logging.Logger:
    """
    Return a namespaced logger. If the root logger has no handlers, install a
    simple, sensible default configuration. This avoids surprising double logs
    when applications or test harnesses already configured logging.

    Parameters
    ----------
    name:
        Logger name. Defaults to ``"indexer"`` so you can call ``get_logger()``
        without arguments in most modules.

    Returns
    -------
    logging.Logger
        A configured (or inherited) logger.

    Examples
    --------
    >>> log = get_logger()
    >>> isinstance(log, logging.Logger)
    True
    """
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format=_DEFAULT_LOG_FORMAT)
    return logging.getLogger(name)


# Public surface
__all__ = [
    "__version__",
    "get_version",
    "get_logger",
]
