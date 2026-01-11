from __future__ import annotations

from PySide6.QtWidgets import QLabel, QMainWindow, QMenu, QStatusBar, QWidget
from PySide6.QtWidgets import QVBoxLayout


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Animica Wallet")
        self.resize(900, 600)

        self._build_menu()
        self._build_status_bar()
        self._build_central()

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
        status.showMessage("Node: stopped")
        self.setStatusBar(status)

    def _build_central(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)

        title = QLabel("Welcome / Setup", self)
        title.setStyleSheet("font-size: 20px; font-weight: 600;")

        subtitle = QLabel("Prepare your wallet and connect to a node.", self)
        subtitle.setStyleSheet("color: #6b6b6b;")

        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addStretch(2)

        self.setCentralWidget(central)
