"""Main Studio shell with beginner-first navigation and shared status services."""

from __future__ import annotations

import logging
from typing import Callable, NamedTuple

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from animica_studio.models.profile_models import RpcProfile
from animica_studio.services.ena_automation_service import EnaService
from animica_studio.services.ena_contribution_engine import EnaContributionConfig, EnaContributionEngine
from animica_studio.services.ena_full_auto_engine import EnaFullAutoEngine
from animica_studio.services.ena_store import EnaStore
from animica_studio.services.profile_service import ProfileService
from animica_studio.services.rpc_client import RpcClient
from animica_studio.services.settings_service import SettingsService
from animica_studio.services.shutdown_manager import ShutdownManager
from animica_studio.services.studio_status_service import StudioStatusService
from animica_studio.storage.config import Config
from animica_studio.ui.components.primitives import Toast
from animica_studio.ui.pages.aicf_page import AicfPage
from animica_studio.ui.pages.console_page import ConsolePage
from animica_studio.ui.pages.da_page import DaPage
from animica_studio.ui.pages.dashboard import DashboardPage
from animica_studio.ui.pages.diagnostics_page import DiagnosticsPage
from animica_studio.ui.pages.ena_hub_page import EnaHubPage
from animica_studio.ui.pages.ide_page import IdePage
from animica_studio.ui.pages.mining_page import MiningPage
from animica_studio.ui.pages.node import NodePage
from animica_studio.ui.pages.quantum_page import QuantumPage
from animica_studio.ui.pages.settings import SettingsPage
from animica_studio.ui.pages.wallet_page import WalletPage
from animica_studio.ui.shell.command_palette import CommandPalette
from animica_studio.ui.shell.header import HeaderBar
from animica_studio.ui.shell.icon_provider import IconProvider
from animica_studio.ui.shell.main_stack import AnimatedStack
from animica_studio.ui.shell.sidebar import Sidebar
from animica_studio.ui.theme.stylesheet import build_stylesheet
from animica_studio.ui.theme.theme_manager import ThemeManager
from animica_studio.util.qt import safe_slot

log = logging.getLogger(__name__)
_HEALTH_INTERVAL_MS = 10_000


class _NavEntry(NamedTuple):
    label: str
    icon: str
    page_factory: Callable[[], QWidget]
    visible: bool = True


