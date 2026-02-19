"""Quantum page — quantum job status, credits, submit, and watch."""

from __future__ import annotations

import logging

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
from animica_studio.services.quantum_service import QuantumService
from animica_studio.services.workers import WorkerThread
from animica_studio.storage.config import Config
from animica_studio.ui.widgets.stream_console import StreamConsole
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)


class QuantumPage(QWidget):
    """Quantum computation job management."""

    def __init__(self, config: Config | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from animica_studio.storage.config import load_config  # noqa: PLC0415
        self._config = config or load_config()
        self._service = QuantumService(self._config)
        self._cancel_token = CancelToken()
        self._build_ui()

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
        layout.addWidget(tabs, stretch=1)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _build_status_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        btn = QPushButton("🔄  Refresh Status")
        self._status_output = QTextEdit()
        self._status_output.setReadOnly(True)
        btn.clicked.connect(self._refresh_status)
        layout.addWidget(btn)
        layout.addWidget(self._status_output, stretch=1)
        return w

    def _refresh_status(self) -> None:
        self._status_output.setPlainText("Loading…")
        service = self._service

        def _task():
            return service.get_status()

        def _done(result):
            if result.get("ok"):
                self._status_output.setPlainText(safe_json_dumps(result.get("data"), indent=2))
            else:
                self._status_output.setPlainText(f"Error: {format_rpc_error(result.get('error'))}")

        def _err(msg, _tb):
            self._status_output.setPlainText(f"Error: {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()

    # ------------------------------------------------------------------
    # Credits
    # ------------------------------------------------------------------

    def _build_credits_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        form = QFormLayout()
        self._credits_addr = QLineEdit()
        self._credits_addr.setPlaceholderText("0x… or bech32 address")
        form.addRow("Address:", self._credits_addr)
        layout.addLayout(form)
        btn = QPushButton("Fetch Credits")
        btn.clicked.connect(self._fetch_credits)
        layout.addWidget(btn)
        self._credits_output = QTextEdit()
        self._credits_output.setReadOnly(True)
        layout.addWidget(self._credits_output, stretch=1)
        return w

    def _fetch_credits(self) -> None:
        addr = self._credits_addr.text().strip()
        if not addr:
            self._credits_output.setPlainText("Enter an address first.")
            return
        self._credits_output.setPlainText("Loading…")
        service = self._service

        def _task():
            return service.get_credits(addr)

        def _done(result):
            if result.get("ok"):
                self._credits_output.setPlainText(safe_json_dumps(result.get("data"), indent=2))
            else:
                self._credits_output.setPlainText(f"Error: {format_rpc_error(result.get('error'))}")

        def _err(msg, _tb):
            self._credits_output.setPlainText(f"Error: {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    def _build_jobs_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # List jobs
        list_btn = QPushButton("📋  List Jobs")
        list_btn.clicked.connect(self._list_jobs)
        layout.addWidget(list_btn)

        self._jobs_output = QTextEdit()
        self._jobs_output.setReadOnly(True)
        self._jobs_output.setMaximumHeight(200)
        layout.addWidget(self._jobs_output)

        # Submit job
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
        submit_btn.clicked.connect(self._submit_job)
        submit_layout.addRow("", submit_btn)
        layout.addWidget(submit_group)

        # Watch stream
        watch_group = QGroupBox("Watch Job")
        watch_layout = QHBoxLayout(watch_group)
        self._watch_job_id = QLineEdit()
        self._watch_job_id.setPlaceholderText("Job ID")
        watch_btn = QPushButton("▶  Watch")
        watch_btn.clicked.connect(self._watch_job)
        watch_layout.addWidget(self._watch_job_id, 1)
        watch_layout.addWidget(watch_btn)
        layout.addWidget(watch_group)

        self._jobs_console = StreamConsole()
        layout.addWidget(self._jobs_console, stretch=1)
        return w

    def _list_jobs(self) -> None:
        self._jobs_output.setPlainText("Loading…")
        service = self._service

        def _task():
            return service.list_jobs()

        def _done(result):
            if result.get("ok"):
                self._jobs_output.setPlainText(safe_json_dumps(result.get("data"), indent=2))
            else:
                self._jobs_output.setPlainText(f"Error: {format_rpc_error(result.get('error'))}")

        def _err(msg, _tb):
            self._jobs_output.setPlainText(f"Error: {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()

    def _submit_job(self) -> None:
        import json  # noqa: PLC0415
        problem_text = self._job_problem.text().strip()
        try:
            problem_spec = json.loads(problem_text) if problem_text else {}
        except json.JSONDecodeError as exc:
            self._jobs_output.setPlainText(f"Invalid JSON: {exc}")
            return
        budget = self._job_budget.value()
        qubits = self._job_qubits.value()
        shots = self._job_shots.value()
        service = self._service
        self._jobs_output.setPlainText("Submitting…")

        def _task():
            return service.submit_job(problem_spec, budget, qubits=qubits, shots=shots)

        def _done(result):
            if result.get("ok"):
                self._jobs_output.setPlainText(f"✅ Job submitted!\n{safe_json_dumps(result.get('data'), indent=2)}")
            else:
                self._jobs_output.setPlainText(f"Error: {format_rpc_error(result.get('error'))}")

        def _err(msg, _tb):
            self._jobs_output.setPlainText(f"Error: {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()

    def _watch_job(self) -> None:
        job_id = self._watch_job_id.text().strip()
        if not job_id:
            self._jobs_console.append_system("Enter a Job ID first.")
            return
        self._cancel_token = CancelToken()
        token = self._cancel_token
        console = self._jobs_console
        service = self._service
        console.append_system(f"Watching job {job_id}…")

        def _task():
            return service.watch_job_cli(
                job_id,
                cancel_token=token,
                stream_cb=lambda ev: console.append_line(ev.stream, ev.line),
            )

        def _done(result):
            if result.success:
                console.append_system("✅ Done.")
            else:
                console.append_system(f"⚠ Finished (rc={result.returncode})")

        def _err(msg, _tb):
            console.append_system(f"❌ {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()
