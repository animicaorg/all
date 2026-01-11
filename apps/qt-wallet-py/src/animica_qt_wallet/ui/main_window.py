from __future__ import annotations

import asyncio

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMenu,
    QStatusBar,
    QWidget,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QComboBox,
    QPlainTextEdit,
)

from animica_qt_wallet.core.walletd_manager import WalletdManager


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Animica Wallet")
        self.resize(900, 600)

        self._walletd_manager = WalletdManager()
        self._walletd_task: asyncio.Task[None] | None = None
        self._walletd_status_timer = QTimer(self)
        self._walletd_status_timer.setInterval(2_000)
        self._walletd_status_timer.timeout.connect(self._refresh_walletd_status)
        self._node_status_timer = QTimer(self)
        self._node_status_timer.setInterval(2_000)
        self._node_status_timer.timeout.connect(self._refresh_node_status)
        self._node_logs_timer = QTimer(self)
        self._node_logs_timer.setInterval(2_000)
        self._node_logs_timer.timeout.connect(self._refresh_node_logs)

        self._build_menu()
        self._build_status_bar()
        self._build_central()

        self._walletd_task = asyncio.create_task(self._start_walletd())

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()

        file_menu = QMenu("File", self)
        settings_menu = QMenu("Settings", self)
        help_menu = QMenu("Help", self)

        menu_bar.addMenu(file_menu)
        menu_bar.addMenu(settings_menu)
        menu_bar.addMenu(help_menu)

    def _build_status_bar(self) -> None:
        status = QStatusBar(self)
        self._node_status = QLabel("Node: stopped", self)
        self._walletd_status = QLabel("Walletd: starting...", self)
        status.addWidget(self._node_status)
        status.addPermanentWidget(self._walletd_status)
        self.setStatusBar(status)

    def _build_central(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)

        title = QLabel("Welcome / Setup", self)
        title.setStyleSheet("font-size: 20px; font-weight: 600;")

        subtitle = QLabel("Prepare your wallet and connect to a node.", self)
        subtitle.setStyleSheet("color: #6b6b6b;")

        controls = QHBoxLayout()
        self._network_selector = QComboBox(self)
        self._network_selector.addItems(["mainnet", "testnet"])
        self._start_node_button = QPushButton("Start Node", self)
        self._stop_node_button = QPushButton("Stop Node", self)
        self._start_node_button.clicked.connect(self._handle_start_node)
        self._stop_node_button.clicked.connect(self._handle_stop_node)
        controls.addWidget(QLabel("Network:", self))
        controls.addWidget(self._network_selector)
        controls.addStretch(1)
        controls.addWidget(self._start_node_button)
        controls.addWidget(self._stop_node_button)

        self._logs_view = QPlainTextEdit(self)
        self._logs_view.setReadOnly(True)
        self._logs_view.setPlaceholderText("Node logs will appear here once the node starts.")

        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(controls)
        layout.addWidget(QLabel("Node Logs", self))
        layout.addWidget(self._logs_view, stretch=2)
        layout.addStretch(1)

        self.setCentralWidget(central)

    async def _start_walletd(self) -> None:
        status = await self._walletd_manager.ensure_running()
        if status.running:
            self._walletd_status.setText("Walletd: OK")
            self._walletd_status_timer.start()
            self._node_status_timer.start()
            self._node_logs_timer.start()
        else:
            self._walletd_status.setText("Walletd: unavailable")

    def _refresh_walletd_status(self) -> None:
        asyncio.create_task(self._update_walletd_status())

    async def _update_walletd_status(self) -> None:
        status = await self._walletd_manager.ensure_running()
        if status.running:
            self._walletd_status.setText("Walletd: OK")
        else:
            self._walletd_status.setText("Walletd: unavailable")

    def _refresh_node_status(self) -> None:
        asyncio.create_task(self._update_node_status())

    async def _update_node_status(self) -> None:
        try:
            status = await self._walletd_manager.get_node_status()
        except Exception:
            self._node_status.setText("Node: unavailable")
            return
        if status.running:
            label = f"Node: running ({status.network})"
        elif status.restarting:
            label = f"Node: restarting ({status.network})"
        else:
            label = "Node: stopped"
        self._node_status.setText(label)

    def _refresh_node_logs(self) -> None:
        asyncio.create_task(self._update_node_logs())

    async def _update_node_logs(self) -> None:
        try:
            lines = await self._walletd_manager.get_node_logs_tail(200)
        except Exception:
            return
        self._logs_view.setPlainText("\n".join(lines))
        self._logs_view.verticalScrollBar().setValue(self._logs_view.verticalScrollBar().maximum())

    def _handle_start_node(self) -> None:
        network = self._network_selector.currentText()
        asyncio.create_task(self._walletd_manager.start_node(network))

    def _handle_stop_node(self) -> None:
        asyncio.create_task(self._walletd_manager.stop_node())

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._walletd_task:
            self._walletd_task.cancel()
        asyncio.create_task(self._walletd_manager.shutdown())
        super().closeEvent(event)
