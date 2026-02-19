"""DA page — blob put/get/proof with progress and namespace support."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.da_service import DaService
from animica_studio.services.error_format import format_rpc_error, safe_json_dumps
from animica_studio.services.workers import WorkerThread
from animica_studio.storage.config import Config
from animica_studio.ui.widgets.stream_console import StreamConsole
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)


class DaPage(QWidget):
    """Data Availability: put, get, proof."""

    def __init__(self, config: Config | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from animica_studio.storage.config import load_config  # noqa: PLC0415
        self._config = config or load_config()
        self._service = DaService(self._config)
        self._cancel_token = CancelToken()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("🗄️  DA (Data Availability)")
        title.setObjectName("placeholderLabel")
        layout.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._build_put_tab(), "Put Blob")
        tabs.addTab(self._build_get_tab(), "Get Blob")
        tabs.addTab(self._build_proof_tab(), "Get Proof")
        layout.addWidget(tabs, stretch=1)

    # ------------------------------------------------------------------
    # Put tab
    # ------------------------------------------------------------------

    def _build_put_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        form = QFormLayout()
        self._put_namespace = QLineEdit()
        self._put_namespace.setPlaceholderText("Optional namespace")
        form.addRow("Namespace:", self._put_namespace)
        self._put_file_path = QLineEdit()
        self._put_file_path.setPlaceholderText("File path or enter text below")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_file)
        file_row = QHBoxLayout()
        file_row.addWidget(self._put_file_path)
        file_row.addWidget(browse_btn)
        form.addRow("File:", file_row)
        layout.addLayout(form)

        self._put_text = QTextEdit()
        self._put_text.setPlaceholderText("Or paste raw text/hex to upload as blob…")
        self._put_text.setMaximumHeight(120)
        layout.addWidget(self._put_text)

        self._put_progress = QProgressBar()
        self._put_progress.setVisible(False)
        layout.addWidget(self._put_progress)

        btn_row = QHBoxLayout()
        put_btn = QPushButton("⬆️  Upload Blob")
        put_btn.clicked.connect(self._on_put)
        self._put_cancel_btn = QPushButton("⏹  Cancel")
        self._put_cancel_btn.setEnabled(False)
        self._put_cancel_btn.clicked.connect(lambda: self._cancel_token.cancel())
        btn_row.addWidget(put_btn)
        btn_row.addWidget(self._put_cancel_btn)
        layout.addLayout(btn_row)

        self._put_output = QTextEdit()
        self._put_output.setReadOnly(True)
        layout.addWidget(self._put_output, stretch=1)
        return w

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select file to upload")
        if path:
            self._put_file_path.setText(path)

    def _on_put(self) -> None:
        import os  # noqa: PLC0415
        namespace = self._put_namespace.text().strip() or None
        file_path = self._put_file_path.text().strip()
        text = self._put_text.toPlainText().strip()

        if file_path and os.path.isfile(file_path):
            with open(file_path, "rb") as f:
                data = f.read()
        elif text:
            data = text.encode()
        else:
            self._put_output.setPlainText("Provide a file or text to upload.")
            return

        self._cancel_token = CancelToken()
        token = self._cancel_token
        self._put_progress.setVisible(True)
        self._put_progress.setValue(0)
        self._put_output.setPlainText("Uploading…")
        self._put_cancel_btn.setEnabled(True)
        service = self._service
        total_size = len(data)

        def _progress(done, total):
            pct = int(done * 100 / max(total, 1))
            self._put_progress.setValue(pct)

        def _task():
            return service.put_blob(data, namespace=namespace, cancel_token=token, progress_cb=_progress)

        def _done(result):
            self._put_progress.setVisible(False)
            self._put_cancel_btn.setEnabled(False)
            if result.get("ok"):
                self._put_output.setPlainText(f"✅ Uploaded!\n{safe_json_dumps(result, indent=2)}")
            else:
                self._put_output.setPlainText(f"Error: {format_rpc_error(result.get('error'))}")

        def _err(msg, _tb):
            self._put_progress.setVisible(False)
            self._put_cancel_btn.setEnabled(False)
            self._put_output.setPlainText(f"Error: {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()

    # ------------------------------------------------------------------
    # Get tab
    # ------------------------------------------------------------------

    def _build_get_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        form = QFormLayout()
        self._get_commitment = QLineEdit()
        self._get_commitment.setPlaceholderText("Commitment hash (0x…)")
        form.addRow("Commitment:", self._get_commitment)
        layout.addLayout(form)
        get_btn = QPushButton("⬇️  Download Blob")
        get_btn.clicked.connect(self._on_get)
        layout.addWidget(get_btn)
        self._get_output = QTextEdit()
        self._get_output.setReadOnly(True)
        layout.addWidget(self._get_output, stretch=1)
        return w

    def _on_get(self) -> None:
        commitment = self._get_commitment.text().strip()
        if not commitment:
            self._get_output.setPlainText("Enter a commitment hash.")
            return
        self._get_output.setPlainText("Downloading…")
        service = self._service

        def _task():
            return service.get_blob(commitment)

        def _done(result):
            if result.get("ok"):
                raw = result.get("data")
                if isinstance(raw, bytes):
                    try:
                        text = raw.decode("utf-8")
                    except Exception:  # noqa: BLE001
                        text = "0x" + raw.hex()
                    self._get_output.setPlainText(f"✅ {len(raw)} bytes:\n{text[:2000]}")
                else:
                    self._get_output.setPlainText(f"✅\n{safe_json_dumps(raw, indent=2)}")
            else:
                self._get_output.setPlainText(f"Error: {format_rpc_error(result.get('error'))}")

        def _err(msg, _tb):
            self._get_output.setPlainText(f"Error: {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()

    # ------------------------------------------------------------------
    # Proof tab
    # ------------------------------------------------------------------

    def _build_proof_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        form = QFormLayout()
        self._proof_commitment = QLineEdit()
        self._proof_commitment.setPlaceholderText("Commitment hash (0x…)")
        form.addRow("Commitment:", self._proof_commitment)
        layout.addLayout(form)
        proof_btn = QPushButton("🔍  Get Proof")
        proof_btn.clicked.connect(self._on_proof)
        layout.addWidget(proof_btn)
        self._proof_output = QTextEdit()
        self._proof_output.setReadOnly(True)
        layout.addWidget(self._proof_output, stretch=1)
        return w

    def _on_proof(self) -> None:
        commitment = self._proof_commitment.text().strip()
        if not commitment:
            self._proof_output.setPlainText("Enter a commitment hash.")
            return
        self._proof_output.setPlainText("Fetching proof…")
        service = self._service

        def _task():
            return service.get_proof(commitment)

        def _done(result):
            if result.get("ok"):
                self._proof_output.setPlainText(f"✅\n{safe_json_dumps(result.get('proof'), indent=2)}")
            else:
                self._proof_output.setPlainText(f"Error: {format_rpc_error(result.get('error'))}")

        def _err(msg, _tb):
            self._proof_output.setPlainText(f"Error: {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()
