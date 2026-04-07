"""Reusable background-worker helpers for Qt UI code.

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

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal

log = logging.getLogger(__name__)


def _ensure_non_ui_callable(fn: Callable[..., Any]) -> None:
    bound_self = getattr(fn, "__self__", None)
    if isinstance(bound_self, QObject):
        raise TypeError("Worker callable must not be a bound QObject/UI method")


def _safe_emit(signal: Signal, *args: Any) -> None:
    try:
        signal.emit(*args)
    except RuntimeError:
        log.debug("Skipped signal emit after Qt object deletion")


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
        _ensure_non_ui_callable(fn)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        """Execute the wrapped callable.  Connected to :pymeth:`QThread.started`."""
        _safe_emit(self.started)
        try:
            value = self._fn(*self._args, **self._kwargs)
            _safe_emit(self.result, value)
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            log.error("Worker error: %s\n%s", exc, tb)
            _safe_emit(self.error, str(exc), tb)
        finally:
            _safe_emit(self.finished)


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


class WorkerSignals(QObject):
    """Signals emitted by :class:`WorkerRunnable` tasks."""

    started: Signal = Signal()
    finished: Signal = Signal()
    result: Signal = Signal(object)
    error: Signal = Signal(str, str)


class WorkerRunnable(QRunnable):
    """QRunnable that executes a pure background callable and emits signals.

    The callable must only perform non-UI work. UI updates must be connected to
    :attr:`signals.result` / :attr:`signals.error`, which are delivered to UI
    slots on the main thread via queued connections.
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        _ensure_non_ui_callable(fn)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        _safe_emit(self.signals.started)
        try:
            value = self._fn(*self._args, **self._kwargs)
            _safe_emit(self.signals.result, value)
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            log.error("WorkerRunnable error: %s\n%s", exc, tb)
            _safe_emit(self.signals.error, str(exc), tb)
        finally:
            _safe_emit(self.signals.finished)


def run_in_threadpool(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> WorkerRunnable:
    """Run *fn* on Qt's global thread pool and return its runnable handle."""
    runnable = WorkerRunnable(fn, *args, **kwargs)
    QThreadPool.globalInstance().start(runnable)
    return runnable
