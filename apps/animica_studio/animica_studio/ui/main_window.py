"""MainWindow: sidebar navigation + header bar + stacked pages."""

from __future__ import annotations

import logging
from typing import NamedTuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from animica_studio.storage.config import Config
from animica_studio.ui.pages.console import ConsolePage
from animica_studio.ui.pages.dashboard import DashboardPage
from animica_studio.ui.pages.node import NodePage
from animica_studio.ui.pages.settings import SettingsPage
from animica_studio.ui.pages.wallet import WalletPage

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Navigation entry
# ---------------------------------------------------------------------------


class _NavEntry(NamedTuple):
    label: str
    icon: str
    page: QWidget


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    """Application main window.

    Layout
    ------
    ::

        ┌──────────────────────────────────────────┐
        │  Header bar  (profile name │ rpc url)    │
        ├─────────┬────────────────────────────────┤
        │         │                                │
        │ Sidebar │        Stacked pages           │
        │  nav    │                                │
        │         │                                │
        └─────────┴────────────────────────────────┘
    """

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config
        self._nav_buttons: list[QPushButton] = []

        self.setWindowTitle("Animica Studio")
        self.resize(1100, 700)
        self.setMinimumSize(800, 500)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_header())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self._stack = QStackedWidget()

        pages: list[_NavEntry] = [
            _NavEntry("Dashboard", "📊", DashboardPage()),
            _NavEntry("Wallet", "💳", WalletPage(config=self._config)),
            _NavEntry("Node", "🖥️", NodePage(config=self._config)),
            _NavEntry("Console", "🖱️", ConsolePage()),
            _NavEntry("Settings", "⚙️", SettingsPage(config=self._config)),
        ]

        body_layout.addWidget(self._build_sidebar(pages))
        body_layout.addWidget(self._stack, stretch=1)
        root_layout.addWidget(body, stretch=1)

        # Default to first page
        if pages:
            self._navigate(0)

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("headerBar")
        header.setFixedHeight(48)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 0, 16, 0)

        title = QLabel("Animica Studio")
        title.setObjectName("headerTitle")

        layout.addWidget(title)
        layout.addStretch()

        profile = self._config.get_active_profile()

        self._profile_label = QLabel(f"Profile: {profile.name}")
        self._profile_label.setObjectName("headerMeta")

        self._rpc_label = QLabel(f"RPC: {profile.rpc_url}")
        self._rpc_label.setObjectName("headerMeta")

        sep = QLabel("|")
        sep.setObjectName("headerSep")

        layout.addWidget(self._profile_label)
        layout.addWidget(sep)
        layout.addWidget(self._rpc_label)

        return header

    def _build_sidebar(self, pages: list[_NavEntry]) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(160)
        sidebar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 16, 0, 16)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        for idx, entry in enumerate(pages):
            self._stack.addWidget(entry.page)

            btn = QPushButton(f"{entry.icon}  {entry.label}")
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.clicked.connect(lambda _checked, i=idx: self._navigate(i))
            self._nav_buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch()
        return sidebar

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _navigate(self, index: int) -> None:
        """Switch the stacked widget to *index* and update button states."""
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == index)
        log.debug("Navigated to page index %d", index)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def refresh_header(self) -> None:
        """Re-read the active profile from config and update header labels."""
        profile = self._config.get_active_profile()
        self._profile_label.setText(f"Profile: {profile.name}")
        self._rpc_label.setText(f"RPC: {profile.rpc_url}")
