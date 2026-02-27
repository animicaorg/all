"""StreamConsole — reusable streaming output widget.

Features
--------
* Append-only QPlainTextEdit with configurable max lines.
* Filter / search in output.
* Copy / Clear / Save-to-file buttons.
* Status line: running / exit code / duration.
* Stop button wired to a CancelToken.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import Signal, QObject
from PySide6.QtGui import QTextCursor, QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)

_MAX_LINES = 5_000
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB soft cap


class _Forwarder(QObject):
    """Cross-thread signal forwarder."""
    line_received: Signal = Signal(str, str)


class StreamConsole(QWidget):
    """A reusable widget for streaming text output.

    Connect :attr:`append_line` (thread-safe via signal) to push lines
    from any thread.
    """

    stopped: Signal = Signal()  # emitted when Stop is clicked

    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self._cancel_token: "CancelToken | None" = None
        self._running = False
        self._start_ts: float = 0.0
        self._line_count = 0
        self._byte_count = 0

        self._fwd = _Forwarder(self)
        self._fwd.line_received.connect(self._do_append)

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # --- filter row ---
        filter_row = QHBoxLayout()
        filter_row.setSpacing(4)
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter output…")
        self._filter_edit.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._filter_edit, stretch=1)
        copy_btn = QPushButton("📋 Copy")
        copy_btn.setFlat(True)
        copy_btn.setToolTip("Copy all output to clipboard")
        copy_btn.clicked.connect(self._on_copy)
        filter_row.addWidget(copy_btn)
        clear_btn = QPushButton("🗑 Clear")
        clear_btn.setFlat(True)
        clear_btn.clicked.connect(self.clear)
        filter_row.addWidget(clear_btn)
        save_btn = QPushButton("💾 Save…")
        save_btn.setFlat(True)
        save_btn.clicked.connect(self._on_save)
        filter_row.addWidget(save_btn)
        layout.addLayout(filter_row)

        # --- output area ---
        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        mono = QFont("Courier New", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._output.setFont(mono)
        self._output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._output.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._output, stretch=1)

        # --- status row ---
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self._status_label = QLabel("Idle")
        self._status_label.setObjectName("consoleStatus")
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        self._stop_btn = QPushButton("⏹ Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        status_row.addWidget(self._stop_btn)
        layout.addLayout(status_row)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_cancel_token(self, token: "CancelToken | None") -> None:
        self._cancel_token = token

    def set_running(self, running: bool) -> None:
        self._running = running
        self._stop_btn.setEnabled(running)
        if running:
            self._start_ts = time.time()
            self._status_label.setText("⏳ Running…")
        else:
            self._update_status_idle()

    def set_exit_status(self, exit_code: "int | None", duration_ms: int, cancelled: bool = False) -> None:
        self._running = False
        self._stop_btn.setEnabled(False)
        if cancelled:
            self._status_label.setText(f"⛔ Cancelled | {duration_ms}ms")
        elif exit_code is None:
            self._status_label.setText(f"⚠️  No exit code | {duration_ms}ms")
        elif exit_code == 0:
            self._status_label.setText(f"✅ Exit 0 | {duration_ms}ms")
        else:
            self._status_label.setText(f"❌ Exit {exit_code} | {duration_ms}ms")

    def append_line(self, *args: str) -> None:
        """Thread-safe append. May be called from any thread.

        Supported signatures:
        * append_line(line)
        * append_line(kind, text)
        """
        if len(args) == 1:
            line = args[0]
            self._fwd.line_received.emit("", line)
            return
        if len(args) == 2:
            kind, text = args
            self._fwd.line_received.emit(kind, text)
            return
        raise TypeError("append_line expects 1 or 2 string arguments")

    def append_system(self, text: str) -> None:
        self.append_line("system", text)

    def append_stdout(self, text: str) -> None:
        self.append_line("stdout", text)

    def append_stderr(self, text: str) -> None:
        self.append_line("stderr", text)

    def append_error(self, text: str) -> None:
        self.append_line("error", text)

    def append_info(self, text: str) -> None:
        self.append_system(text)

    def append_warn(self, text: str) -> None:
        self.append_error(f"⚠️ {text}")

    def append_debug(self, text: str) -> None:
        self.append_line("debug", text)

    def clear(self) -> None:
        self._output.clear()
        self._line_count = 0
        self._byte_count = 0

    def get_text(self) -> str:
        return self._output.toPlainText()

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------

    def _do_append(self, kind: str, text: str) -> None:
        """Actual Qt-thread append; enforces max lines / bytes."""
        line = self._format_line(kind, text)
        if self._line_count >= _MAX_LINES or self._byte_count >= _MAX_BYTES:
            # Trim first half
            self._output.clear()
            self._output.appendPlainText("[... output truncated ...]")
            self._line_count = 1
            self._byte_count = len("[... output truncated ...]")

        self._line_count += 1
        self._byte_count += len(line) + 1
        self._output.appendPlainText(line)
        # Auto-scroll to bottom
        self._output.moveCursor(QTextCursor.MoveOperation.End)

    def _format_line(self, kind: str, text: str) -> str:
        if not kind:
            return text
        ts = time.strftime("%H:%M:%S")
        return f"[{ts}] [{kind}] {text}"

    def _on_filter_changed(self, text: str) -> None:
        # Highlight matching text (basic: just scroll to first match)
        if not text:
            return
        doc = self._output.document()
        cursor = doc.find(text)
        if not cursor.isNull():
            self._output.setTextCursor(cursor)

    def _on_copy(self) -> None:
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415
        QApplication.clipboard().setText(self._output.toPlainText())

    def _on_save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Output", "output.txt", "Text files (*.txt);;All files (*)"
        )
        if path:
            try:
                Path(path).write_text(self._output.toPlainText(), encoding="utf-8")
            except OSError as exc:
                log.warning("StreamConsole: save failed: %s", exc)

    def _on_stop(self) -> None:
        if self._cancel_token:
            self._cancel_token.cancel()
        self.stopped.emit()

    def _update_status_idle(self) -> None:
        if self._start_ts > 0:
            elapsed_ms = int((time.time() - self._start_ts) * 1000)
            self._status_label.setText(f"Idle | last run: {elapsed_ms}ms")
        else:
            self._status_label.setText("Idle")
