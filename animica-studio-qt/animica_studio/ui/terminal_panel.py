"""Terminal / Problems / Logs bottom panel for Animica Studio Qt.

:class:`TerminalPanel` wraps a :class:`~animica_studio.services.command_runner.CommandRunner`
and provides:

* a read-only, live-streaming output view (objectName ``Terminal``),
* a command input line with Run / Stop controls,
* tabs for **Terminal**, **Problems** and **Logs**,
* highlighting of non-zero exit codes,
* routing of *risky* commands through an approval flow. Because approval is the
  caller's responsibility (per CONTRACTS), this panel emits
  :pyattr:`approvalRequested(command, category, callback)` and only runs once the
  callback is invoked with ``True``. The ``MainWindow`` connects to this signal
  and shows the Approve / Deny / Always-allow dialog.

The panel never blocks the GUI thread: it uses ``CommandRunner.run`` (QProcess)
for execution.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import redact
from ..services.command_runner import CommandRunner

logger = logging.getLogger(__name__)


class _OutputView(QPlainTextEdit):
    """A read-only, monospaced output view that appends streamed text."""

    def __init__(self, object_name: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setProperty("class", "code")
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setMaximumBlockCount(20000)  # bound memory on noisy commands

    def append_text(self, text: str) -> None:
        if not text:
            return
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def append_line(self, text: str) -> None:
        self.append_text(text if text.endswith("\n") else text + "\n")


class TerminalPanel(QWidget):
    """Bottom panel: terminal output + command input, plus Problems/Logs tabs."""

    #: Emitted when a *risky* command needs approval before running.
    #: Args: (command, category, callback). The callback must be called with a
    #: bool — ``True`` approves and runs the command, ``False`` denies it.
    approvalRequested = Signal(str, str, object)

    #: Emitted whenever a command finishes (command, exit_code).
    commandFinished = Signal(str, int)

    def __init__(
        self,
        command_runner: Optional[CommandRunner] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("BottomPanel")
        self._runner = command_runner or CommandRunner()
        self._current_cmd: str = ""
        self._build_ui()
        self._wire_runner()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        self._tabs = QTabWidget(self)
        self._terminal_view = _OutputView("Terminal")
        self._problems_view = _OutputView("Terminal")
        self._logs_view = _OutputView("Terminal")
        self._tabs.addTab(self._terminal_view, "Terminal")
        self._tabs.addTab(self._problems_view, "Problems")
        self._tabs.addTab(self._logs_view, "Logs")
        root.addWidget(self._tabs, 1)

        controls = QHBoxLayout()
        controls.setSpacing(6)

        self._input = QLineEdit(self)
        self._input.setObjectName("CommandInput")
        self._input.setPlaceholderText("Enter a shell command and press Run (or Enter)…")
        self._input.returnPressed.connect(self._on_run_clicked)
        controls.addWidget(self._input, 1)

        self._run_btn = QPushButton("Run", self)
        self._run_btn.setProperty("class", "primary")
        self._run_btn.setProperty("variant", "primary")
        self._run_btn.clicked.connect(self._on_run_clicked)
        controls.addWidget(self._run_btn)

        self._stop_btn = QPushButton("Stop", self)
        self._stop_btn.setProperty("class", "danger")
        self._stop_btn.setProperty("variant", "danger")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self.stop)
        controls.addWidget(self._stop_btn)

        self._clear_btn = QPushButton("Clear", self)
        self._clear_btn.setProperty("class", "ghost")
        self._clear_btn.setProperty("variant", "ghost")
        self._clear_btn.clicked.connect(self.clear)
        controls.addWidget(self._clear_btn)

        root.addLayout(controls)

    def _wire_runner(self) -> None:
        self._runner.outputReceived.connect(self._on_output)
        self._runner.started.connect(self._on_started)
        self._runner.finished.connect(self._on_finished)
        self._runner.errorOccurred.connect(self._on_error)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def set_command_runner(self, runner: CommandRunner) -> None:
        """Swap the underlying command runner (e.g. when a project opens)."""
        try:
            self._runner.outputReceived.disconnect(self._on_output)
            self._runner.started.disconnect(self._on_started)
            self._runner.finished.disconnect(self._on_finished)
            self._runner.errorOccurred.disconnect(self._on_error)
        except (RuntimeError, TypeError):
            pass
        self._runner = runner
        self._wire_runner()

    def set_cwd(self, cwd: str) -> None:
        self._runner.set_cwd(cwd)

    def run(self, cmd: str) -> None:
        """Run ``cmd``, routing risky commands through the approval flow."""
        cmd = (cmd or "").strip()
        if not cmd:
            return
        if self._runner.is_running():
            self.log("A command is already running; please wait or Stop it.")
            return

        category = CommandRunner.classify(cmd)
        if category:
            self.log(f"Command flagged as '{category}'; awaiting approval: {cmd}")

            def _callback(allowed: bool, _cmd: str = cmd) -> None:
                if allowed:
                    self._execute(_cmd)
                else:
                    self.log(f"Denied: {_cmd}")
                    self._terminal_view.append_line(f"[denied] {_cmd}")

            # If anyone is connected, defer to them; otherwise refuse (no policy here).
            if self._has_approver():
                self.approvalRequested.emit(cmd, category, _callback)
            else:
                # No approver connected — be safe and deny by default.
                self.log(f"No approver connected; refusing risky command: {cmd}")
                self._terminal_view.append_line(f"[blocked, no approver] {cmd}")
            return

        self._execute(cmd)

    def _has_approver(self) -> bool:
        """Return True when something is connected to ``approvalRequested``."""
        try:
            meta = self.metaObject()
            index = meta.indexOfSignal("approvalRequested(QString,QString,QVariant)")
            if index < 0:
                # Fall back to a name-only lookup across overloads.
                for i in range(meta.methodCount()):
                    if bytes(meta.method(i).name()) == b"approvalRequested":
                        index = i
                        break
            if index < 0:
                return False
            return self.isSignalConnected(meta.method(index))
        except Exception:  # pragma: no cover - defensive
            return False

    def log(self, text: str) -> None:
        """Append a redacted line to the Logs tab."""
        self._logs_view.append_line(redact(text))

    def report_problem(self, text: str) -> None:
        """Append a line to the Problems tab and surface the tab."""
        self._problems_view.append_line(text)

    def clear(self) -> None:
        """Clear the currently active output tab."""
        widget = self._tabs.currentWidget()
        if isinstance(widget, QPlainTextEdit):
            widget.clear()

    def stop(self) -> None:
        self._runner.stop()
        self._stop_btn.setEnabled(False)
        self._run_btn.setEnabled(True)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _execute(self, cmd: str) -> None:
        self._current_cmd = cmd
        self._tabs.setCurrentWidget(self._terminal_view)
        self._terminal_view.append_line(f"$ {cmd}")
        self.log(f"run: {cmd}")
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        try:
            self._runner.run(cmd)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Failed to start command")
            self._terminal_view.append_line(f"[error] {exc}")
            self._run_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)

    def _on_run_clicked(self) -> None:
        cmd = self._input.text().strip()
        if cmd:
            self._input.clear()
            self.run(cmd)

    # -- runner signal handlers ---------------------------------------- #
    def _on_output(self, chunk: str) -> None:
        self._terminal_view.append_text(chunk)

    def _on_started(self, cmd: str) -> None:
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

    def _on_finished(self, code: int) -> None:
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        if code == 0:
            self._terminal_view.append_line(f"[exit 0] {self._current_cmd}")
            self.log(f"exit 0: {self._current_cmd}")
        else:
            line = f"[exit {code}] {self._current_cmd}"
            self._terminal_view.append_line(line)
            self.report_problem(line)
            self.log(f"non-zero exit {code}: {self._current_cmd}")
        self.commandFinished.emit(self._current_cmd, int(code))

    def _on_error(self, message: str) -> None:
        msg = redact(message)
        self._terminal_view.append_line(f"[runner error] {msg}")
        self.report_problem(f"runner error: {msg}")
        self.log(f"runner error: {msg}")
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)


__all__ = ["TerminalPanel"]
