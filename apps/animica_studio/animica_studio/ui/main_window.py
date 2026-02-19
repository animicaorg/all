"""MainWindow: sidebar navigation + header bar + stacked pages."""

from __future__ import annotations

import logging
from typing import NamedTuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from animica_studio.models.profile_models import RpcProfile
from animica_studio.services.profile_service import ProfileService
from animica_studio.storage.config import Config
from animica_studio.ui.pages.console_page import ConsolePage
from animica_studio.ui.pages.dashboard import DashboardPage
from animica_studio.ui.pages.ide_page import IdePage
from animica_studio.ui.pages.node import NodePage
from animica_studio.ui.pages.settings import SettingsPage
from animica_studio.ui.pages.wallet_page import WalletPage

log = logging.getLogger(__name__)

_HEALTH_INTERVAL_MS = 10_000  # 10 seconds
_HEALTH_GREEN_THRESHOLD_S = 30.0
_HEALTH_YELLOW_THRESHOLD_S = 120.0

# Health indicator dots
_DOT_GREEN = "🟢"
_DOT_YELLOW = "🟡"
_DOT_RED = "🔴"


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

        ┌──────────────────────────────────────────────────────┐
        │  Header bar  (profile combo │ rpc url │ chain │ dot)  │
        ├─────────┬────────────────────────────────────────────┤
        │         │                                            │
        │ Sidebar │          Stacked pages                     │
        │  nav    │                                            │
        │         │                                            │
        └─────────┴────────────────────────────────────────────┘
    """

    def __init__(self, config: Config, profile_service: ProfileService) -> None:
        super().__init__()
        self._config = config
        self._profile_service = profile_service
        self._nav_buttons: list[QPushButton] = []

        # Health-check state
        self._last_rpc_success_ts: float = 0.0
        self._last_rpc_error: str | None = None
        self._last_actual_chain_id: int | None = None
        self._health_worker = None  # type: ignore[var-annotated]

        self.setWindowTitle("Animica Studio")
        self.resize(1100, 700)
        self.setMinimumSize(800, 500)

        # Subscribe to profile changes
        self._profile_service.subscribe(self._on_profile_changed)

        self._build_ui()

        # Start health-check timer (UI layer — never blocks)
        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._trigger_health_check)
        self._health_timer.start(_HEALTH_INTERVAL_MS)
        # Run once at startup
        QTimer.singleShot(1000, self._trigger_health_check)

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

        # "No profile" banner (hidden by default)
        self._no_profile_banner = self._build_no_profile_banner()
        root_layout.addWidget(self._no_profile_banner)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self._stack = QStackedWidget()

        self._wallet_page = WalletPage(config=self._config)
        pages: list[_NavEntry] = [
            _NavEntry("Dashboard", "📊", DashboardPage()),
            _NavEntry("Wallet", "💳", self._wallet_page),
            _NavEntry("Node", "🖥️", NodePage(config=self._config)),
            _NavEntry("Console", "🖱️", ConsolePage()),
            _NavEntry("IDE", "📝", IdePage()),
            _NavEntry("Settings", "⚙️", SettingsPage(config=self._config)),
        ]

        body_layout.addWidget(self._build_sidebar(pages))
        body_layout.addWidget(self._stack, stretch=1)
        root_layout.addWidget(body, stretch=1)

        # Default to first page
        if pages:
            self._navigate(0)

        # Refresh header from current active profile
        self.refresh_header()

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("headerBar")
        header.setFixedHeight(48)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)

        title = QLabel("Animica Studio")
        title.setObjectName("headerTitle")
        layout.addWidget(title)

        sep0 = QLabel("|")
        sep0.setObjectName("headerSep")
        layout.addWidget(sep0)

        # Profile combo
        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(140)
        self._profile_combo.setObjectName("profileCombo")
        self._profile_combo.currentIndexChanged.connect(self._on_profile_combo_changed)
        layout.addWidget(self._profile_combo)

        sep1 = QLabel("|")
        sep1.setObjectName("headerSep")
        layout.addWidget(sep1)

        # RPC URL label (elided)
        self._rpc_label = QLabel("")
        self._rpc_label.setObjectName("headerMeta")
        self._rpc_label.setMaximumWidth(200)
        layout.addWidget(self._rpc_label)

        sep2 = QLabel("|")
        sep2.setObjectName("headerSep")
        layout.addWidget(sep2)

        # Chain ID
        self._chain_label = QLabel("")
        self._chain_label.setObjectName("headerMeta")
        layout.addWidget(self._chain_label)

        # Health dot
        self._health_dot = QLabel(_DOT_RED)
        self._health_dot.setToolTip("Node health: unknown")
        layout.addWidget(self._health_dot)

        layout.addStretch()

        # Manage profiles menu button
        self._manage_btn = QPushButton("⚙️ Profiles")
        self._manage_btn.setObjectName("navButton")
        self._manage_btn.setFlat(True)
        self._manage_btn.clicked.connect(self._open_profiles_menu)
        layout.addWidget(self._manage_btn)

        return header

    def _build_no_profile_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName("noProfileBanner")
        banner.setStyleSheet(
            "QFrame#noProfileBanner { background: #45475a; border-bottom: 1px solid #f38ba8; }"
        )
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(16, 6, 16, 6)
        msg = QLabel("⚠️  No profile configured. Set up a connection to use the app.")
        msg.setStyleSheet("color: #f9e2af;")
        layout.addWidget(msg)
        layout.addStretch()
        wizard_btn = QPushButton("Open Setup Wizard")
        wizard_btn.setObjectName("primaryButton")
        wizard_btn.clicked.connect(self._open_wizard)
        layout.addWidget(wizard_btn)
        banner.setVisible(False)
        banner.setFixedHeight(40)
        return banner

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
    # Header / profile refresh
    # ------------------------------------------------------------------

    def refresh_header(self) -> None:
        """Re-read the active profile from the service and update header labels."""
        try:
            profiles = self._profile_service.list_profiles()
        except Exception:  # noqa: BLE001
            profiles = []

        # Rebuild combo (block signals while doing so)
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        active_id = self._profile_service.get_active_profile_id()
        current_idx = 0
        for i, p in enumerate(profiles):
            self._profile_combo.addItem(p.name, p.id)
            if p.id == active_id:
                current_idx = i
        self._profile_combo.setCurrentIndex(current_idx)
        self._profile_combo.blockSignals(False)

        # Update labels
        try:
            active = self._profile_service.get_active()
            self._rpc_label.setText(self._elide_url(active.effective_rpc_url(), 30))
            self._rpc_label.setToolTip(active.effective_rpc_url())
            expected = active.chain_id_expected
            actual = self._last_actual_chain_id
            if actual is not None and actual != expected:
                self._chain_label.setText(f"Chain: {expected}/{actual} ⚠️")
                self._chain_label.setStyleSheet("color: #f9e2af;")
            else:
                self._chain_label.setText(f"Chain: {expected}")
                self._chain_label.setStyleSheet("color: #a6adc8;")
            self._no_profile_banner.setVisible(False)
        except Exception:  # noqa: BLE001
            self._rpc_label.setText("—")
            self._chain_label.setText("—")
            self._no_profile_banner.setVisible(True)

        self._update_health_dot()

    def _elide_url(self, url: str, max_len: int) -> str:
        if len(url) <= max_len:
            return url
        keep = max_len - 3
        half = keep // 2
        return url[:half] + "…" + url[-half:]

    # ------------------------------------------------------------------
    # Profile combo handler
    # ------------------------------------------------------------------

    def _on_profile_combo_changed(self, index: int) -> None:
        if index < 0:
            return
        profile_id = self._profile_combo.itemData(index)
        if profile_id and profile_id != self._profile_service.get_active_profile_id():
            try:
                self._profile_service.set_active(profile_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to switch profile: %s", exc)

    def _on_profile_changed(self, profile: RpcProfile) -> None:
        """Called by ProfileService when the active profile changes."""
        self._last_actual_chain_id = None  # reset until next health check
        self.refresh_header()
        # Notify wallet page so it can cancel in-flight requests and refresh
        self._wallet_page.on_profile_changed(profile)

    # ------------------------------------------------------------------
    # Profiles menu / wizard
    # ------------------------------------------------------------------

    def _open_profiles_menu(self) -> None:
        menu = QMenu(self)
        wizard_action = menu.addAction("Setup Wizard…")
        manage_action = menu.addAction("Manage Profiles…")
        wizard_action.triggered.connect(self._open_wizard)
        manage_action.triggered.connect(self._open_profiles_dialog)
        menu.exec(self._manage_btn.mapToGlobal(self._manage_btn.rect().bottomLeft()))

    def _open_wizard(self) -> None:
        from animica_studio.ui.wizard.wizard_window import SetupWizard  # noqa: PLC0415

        dlg = SetupWizard(self._profile_service, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self.refresh_header()

    def _open_profiles_dialog(self) -> None:
        from animica_studio.ui.dialogs.profiles_dialog import ProfilesDialog  # noqa: PLC0415

        dlg = ProfilesDialog(self._profile_service, parent=self)
        dlg.exec()
        self.refresh_header()

    # ------------------------------------------------------------------
    # Background health check
    # ------------------------------------------------------------------

    def _trigger_health_check(self) -> None:
        """Start a background RPC ping. Results update the health dot."""
        try:
            active = self._profile_service.get_active()
        except Exception:  # noqa: BLE001
            return

        url = active.effective_rpc_url()

        def _do_check() -> dict:
            from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415
            import time as _time  # noqa: PLC0415

            client = RpcClient(url, connect_timeout=3.0, read_timeout=8.0, max_retries=1)
            try:
                head = client.get_head()
                chain_id = None
                try:
                    chain_id = client.get_chain_id()
                except Exception:  # noqa: BLE001
                    pass
                drift_s: float | None = None
                if head.timestamp is not None:
                    drift_s = abs(_time.time() - head.timestamp)
                return {
                    "ok": True,
                    "chain_id": chain_id,
                    "head": head.number,
                    "drift_s": drift_s,
                    "ts": _time.time(),
                }
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc), "ts": _time.time()}
            finally:
                client.close()

        from animica_studio.services.workers import WorkerThread  # noqa: PLC0415

        self._health_worker = WorkerThread(_do_check)
        self._health_worker.worker.result.connect(self._on_health_result)
        self._health_worker.worker.error.connect(
            lambda m, _t: self._on_health_result({"ok": False, "error": m})
        )
        self._health_worker.start()

    def _on_health_result(self, result: dict) -> None:
        import time  # noqa: PLC0415

        if result.get("ok"):
            self._last_rpc_success_ts = result.get("ts", time.time())
            self._last_rpc_error = None
            chain_id = result.get("chain_id")
            if chain_id is not None:
                self._last_actual_chain_id = chain_id
        else:
            self._last_rpc_error = result.get("error", "Unknown error")
            log.debug("Health check failed: %s", self._last_rpc_error)

        self._update_health_dot()
        self.refresh_header()

    def _update_health_dot(self) -> None:
        import time  # noqa: PLC0415

        now = time.time()
        elapsed = now - self._last_rpc_success_ts

        if self._last_rpc_success_ts == 0.0:
            dot = _DOT_RED
            tip = f"Never connected. {self._last_rpc_error or ''}"
        elif elapsed < _HEALTH_GREEN_THRESHOLD_S:
            dot = _DOT_GREEN
            tip = f"Connected — last success {elapsed:.0f}s ago"
        elif elapsed < _HEALTH_YELLOW_THRESHOLD_S:
            dot = _DOT_YELLOW
            tip = f"Degraded — last success {elapsed:.0f}s ago"
        else:
            dot = _DOT_RED
            tip = f"Unreachable — last success {elapsed:.0f}s ago. {self._last_rpc_error or ''}"

        self._health_dot.setText(dot)
        self._health_dot.setToolTip(tip)

    # ------------------------------------------------------------------
    # Backward-compat helper
    # ------------------------------------------------------------------

    def show_no_profile_banner(self) -> None:
        """Show the 'no profile configured' banner."""
        self._no_profile_banner.setVisible(True)

    # ------------------------------------------------------------------
    # Close event — clean shutdown
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Clean shutdown: stop node if configured, stop health timer."""
        log.info("MainWindow: close event — shutting down")

        stop_on_exit = getattr(self._config, "stop_node_on_exit", True)
        if stop_on_exit:
            try:
                active = self._profile_service.get_active()
            except Exception:  # noqa: BLE001
                active = None
            if active is not None:
                from animica_studio.services.process_manager import ProcessManager  # noqa: PLC0415
                try:
                    pm = ProcessManager(rpc_url=active.effective_rpc_url())
                    status = pm.status()
                    if status.get("running"):
                        log.info("MainWindow: stopping local node on exit")
                        pm.stop()
                except Exception as exc:  # noqa: BLE001
                    log.warning("MainWindow: could not stop node on exit: %s", exc)

        self._health_timer.stop()
        event.accept()
