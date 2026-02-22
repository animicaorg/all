"""Modern MainWindow shell with animated sidebar, header, and page transitions."""

from __future__ import annotations

import logging
from typing import Callable, NamedTuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtGui import QAction
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
from animica_studio.services.profile_service import ProfileService
from animica_studio.services.ena_automation_service import EnaService
from animica_studio.services.ena_store import EnaStore
from animica_studio.services.shutdown_manager import ShutdownManager
from animica_studio.storage.config import Config
from animica_studio.ui.components.primitives import Toast
from animica_studio.ui.pages.aicf_page import AicfPage
from animica_studio.ui.pages.console_page import ConsolePage
from animica_studio.ui.pages.da_page import DaPage
from animica_studio.ui.pages.dashboard import DashboardPage
from animica_studio.ui.pages.ena_dashboard_page import EnaDashboardPage
from animica_studio.ui.pages.contribute_page import ContributePage
from animica_studio.ui.pages.checkpoints_page import CheckpointsPage
from animica_studio.ui.pages.train_page import TrainPage
from animica_studio.ui.pages.publish_page import PublishPage
from animica_studio.ui.pages.infer_page import InferPage
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


class MainWindow(QMainWindow):
    def __init__(self, config: Config, profile_service: ProfileService, *, safe_mode: bool = False) -> None:
        super().__init__()
        self._config = config
        self._profile_service = profile_service
        self._safe_mode = safe_mode
        self._theme_manager = ThemeManager(config)
        self._ena_service = EnaService(config, EnaStore())
        self._icons = IconProvider()
        self._nav_entries: list[_NavEntry] = []
        self._last_rpc_success_ts = 0.0
        self._last_rpc_error: str | None = None
        self._last_actual_chain_id: int | None = None
        self._health_worker = None
        self._shutdown = ShutdownManager.instance()
        self._ide_page: QWidget | None = None
        self._ide_index: int | None = None
        self.setWindowTitle("Animica Studio")
        self.resize(1220, 760)
        self._profile_service.subscribe(self._on_profile_changed)
        self._build_ui()
        self._build_menu()
        self._apply_theme()
        self._theme_manager.theme_changed.connect(lambda _p: self._apply_theme())
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._open_palette)
        QShortcut(QKeySequence("Meta+K"), self, activated=self._open_palette)
        QShortcut(QKeySequence("Ctrl+\\"), self, activated=self._toggle_sidebar)
        QTimer.singleShot(0, self._clamp_to_screen)
        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._trigger_health_check)

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._header = HeaderBar(self._icons)
        self._header.open_palette.connect(self._open_palette)
        self._header.open_settings.connect(lambda: self._navigate(len(self._nav_entries)-1))
        self._header.open_profiles.connect(self._open_profiles_menu)
        self._header.profile_combo().currentIndexChanged.connect(self._on_profile_combo_changed)
        root.addWidget(self._header)

        body = QWidget()
        b = QHBoxLayout(body)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(0)
        self._sidebar = Sidebar()
        self._stack = AnimatedStack()
        self._stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_scroll = QScrollArea()
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._content_scroll.setWidget(self._stack)
        b.addWidget(self._sidebar)
        b.addWidget(self._content_scroll, 1)
        root.addWidget(body, 1)
        self.setCentralWidget(central)

        self._wallet_page = WalletPage(config=self._config, safe_mode=self._safe_mode)
        self._wallet_page.open_settings_requested.connect(self._open_settings_from_wallet)
        self._dashboard_page = DashboardPage()
        self._settings_page = SettingsPage(config=self._config, theme_manager=self._theme_manager)
        self._nav_entries = [
            _NavEntry("Dashboard", "◈", lambda: self._dashboard_page),
            _NavEntry("Wallet", "◉", lambda: self._wallet_page),
            _NavEntry("Node", "◍", lambda: NodePage(config=self._config)),
            _NavEntry("Mining", "◎", lambda: MiningPage(config=self._config)),
            _NavEntry("AICF", "◇", lambda: AicfPage(config=self._config)),
            _NavEntry("DA", "◌", lambda: DaPage(config=self._config)),
            _NavEntry("Quantum", "⬡", lambda: QuantumPage(config=self._config)),
            _NavEntry("Console", "▣", lambda: ConsolePage(config=self._config)),
            _NavEntry("IDE", "✎", self._build_ide_placeholder),
            _NavEntry("ENA Dashboard", "✦", lambda: EnaDashboardPage(config=self._config, service=self._ena_service)),
            _NavEntry("ENA Contribute", "◐", lambda: ContributePage(self._ena_service)),
            _NavEntry("ENA Checkpoints", "◑", lambda: CheckpointsPage(self._ena_service)),
            _NavEntry("ENA Train", "◒", lambda: TrainPage(self._ena_service)),
            _NavEntry("ENA Publish", "◓", lambda: PublishPage(self._ena_service)),
            _NavEntry("ENA Infer", "◔", lambda: InferPage(self._ena_service)),
            _NavEntry("Settings", "⚙", lambda: self._settings_page),
        ]
        for i, e in enumerate(self._nav_entries):
            self._stack.addWidget(e.page_factory())
            self._sidebar.add_item(e.label, e.icon, i)
            if e.label == "IDE":
                self._ide_index = i
        self._sidebar.navigate.connect(self._navigate)
        self._navigate(0)
        self.refresh_header()


    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        new_menu = file_menu.addMenu("New")
        act = QAction("Script from Template…", self)
        act.triggered.connect(self._on_file_new_template)
        new_menu.addAction(act)

    def _on_file_new_template(self) -> None:
        if self._ide_index is None:
            return
        self._navigate(self._ide_index)
        self._ensure_lazy_pages(self._ide_index)
        if self._ide_page is not None and hasattr(self._ide_page, "new_script_from_template"):
            self._ide_page.new_script_from_template()

    def _apply_theme(self) -> None:
        self.setStyleSheet(build_stylesheet(self._theme_manager.palette()))
        self._dashboard_page.set_visual_effects(
            False if self._safe_mode else self._theme_manager.visual_effects(),
            self._theme_manager.reduced_motion(),
        )

    def _toggle_sidebar(self) -> None:
        self._sidebar.toggle(animate=not self._theme_manager.reduced_motion())

    def _clamp_to_screen(self) -> None:
        screen = self.screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        bounded_width = min(max(self.width(), 960), available.width())
        bounded_height = min(max(self.height(), 640), available.height())
        self.resize(bounded_width, bounded_height)

    def _navigate(self, index: int) -> None:
        self._ensure_lazy_pages(index)
        self._stack.setCurrentIndexAnimated(index, reduced_motion=self._theme_manager.reduced_motion())
        self._sidebar.set_active(index)

    def _ensure_lazy_pages(self, index: int) -> None:
        if self._ide_index is None or index != self._ide_index or self._ide_page is not None:
            return
        self._ide_page = self._build_ide_page_safe()
        current = self._stack.widget(index)
        self._stack.insertWidget(index, self._ide_page)
        if current is not None:
            self._stack.removeWidget(current)
            current.deleteLater()

    def _build_ide_placeholder(self) -> QWidget:
        placeholder = QWidget(self)
        layout = QVBoxLayout(placeholder)
        layout.addWidget(QLabel("IDE will load on demand."))
        return placeholder

    @safe_slot(log)
    def _open_palette(self) -> None:
        dlg = CommandPalette([e.label for e in self._nav_entries], self)
        dlg.navigate.connect(self._navigate)
        dlg.exec()

    def refresh_header(self) -> None:
        try:
            profiles = self._profile_service.list_profiles()
        except Exception:
            profiles = []
        combo = self._header.profile_combo()
        combo.blockSignals(True)
        combo.clear()
        active_id = self._profile_service.get_active_profile_id()
        current_idx = 0
        for i, p in enumerate(profiles):
            combo.addItem(p.name, p.id)
            if p.id == active_id:
                current_idx = i
        combo.setCurrentIndex(current_idx)
        combo.blockSignals(False)
        try:
            active = self._profile_service.get_active()
            rpc = self._elide_url(active.effective_rpc_url(), 34)
            chain = str(active.chain_id_expected)
            if self._last_actual_chain_id is not None and self._last_actual_chain_id != active.chain_id_expected:
                chain = f"{active.chain_id_expected}/{self._last_actual_chain_id}"
            self._header.set_meta(rpc, chain)
        except Exception as exc:
            self._header.set_meta("—", "—")
            self._toast(f"No active profile: {str(exc)}")
        self._header.set_connection(self._last_rpc_error is None)

    def _elide_url(self, url: str, max_len: int) -> str:
        if len(url) <= max_len:
            return url
        half = (max_len - 1) // 2
        return f"{url[:half]}…{url[-half:]}"

    @safe_slot(log)
    def _on_profile_combo_changed(self, index: int) -> None:
        if index < 0:
            return
        pid = self._header.profile_combo().itemData(index)
        if pid and pid != self._profile_service.get_active_profile_id():
            try:
                self._profile_service.set_active(pid)
                self._toast("Active profile updated")
            except Exception as exc:
                self._toast(f"Failed to switch profile: {str(exc)}")

    @safe_slot(log)
    def _on_profile_changed(self, profile: RpcProfile) -> None:
        self._last_actual_chain_id = None
        self.refresh_header()
        self._wallet_page.on_profile_changed(profile)

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

        dlg = SetupWizard(self._profile_service, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self.refresh_header()

    @safe_slot(log)
    def _open_profiles_dialog(self) -> None:
        from animica_studio.ui.dialogs.profiles_dialog import ProfilesDialog  # noqa: PLC0415

        dlg = ProfilesDialog(self._profile_service, parent=self)
        dlg.exec()
        self.refresh_header()

    @safe_slot(log)
    def _trigger_health_check(self) -> None:
        if self._safe_mode:
            return
        if self._health_worker is not None and self._health_worker.isRunning():
            return
        try:
            active = self._profile_service.get_active()
        except Exception:
            return
        url = active.effective_rpc_url()

        def _do_check() -> dict:
            from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415
            import time as _t

            client = RpcClient(url, connect_timeout=3.0, read_timeout=8.0, max_retries=1)
            try:
                head = client.get_head()
                try:
                    chain_id = client.get_chain_id()
                except Exception:
                    chain_id = None
                return {"ok": True, "ts": _t.time(), "chain_id": chain_id, "head": head.number}
            except Exception as exc:
                return {"ok": False, "error": str(exc), "ts": _t.time()}
            finally:
                client.close()

        from animica_studio.services.workers import WorkerThread  # noqa: PLC0415

        self._health_worker = WorkerThread(_do_check)
        self._shutdown.track_thread(self._health_worker)
        self._health_worker.worker.result.connect(self._on_health_result)
        self._health_worker.worker.error.connect(
            lambda m, _tb: self._on_health_result({"ok": False, "error": str(m)})
        )
        self._health_worker.start()

    def _on_health_result(self, result: dict) -> None:
        if result.get("ok"):
            self._last_rpc_success_ts = float(result.get("ts", 0.0))
            self._last_rpc_error = None
            self._last_actual_chain_id = result.get("chain_id")
        else:
            self._last_rpc_error = str(result.get("error", "Unknown error"))
        self.refresh_header()

    def _toast(self, text: str) -> None:
        t = Toast(self, text)
        t.move(self.width() - 320, 72)
        t.show_toast(animate=not self._theme_manager.reduced_motion())

    def show_no_profile_banner(self) -> None:
        self._toast("No profile configured. Open Setup Wizard from Profiles.")

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
        event.accept()

    def _build_ide_page_safe(self) -> QWidget:
        try:
            self._ide_page = IdePage()
            return self._ide_page
        except Exception:
            log.exception("IDE page initialisation failed")
            placeholder = QWidget()
            layout = QVBoxLayout(placeholder)
            layout.addWidget(QLabel("IDE unavailable (startup degraded mode)."))
            self.show_startup_degraded_banner("Startup degraded mode: IDE is unavailable")
            return placeholder


    def _open_settings_from_wallet(self) -> None:
        self._navigate(len(self._nav_entries) - 1)
