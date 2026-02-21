"""Reusable QThread worker skeleton.

Usage example::

    def my_task(x: int) -> int:
        return x * 2

    thread = WorkerThread(my_task, 21)
    thread.worker.result.connect(lambda v: print("result:", v))
    thread.worker.error.connect(lambda msg, tb: print("error:", msg))
    thread.start()
"""

from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

log = logging.getLogger(__name__)


class Worker(QObject):
    """Runs a callable on a background thread and emits lifecycle signals.

    Signals
    -------
    started:
        Emitted immediately before the callable is invoked.
    finished:
        Emitted when the callable returns (with or without error).
    result(object):
        Emitted with the return value on success.
    error(str, str):
        Emitted with ``(error_message, formatted_traceback)`` on exception.
    """

    started: Signal = Signal()
    finished: Signal = Signal()
    result: Signal = Signal(object)
    error: Signal = Signal(str, str)

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        """Execute the wrapped callable.  Connected to :pymeth:`QThread.started`."""
        self.started.emit()
        try:
            value = self._fn(*self._args, **self._kwargs)
            self.result.emit(value)
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            log.error("Worker error: %s\n%s", exc, tb)
            self.error.emit(str(exc), tb)
        finally:
            self.finished.emit()


class WorkerThread(QThread):
    """Convenience wrapper: creates a :class:`Worker`, moves it to *self*, and
    connects ``QThread.started`` → ``Worker.run``.

    Parameters
    ----------
    fn:
        The callable to run on the background thread.
    *args, **kwargs:
        Forwarded to *fn* at call time.
    """

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.worker = Worker(fn, *args, **kwargs)
        self.worker.moveToThread(self)
        self.started.connect(self.worker.run)
        self.worker.finished.connect(self.quit)
        _ACTIVE_THREADS.add(self)
        self.finished.connect(lambda: _ACTIVE_THREADS.discard(self))


_ACTIVE_THREADS: set[WorkerThread] = set()
