"""Mining page — mine blocks, automine toggle, live mining log."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.mining_service import MiningService
from animica_studio.services.workers import WorkerThread
from animica_studio.storage.config import Config
from animica_studio.ui.widgets.stream_console import StreamConsole
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)


class MiningPage(QWidget):
    """Mining controls: mine-blocks, automine, live log."""

    def __init__(self, config: Config | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from animica_studio.storage.config import load_config  # noqa: PLC0415
        self._config = config or load_config()
        self._service = MiningService(self._config)
        self._cancel_token = CancelToken()
        self._worker: WorkerThread | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("⛏️  Mining")
        title.setObjectName("placeholderLabel")
        layout.addWidget(title)

        # Mine-blocks group
        mine_group = QGroupBox("Mine Blocks (local CPU)")
        mine_layout = QHBoxLayout(mine_group)
        mine_layout.addWidget(QLabel("Count:"))
        self._count_spin = QSpinBox()
        self._count_spin.setRange(1, 1000)
        self._count_spin.setValue(1)
        mine_layout.addWidget(self._count_spin)
        mine_layout.addWidget(QLabel("Miner address:"))
        self._miner_addr = QLineEdit()
        self._miner_addr.setPlaceholderText("0x… or blank for node default")
        mine_layout.addWidget(self._miner_addr, 1)
        self._mine_btn = QPushButton("▶  Mine Blocks")
        self._mine_btn.clicked.connect(self._on_mine)
        mine_layout.addWidget(self._mine_btn)
        layout.addWidget(mine_group)

        # Automine group
        auto_group = QGroupBox("Automine (RPC)")
        auto_layout = QHBoxLayout(auto_group)
        self._automine_check = QCheckBox("Enable automine")
        auto_layout.addWidget(self._automine_check)
        self._automine_btn = QPushButton("Apply")
        self._automine_btn.clicked.connect(self._on_automine)
        auto_layout.addWidget(self._automine_btn)
        auto_layout.addStretch()
        layout.addWidget(auto_group)

        # Cancel button
        self._cancel_btn = QPushButton("⏹  Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(self._cancel_btn)

        # Stream console
        self._console = StreamConsole()
        layout.addWidget(self._console, stretch=1)

    # ------------------------------------------------------------------

    def _on_mine(self) -> None:
        count = self._count_spin.value()
        addr = self._miner_addr.text().strip() or None
        self._cancel_token = CancelToken()
        self._console.append_system(f"Mining {count} block(s)…")
        self._mine_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)

        service = self._service
        token = self._cancel_token
        console = self._console

        def _task():
            return service.mine_blocks(
                count,
                miner_address=addr,
                cancel_token=token,
                stream_cb=lambda ev: console.append_line(ev.stream, ev.line),
            )

        def _done(result):
            self._mine_btn.setEnabled(True)
            self._cancel_btn.setEnabled(False)
            if result.success:
                console.append_system("✅ Done.")
            else:
                console.append_system(f"⚠ Finished (rc={result.returncode}). {result.stderr[:200]}")

        def _err(msg, _tb):
            self._mine_btn.setEnabled(True)
            self._cancel_btn.setEnabled(False)
            console.append_system(f"❌ Error: {msg}")

        self._worker = WorkerThread(_task)
        self._worker.worker.result.connect(_done)
        self._worker.worker.error.connect(_err)
        self._worker.start()

    def _on_automine(self) -> None:
        enabled = self._automine_check.isChecked()
        service = self._service
        console = self._console

        def _task():
            return service.set_automine(enabled)

        def _done(result):
            if result.get("ok"):
                console.append_system(f"✅ Automine {'enabled' if enabled else 'disabled'}.")
            else:
                console.append_system(f"⚠ {result.get('error', 'Unknown error')}")

        def _err(msg, _tb):
            console.append_system(f"❌ {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()

    def _on_cancel(self) -> None:
        self._cancel_token.cancel()
        self._cancel_btn.setEnabled(False)
        self._console.append_system("[Cancellation requested…]")