class MainWindow(QMainWindow):
    def __init__(self, config: Config, profile_service: ProfileService, *, safe_mode: bool = False) -> None:
        super().__init__()
        self._config = config
        self._profile_service = profile_service
        self._safe_mode = safe_mode
        self._theme_manager = ThemeManager(config)
        self._settings_service = SettingsService(config)
        self._status_service = StudioStatusService(config, self._settings_service)
        self._ena_service = EnaService(config, EnaStore())
        contrib_cfg = (config.ena.get("ena_contrib") if isinstance(config.ena, dict) else {}) or {}
        self._ena_contrib_engine = EnaContributionEngine(
            EnaContributionConfig(
                enabled=bool(contrib_cfg.get("enabled", False)),
                intensity=str(contrib_cfg.get("intensity") or "medium"),
                mode=str(contrib_cfg.get("mode") or "local"),
                services_url=str(contrib_cfg.get("services_url") or ""),
                auto_start=bool(contrib_cfg.get("auto_start", False)),
                rpc_url=config.get_active_profile().node.rpc_local_url,
            )
        )
        self._ena_full_auto_engine = EnaFullAutoEngine(config.get_active_profile().node.rpc_local_url, self)
        self._icons = IconProvider()
        self._nav_entries: list[_NavEntry] = []
        self._page_cache: dict[str, QWidget] = {}
        self._shutdown = ShutdownManager.instance()
        self._lazy_page_labels = {
            "IDE",
            "ENA Assistant",
            "Mining",
            "AICF",
            "DA",
            "Logs",
            "Settings",
            "Console",
            "Quantum",
        }
        self._health_worker = None
        self._last_rpc_error: str | None = None
        self._last_actual_chain_id: int | None = None

        self.setWindowTitle("Animica Studio")
        self.resize(1280, 820)
        self._profile_service.subscribe(self._on_profile_changed)
        self._build_ui()
        self._build_menu()
        self._apply_theme()
        self._theme_manager.theme_changed.connect(lambda _p: self._apply_theme())
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._open_palette)
        QShortcut(QKeySequence("Meta+K"), self, activated=self._open_palette)
        QShortcut(QKeySequence("Ctrl+\\"), self, activated=self._toggle_sidebar)
        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._trigger_health_check)
        QTimer.singleShot(0, self._ena_contrib_engine.start_if_configured)

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = HeaderBar(self._icons)
        self._header.open_palette.connect(self._open_palette)
        self._header.open_settings.connect(lambda: self._navigate(self._nav_index("Settings")))
        self._header.open_profiles.connect(self._open_profiles_menu)
        self._header.profile_combo().currentIndexChanged.connect(self._on_profile_combo_changed)
        root.addWidget(self._header)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        self._sidebar = Sidebar()
        self._stack = AnimatedStack()
        self._stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_scroll = QScrollArea()
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._content_scroll.setWidget(self._stack)
        body_layout.addWidget(self._sidebar)
        body_layout.addWidget(self._content_scroll, 1)
        root.addWidget(body, 1)
        self.setCentralWidget(central)

        self._dashboard_page = DashboardPage(
            config=self._config,
            profile_service=self._profile_service,
            status_service=self._status_service,
        )
        self._wallet_page = WalletPage(
            config=self._config,
            safe_mode=self._safe_mode,
            status_service=self._status_service,
            settings_service=self._settings_service,
        )
        self._node_page = NodePage(config=self._config, status_service=self._status_service)
        self._settings_page = SettingsPage(
            config=self._config,
            theme_manager=self._theme_manager,
            profile_service=self._profile_service,
            settings_service=self._settings_service,
            status_service=self._status_service,
        )
        self._diagnostics_page = DiagnosticsPage(config=self._config, status_service=self._status_service)

        self._page_cache = {
            "Home": self._dashboard_page,
            "Wallet": self._wallet_page,
            "Node": self._node_page,
            "Settings": self._settings_page,
            "Logs": self._diagnostics_page,
        }

        self._nav_entries = [
            _NavEntry("Home", "◈", lambda: self._dashboard_page),
            _NavEntry("IDE", "✎", lambda: self._build_ide_page_safe()),
            _NavEntry(
                "ENA Assistant",
                "✦",
                lambda: EnaHubPage(
                    config=self._config,
                    service=self._ena_service,
                    contrib_engine=self._ena_contrib_engine,
                    full_auto_engine=self._ena_full_auto_engine,
                ),
            ),
            _NavEntry("Node", "◍", lambda: self._node_page),
            _NavEntry("Wallet", "◉", lambda: self._wallet_page),
            _NavEntry("Mining", "◎", lambda: MiningPage(config=self._config)),
            _NavEntry("AICF", "◇", lambda: AicfPage(config=self._config)),
            _NavEntry("DA", "◌", lambda: DaPage(config=self._config)),
            _NavEntry("Settings", "⚙", lambda: self._settings_page),
            _NavEntry("Logs", "▣", lambda: self._diagnostics_page),
            _NavEntry("Console", "⌘", lambda: ConsolePage(config=self._config), visible=False),
            _NavEntry("Quantum", "⬡", lambda: QuantumPage(config=self._config), visible=False),
        ]

        nav_sections = {
            "Home": "Workspace",
            "IDE": "Workspace",
            "ENA Assistant": "Workspace",
            "Node": "Runtime",
            "Wallet": "Operations",
            "Mining": "Operations",
            "AICF": "Operations",
            "DA": "Operations",
            "Settings": "System",
            "Logs": "System",
            "Console": "Advanced",
            "Quantum": "Advanced",
        }
        current_section: str | None = None
        for index, entry in enumerate(self._nav_entries):
            self._stack.addWidget(self._initial_page_widget(entry))
            if entry.visible:
                section = nav_sections.get(entry.label)
                if section and section != current_section:
                    self._sidebar.add_section(section)
                    current_section = section
                self._sidebar.add_item(entry.label, entry.icon, index)
        self._sidebar.navigate.connect(self._navigate)
        self._dashboard_page.action_requested.connect(self._handle_home_action)
        self._node_page.open_logs_requested.connect(lambda: self._navigate(self._nav_index("Logs")))
        self._settings_page.rerun_onboarding_requested.connect(self._open_wizard)
        self._settings_page.open_logs_requested.connect(lambda: self._navigate(self._nav_index("Logs")))
        self._navigate(self._nav_index("Home"))
        self.refresh_header()

    def _initial_page_widget(self, entry: _NavEntry) -> QWidget:
        cached = self._page_cache.get(entry.label)
        if cached is not None:
            return cached
        placeholder = QWidget(self)
        layout = QVBoxLayout(placeholder)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(QLabel(f"{entry.label} loads on demand."))
        layout.addStretch(1)
        return placeholder

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        wizard_action = QAction("Setup Wizard…", self)
        wizard_action.triggered.connect(self._open_wizard)
        file_menu.addAction(wizard_action)

        tools_menu = self.menuBar().addMenu("Tools")
        for label in ("IDE", "Console", "Quantum"):
            action = QAction(label, self)
            action.triggered.connect(lambda _checked=False, name=label: self._navigate(self._nav_index(name)))
            tools_menu.addAction(action)

    def _apply_theme(self) -> None:
        self.setStyleSheet(build_stylesheet(self._theme_manager.palette()))
        self._dashboard_page.set_visual_effects(
            False if self._safe_mode else self._theme_manager.visual_effects(),
            self._theme_manager.reduced_motion(),
        )

    def _toggle_sidebar(self) -> None:
        self._sidebar.toggle(animate=not self._theme_manager.reduced_motion())

    def _nav_index(self, label: str) -> int:
        for index, entry in enumerate(self._nav_entries):
            if entry.label == label:
                return index
        raise KeyError(label)

    def _ensure_page_loaded(self, index: int) -> None:
        entry = self._nav_entries[index]
        if entry.label not in self._lazy_page_labels or entry.label in self._page_cache:
            return
        page = entry.page_factory()
        self._page_cache[entry.label] = page
        current = self._stack.widget(index)
        self._stack.insertWidget(index, page)
        if current is not None:
            self._stack.removeWidget(current)
            current.deleteLater()

    def _navigate(self, index: int) -> None:
        self._ensure_page_loaded(index)
        self._stack.setCurrentIndexAnimated(index, reduced_motion=self._theme_manager.reduced_motion())
        self._sidebar.set_active(index)

    @safe_slot(log)
    def _open_palette(self) -> None:
        items = [entry.label for entry in self._nav_entries]
        dlg = CommandPalette(items, self)
        dlg.navigate.connect(self._navigate)
        dlg.exec()

    def _handle_home_action(self, action_id: str) -> None:
        if action_id == "wallet_create":
            self._navigate(self._nav_index("Wallet"))
            self._wallet_page.focus_create_wallet()
            return
        if action_id == "wallet_send":
            self._navigate(self._nav_index("Wallet"))
            self._wallet_page.focus_send()
            return
        if action_id == "wallet_receive":
            self._navigate(self._nav_index("Wallet"))
            self._wallet_page.focus_receive()
            return
        if action_id == "node_open":
            self._navigate(self._nav_index("Node"))
            return
        if action_id == "node_start":
            self._navigate(self._nav_index("Node"))
            self._node_page._run_start()  # noqa: SLF001
            return
        if action_id == "mining_open":
            self._navigate(self._nav_index("Mining"))
            return
        if action_id == "ide_open":
            self._navigate(self._nav_index("IDE"))
            return
        if action_id == "ena_open":
            self._navigate(self._nav_index("ENA Assistant"))
            return
        if action_id == "settings_open":
            self._navigate(self._nav_index("Settings"))
            return
        if action_id == "logs_open":
            self._navigate(self._nav_index("Logs"))

    def refresh_header(self) -> None:
        try:
            profiles = self._profile_service.list_profiles()
        except Exception:
            profiles = []
        combo = self._header.profile_combo()
        combo.blockSignals(True)
        combo.clear()
        active_id = self._profile_service.get_active_profile_id()
        current_index = 0
        for index, profile in enumerate(profiles):
            combo.addItem(profile.name, profile.id)
            if profile.id == active_id:
                current_index = index
        combo.setCurrentIndex(current_index)
        combo.blockSignals(False)
        try:
            active = self._profile_service.get_active()
            rpc = active.effective_rpc_url()
            chain = str(active.chain_id_expected)
            if self._last_actual_chain_id is not None and self._last_actual_chain_id != active.chain_id_expected:
                chain = f"{active.chain_id_expected}/{self._last_actual_chain_id}"
            self._header.set_meta(rpc, chain)
        except Exception as exc:  # noqa: BLE001
            self._header.set_meta("—", "—")
            self._toast(f"No active profile: {exc}")
        self._header.set_connection(self._last_rpc_error is None)

    @safe_slot(log)
    def _on_profile_combo_changed(self, index: int) -> None:
        if index < 0:
            return
        pid = self._header.profile_combo().itemData(index)
        if pid and pid != self._profile_service.get_active_profile_id():
            try:
                self._profile_service.set_active(pid)
            except Exception as exc:  # noqa: BLE001
                self._toast(f"Failed to switch profile: {exc}")

    @safe_slot(log)
    def _on_profile_changed(self, profile: RpcProfile) -> None:
        self._last_actual_chain_id = None
        self.refresh_header()
        self._wallet_page.on_profile_changed(profile)
        self._node_page.refresh_status()
        self._diagnostics_page._refresh()  # noqa: SLF001

    @safe_slot(log)
    def _open_profiles_menu(self) -> None:
        menu = QMenu(self)
        wizard_action = menu.addAction("Setup Wizard…")
        manage_action = menu.addAction("Manage Profiles…")
        wizard_action.triggered.connect(self._open_wizard)
        manage_action.triggered.connect(self._open_profiles_dialog)
        menu.exec(self.cursor().pos())

    @safe_slot(log)
    def _open_wizard(self) -> None:
        from animica_studio.ui.wizard.wizard_window import SetupWizard  # noqa: PLC0415

        dlg = SetupWizard(
            self._profile_service,
            settings_service=self._settings_service,
            status_service=self._status_service,
            parent=self,
        )
        if dlg.exec() == dlg.DialogCode.Accepted:
            self.refresh_header()
            self._dashboard_page.refresh_snapshot()
            self._wallet_page.refresh_wallets()
            self._node_page.refresh_status()

    @safe_slot(log)
    def _open_profiles_dialog(self) -> None:
        from animica_studio.ui.dialogs.profiles_dialog import ProfilesDialog  # noqa: PLC0415

        dlg = ProfilesDialog(self._profile_service, parent=self)
        dlg.exec()
        self.refresh_header()

    @safe_slot(log)
    def _trigger_health_check(self) -> None:
        if self._safe_mode or (self._health_worker is not None and self._health_worker.isRunning()):
            return
        active = self._profile_service.get_active()
        url = active.effective_rpc_url()

        def _do_check() -> dict[str, object]:
            client = RpcClient(url, connect_timeout=3.0, read_timeout=8.0, max_retries=1)
            try:
                head = client.get_head()
                try:
                    chain_id = client.get_chain_id()
                except Exception:
                    chain_id = None
                return {"ok": True, "chain_id": chain_id, "head": head.number}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}
            finally:
                client.close()

        from animica_studio.services.workers import WorkerThread  # noqa: PLC0415

        self._health_worker = WorkerThread(_do_check)
        self._shutdown.track_thread(self._health_worker)
        self._health_worker.worker.result.connect(self._on_health_result)
        self._health_worker.worker.error.connect(lambda msg, _tb: self._on_health_result({"ok": False, "error": msg}))
        self._health_worker.start()

    def _on_health_result(self, result: dict[str, object]) -> None:
        if result.get("ok"):
            self._last_rpc_error = None
            self._last_actual_chain_id = result.get("chain_id") if isinstance(result.get("chain_id"), int) else None
        else:
            self._last_rpc_error = str(result.get("error", "Unknown error"))
        self.refresh_header()

    def _toast(self, text: str) -> None:
        toast = Toast(self, text)
        toast.move(self.width() - 320, 72)
        toast.show_toast(animate=not self._theme_manager.reduced_motion())

    def show_no_profile_banner(self) -> None:
        self._toast("No profile configured. Open the Setup Wizard from Profiles.")

    def show_startup_degraded_banner(self, message: str) -> None:
        self._toast(message)

    def run_post_start_init(self) -> None:
        if self._safe_mode:
            self.show_startup_degraded_banner("Safe mode enabled")
            return
        self._health_timer.start(_HEALTH_INTERVAL_MS)
        QTimer.singleShot(1200, self._trigger_health_check)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._health_timer.stop()
        try:
            self._profile_service.unsubscribe(self._on_profile_changed)
        except Exception:
            log.exception("Failed to unsubscribe profile observer during shutdown")
        try:
            self._ena_contrib_engine.stop()
        except Exception:
            log.exception("Failed to stop ENA contribution engine")
        try:
            self._ena_full_auto_engine.stop()
        except Exception:
            log.exception("Failed to stop ENA full-auto engine")
        if self._health_worker is not None and self._health_worker.isRunning():
            self._health_worker.quit()
            self._health_worker.wait(1000)
        self._shutdown.shutdown()
        super().closeEvent(event)
        event.accept()

    def _build_ide_page_safe(self) -> QWidget:
        try:
            return IdePage()
        except Exception:  # noqa: BLE001
            log.exception("IDE page initialisation failed")
            placeholder = QWidget()
            layout = QVBoxLayout(placeholder)
            layout.addWidget(QLabel("IDE unavailable in this environment."))
            return placeholder
