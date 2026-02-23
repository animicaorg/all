"""Quantum page — safe quantum status/credits/jobs UI and async controller."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.error_format import format_rpc_error, safe_json_dumps
from animica_studio.services.job_runner import JobHandle, JobRunner
from animica_studio.services.quantum_service import QuantumService
from animica_studio.services.workers import WorkerRunnable, run_in_threadpool
from animica_studio.storage.config import Config
from animica_studio.ui.widgets.stream_console import StreamConsole
from animica_studio.util.qt import safe_slot, ui_thread_only

log = logging.getLogger(__name__)


@dataclass
class _RpcAction:
    id: str
    fn: Callable[[], dict]


class QuantumJobController(QObject):
    """Owns asynchronous Quantum work and emits UI-safe lifecycle signals."""

    started = Signal(str)  # action_id
    output = Signal(str, str)  # kind, text
    success = Signal(object)  # payload
    failure = Signal(str, str)  # message, details
    finished = Signal()

    def __init__(self, service: QuantumService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._runner = JobRunner.instance()
        self._active_rpc: dict[str, WorkerRunnable] = {}
        self._active_cli: dict[str, JobHandle] = {}
        self._active_watch_job_id: str | None = None

    def has_active_action(self) -> bool:
        return bool(self._active_rpc or self._active_cli)

    def run_rpc(self, action: _RpcAction) -> None:
        self.started.emit(action.id)
        runnable = run_in_threadpool(action.fn)
        self._active_rpc[action.id] = runnable

        def _cleanup() -> None:
            self._active_rpc.pop(action.id, None)
            self.finished.emit()

        def _on_result(payload: object) -> None:
            if isinstance(payload, dict) and payload.get("ok") is False:
                self.failure.emit(str(payload.get("error", "Unknown error")), safe_json_dumps(payload, indent=2))
                return
            if isinstance(payload, dict) and "data" in payload and payload.get("ok") is True:
                self.success.emit(payload.get("data"))
                return
            self.success.emit(payload)

        runnable.signals.result.connect(_on_result)
        runnable.signals.error.connect(self.failure)
        runnable.signals.finished.connect(_cleanup)

    def run_watch(self, quantum_job_id: str) -> None:
        action_id = f"watch:{quantum_job_id}"
        self.started.emit(action_id)
        self._active_watch_job_id = None
        handle = self._runner.run_cli(["quantum", "jobs", "watch", quantum_job_id], timeout_s=300)
        self._active_cli[handle.job_id] = handle
        self._active_watch_job_id = handle.job_id

        handle.started.connect(lambda _: self.output.emit("system", f"Watching job {quantum_job_id}…"))
        handle.output.connect(lambda _id, kind, text: self.output.emit(kind, text))

        def _on_error(_id: str, message: str, details: str) -> None:
            self.failure.emit(message, details)

        def _on_done(job_id: str, exit_code: int, payload: object) -> None:
            self._active_cli.pop(job_id, None)
            if exit_code == 0:
                self.success.emit(payload)
            else:
                self.failure.emit(f"Watch exited with code {exit_code}", safe_json_dumps(payload, indent=2))
            self.finished.emit()

        handle.error.connect(_on_error)
        handle.finished.connect(_on_done)

    def cancel_active_watch(self) -> None:
        if self._active_watch_job_id:
            self._runner.cancel(self._active_watch_job_id)


class QuantumPage(QWidget):
    """Quantum computation job management with safe async execution."""

    def __init__(self, config: Config | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from animica_studio.storage.config import load_config  # noqa: PLC0415

        self._config = config or load_config()
        self._service = QuantumService(self._config)
        self._controller = QuantumJobController(self._service, self)
        self._busy = False
        self._active_action_id: str | None = None
        self._safe_mode = os.getenv("ANIMICA_STUDIO_SAFE_MODE", "").strip() == "1"

        self._action_buttons: dict[str, QPushButton] = {}
        self._diagnostics: list[str] = []

        self._build_ui()
        self._connect_controller_signals()
        self._apply_safe_mode()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("⚛️  Quantum")
        title.setObjectName("placeholderLabel")
        layout.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._build_status_tab(), "Status")
        tabs.addTab(self._build_credits_tab(), "Credits")
        tabs.addTab(self._build_jobs_tab(), "Jobs")
        tabs.addTab(self._build_diagnostics_tab(), "Diagnostics")
        layout.addWidget(tabs, stretch=1)

    def _build_status_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        btn = QPushButton("🔄  Refresh Status")
        self._action_buttons["refresh_status"] = btn
        self._status_output = QTextEdit()
        self._status_output.setReadOnly(True)
        btn.clicked.connect(self._on_refresh_status_clicked)
        layout.addWidget(btn)
        layout.addWidget(self._status_output, stretch=1)
        return w

    def _build_credits_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        form = QFormLayout()
        self._credits_addr = QLineEdit()
        self._credits_addr.setPlaceholderText("0x… or bech32 address")
        form.addRow("Address:", self._credits_addr)
        layout.addLayout(form)

        btn = QPushButton("Fetch Credits")
        self._action_buttons["fetch_credits"] = btn
        btn.clicked.connect(self._on_fetch_credits_clicked)
        layout.addWidget(btn)

        self._credits_output = QTextEdit()
        self._credits_output.setReadOnly(True)
        layout.addWidget(self._credits_output, stretch=1)
        return w

    def _build_jobs_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        list_btn = QPushButton("📋  List Jobs")
        self._action_buttons["list_jobs"] = list_btn
        list_btn.clicked.connect(self._on_list_jobs_clicked)
        layout.addWidget(list_btn)

        self._jobs_output = QTextEdit()
        self._jobs_output.setReadOnly(True)
        self._jobs_output.setMaximumHeight(200)
        layout.addWidget(self._jobs_output)

        submit_group = QGroupBox("Submit Job")
        submit_layout = QFormLayout(submit_group)
        self._job_problem = QLineEdit()
        self._job_problem.setPlaceholderText('{"circuit": "…"}')
        submit_layout.addRow("Problem (JSON):", self._job_problem)

        self._job_budget = QSpinBox()
        self._job_budget.setRange(1, 1_000_000)
        self._job_budget.setValue(100)
        submit_layout.addRow("Budget (credits):", self._job_budget)

        self._job_qubits = QSpinBox()
        self._job_qubits.setRange(1, 64)
        self._job_qubits.setValue(4)
        submit_layout.addRow("Qubits:", self._job_qubits)

        self._job_shots = QSpinBox()
        self._job_shots.setRange(1, 100_000)
        self._job_shots.setValue(1024)
        submit_layout.addRow("Shots:", self._job_shots)

        submit_btn = QPushButton("🚀  Submit")
        self._action_buttons["submit_job"] = submit_btn
        submit_btn.clicked.connect(self._on_submit_job_clicked)
        submit_layout.addRow("", submit_btn)
        layout.addWidget(submit_group)

        watch_group = QGroupBox("Watch Job")
        watch_layout = QHBoxLayout(watch_group)
        self._watch_job_id = QLineEdit()
        self._watch_job_id.setPlaceholderText("Job ID")
        watch_btn = QPushButton("▶  Watch")
        self._action_buttons["watch_job"] = watch_btn
        watch_btn.clicked.connect(self._on_watch_job_clicked)
        self._stop_watch_btn = QPushButton("⏹ Stop")
        self._stop_watch_btn.clicked.connect(self._on_stop_watch_clicked)
        self._stop_watch_btn.setEnabled(False)
        watch_layout.addWidget(self._watch_job_id, 1)
        watch_layout.addWidget(watch_btn)
        watch_layout.addWidget(self._stop_watch_btn)
        layout.addWidget(watch_group)

        self._jobs_console = StreamConsole()
        layout.addWidget(self._jobs_console, stretch=1)
        return w

    def _build_diagnostics_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self._diag_text = QTextEdit()
        self._diag_text.setReadOnly(True)
        copy_btn = QPushButton("📋 Copy diagnostics")
        copy_btn.clicked.connect(self._copy_diagnostics)
        self._action_buttons["copy_diagnostics"] = copy_btn
        layout.addWidget(copy_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._diag_text, stretch=1)
        return w

    def _connect_controller_signals(self) -> None:
        self._controller.started.connect(self._on_action_started)
        self._controller.output.connect(self._on_action_output)
        self._controller.success.connect(self._on_action_success)
        self._controller.failure.connect(self._on_action_failure)
        self._controller.finished.connect(self._on_action_finished)

    def _profile_rpc_url(self) -> str:
        return self._config.get_active_profile().node.rpc_local_url

    def _log_click(self, button_name: str) -> None:
        self._append_diag(
            f"Quantum: {button_name} clicked | thread={threading.current_thread().name}"
            f"/{threading.get_ident()} | rpc_url={self._profile_rpc_url()}"
        )
        log.info(
            "Quantum: %s clicked (thread=%s/%s rpc_url=%s)",
            button_name,
            threading.current_thread().name,
            threading.get_ident(),
            self._profile_rpc_url(),
        )

    def _append_diag(self, line: str) -> None:
        self._diagnostics.append(line)
        self._diag_text.setPlainText("\n".join(self._diagnostics[-200:]))

    def _run_rpc_action(self, action_id: str, fn: Callable[[], dict], loading_message: str) -> None:
        if self._busy:
            self._append_diag(f"Busy guard ignored action={action_id}")
            return
        self._set_busy(True, action_id)
        self._jobs_console.append_system(loading_message)
        self._controller.run_rpc(_RpcAction(id=action_id, fn=fn))

    def _apply_safe_mode(self) -> None:
        if not self._safe_mode:
            return
        for name, btn in self._action_buttons.items():
            btn.setEnabled(name == "copy_diagnostics")
        self._append_diag("ANIMICA_STUDIO_SAFE_MODE=1 — actions disabled, diagnostics only.")

    @safe_slot(log)
    @ui_thread_only(log)
    def _on_refresh_status_clicked(self) -> None:
        self._log_click("refresh_status")
        if self._safe_mode:
            return
        self._status_output.setPlainText("Loading…")
        self._run_rpc_action("status", self._service.get_status, "Refreshing quantum status…")

    @safe_slot(log)
    @ui_thread_only(log)
    def _on_fetch_credits_clicked(self) -> None:
        self._log_click("fetch_credits")
        if self._safe_mode:
            return
        address = self._credits_addr.text().strip()
        if not address:
            self._credits_output.setPlainText("Enter an address first.")
            return
        self._credits_output.setPlainText("Loading…")
        self._run_rpc_action("credits", lambda: self._service.get_credits(address), "Fetching quantum credits…")

    @safe_slot(log)
    @ui_thread_only(log)
    def _on_list_jobs_clicked(self) -> None:
        self._log_click("list_jobs")
        if self._safe_mode:
            return
        self._jobs_output.setPlainText("Loading…")
        self._run_rpc_action("list_jobs", self._service.list_jobs, "Listing quantum jobs…")

    @safe_slot(log)
    @ui_thread_only(log)
    def _on_submit_job_clicked(self) -> None:
        self._log_click("submit_job")
        if self._safe_mode:
            return
        problem_text = self._job_problem.text().strip()
        try:
            problem_spec = json.loads(problem_text) if problem_text else {}
        except json.JSONDecodeError as exc:
            self._jobs_output.setPlainText(f"Invalid JSON: {exc}")
            return

        budget = self._job_budget.value()
        qubits = self._job_qubits.value()
        shots = self._job_shots.value()
        self._jobs_output.setPlainText("Submitting…")

        self._run_rpc_action(
            "submit_job",
            lambda: self._service.submit_job(problem_spec, budget, qubits=qubits, shots=shots),
            "Submitting quantum job…",
        )

    @safe_slot(log)
    @ui_thread_only(log)
    def _on_watch_job_clicked(self) -> None:
        self._log_click("watch_job")
        if self._safe_mode:
            return
        if self._busy:
            return
        quantum_job_id = self._watch_job_id.text().strip()
        if not quantum_job_id:
            self._jobs_console.append_system("Enter a Job ID first.")
            return
        self._set_busy(True, "watch")
        self._stop_watch_btn.setEnabled(True)
        self._controller.run_watch(quantum_job_id)

    @safe_slot(log)
    @ui_thread_only(log)
    def _on_stop_watch_clicked(self) -> None:
        self._controller.cancel_active_watch()
        self._jobs_console.append_system("Stop requested…")

    @safe_slot(log)
    @ui_thread_only(log)
    def _copy_diagnostics(self) -> None:
        QGuiApplication.clipboard().setText(self._diag_text.toPlainText())

    def _set_busy(self, busy: bool, action_id: str | None = None) -> None:
        self._busy = busy
        self._active_action_id = action_id if busy else None
        if self._safe_mode:
            return
        for name, button in self._action_buttons.items():
            if name == "copy_diagnostics":
                continue
            button.setEnabled(not busy)
        self._stop_watch_btn.setEnabled(busy and action_id == "watch")

    @safe_slot(log)
    @ui_thread_only(log)
    def _on_action_started(self, action_id: str) -> None:
        self._append_diag(f"Action started: {action_id}")

    @safe_slot(log)
    @ui_thread_only(log)
    def _on_action_output(self, kind: str, text: str) -> None:
        self._jobs_console.append_line(kind, text)

    @safe_slot(log)
    @ui_thread_only(log)
    def _on_action_success(self, payload: Any) -> None:
        action = self._active_action_id or ""
        pretty = safe_json_dumps(payload, indent=2)
        if action == "status":
            self._status_output.setPlainText(pretty)
        elif action == "credits":
            self._credits_output.setPlainText(pretty)
        elif action == "list_jobs":
            self._jobs_output.setPlainText(pretty)
        elif action == "submit_job":
            self._jobs_output.setPlainText(f"✅ Job submitted!\n{pretty}")
        else:
            self._jobs_console.append_system("✅ Done.")
        self._append_diag(f"Action success: {action}")

    @safe_slot(log)
    @ui_thread_only(log)
    def _on_action_failure(self, message: str, details: str) -> None:
        action = self._active_action_id or ""
        formatted = format_rpc_error(message)
        if action == "status":
            self._status_output.setPlainText(f"Error: {formatted}")
        elif action == "credits":
            self._credits_output.setPlainText(f"Error: {formatted}")
        elif action in {"list_jobs", "submit_job"}:
            self._jobs_output.setPlainText(f"Error: {formatted}")
        self._jobs_console.append_system(f"❌ {formatted}")
        if details:
            self._jobs_console.append_line("stderr", details)
        self._append_diag(f"Action failure: {action} => {formatted}")

    @safe_slot(log)
    @ui_thread_only(log)
    def _on_action_finished(self) -> None:
        self._append_diag(f"Action finished: {self._active_action_id}")
        self._set_busy(False)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._controller.cancel_active_watch()
        super().closeEvent(event)
