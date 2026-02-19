"""Thread-safe cancellation token."""

from __future__ import annotations

import threading


class CancelToken:
    """A lightweight, thread-safe cancellation flag.

    Usage::

        token = CancelToken()
        # in another thread:
        token.cancel()
        # in the running thread:
        if token.is_cancelled:
            ...
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Signal cancellation."""
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        """Return True if cancellation has been requested."""
        return self._event.is_set()

    def reset(self) -> None:
        """Clear the cancellation flag (useful for re-use in tests)."""
        self._event.clear()
