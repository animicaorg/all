"""Modern MainWindow shell with animated sidebar, header, and page transitions."""

from __future__ import annotations

import logging
from typing import NamedTuple

from PySide6.QtCore import QShortcut, Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QMenu, QVBoxLayout, QWidget

from animica_studio.models.profile_models import RpcProfile
from animica_studio.services.profile_service import ProfileService
from animica_studio.storage.config import Config
from animica_studio.ui.components.primitives import Toast
from animica_studio.ui.pages.aicf_page import AicfPage
from animica_studio.ui.pages.console_page import ConsolePage
from animica_studio.ui.pages.da_page import DaPage
from animica_studio.ui.pages.dashboard import DashboardPage
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

log = logging.getLogger(__name__)
_HEALTH_INTERVAL_MS = 10_000


class _NavEntry(NamedTuple):
    label: str
    icon: str
    page: QWidget


class MainWindow(QMainWindow):
    def __init__(self, config: Config, profile_service: ProfileService) -> None:
        super().__init__()
        self._config = config
        self._profile_service = profile_service
        self._theme_manager = ThemeManager(config)
        self._icons = IconProvider()
        self._nav_entries: list[_NavEntry] = []
        self._last_rpc_success_ts = 0.0
        self._last_rpc_error: str | None = None
        self._last_actual_chain_id: int | None = None
        self._health_worker = None
        self.setWindowTitle("Animica Studio")
        self.resize(1220, 760)
        self._profile_service.subscribe(self._on_profile_changed)
        self._build_ui()
        self._apply_theme()
        self._theme_manager.theme_changed.connect(lambda _p: self._apply_theme())
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._open_palette)
        QShortcut(QKeySequence("Meta+K"), self, activated=self._open_palette)
        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._trigger_health_check)
        self._health_timer.start(_HEALTH_INTERVAL_MS)
        QTimer.singleShot(1200, self._trigger_health_check)

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._header = HeaderBar(self._icons)
        self._header.open_palette.connect(self._open_palette)
        self._header.open_settings.connect(lambda: self._navigate(9))
        self._header.open_profiles.connect(self._open_profiles_menu)
        self._header.profile_combo().currentIndexChanged.connect(self._on_profile_combo_changed)
        root.addWidget(self._header)

        body = QWidget()
        b = QHBoxLayout(body)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(0)
        self._sidebar = Sidebar()
        self._stack = AnimatedStack()
        b.addWidget(self._sidebar)
        b.addWidget(self._stack, 1)
        root.addWidget(body, 1)
        self.setCentralWidget(central)

        self._wallet_page = WalletPage(config=self._config)
        self._dashboard_page = DashboardPage()
        self._settings_page = SettingsPage(config=self._config, theme_manager=self._theme_manager)
        self._nav_entries = [
            _NavEntry("Dashboard", "◈", self._dashboard_page),
            _NavEntry("Wallet", "◉", self._wallet_page),
            _NavEntry("Node", "◍", NodePage(config=self._config)),
            _NavEntry("Mining", "◎", MiningPage(config=self._config)),
            _NavEntry("AICF", "◇", AicfPage(config=self._config)),
            _NavEntry("DA", "◌", DaPage(config=self._config)),
            _NavEntry("Quantum", "⬡", QuantumPage(config=self._config)),
            _NavEntry("Console", "▣", ConsolePage()),
            _NavEntry("IDE", "✎", IdePage()),
            _NavEntry("Settings", "⚙", self._settings_page),
        ]
        for i, e in enumerate(self._nav_entries):
            self._stack.addWidget(e.page)
            self._sidebar.add_item(e.label, e.icon, i)
        self._sidebar.navigate.connect(self._navigate)
        self._navigate(0)
        self.refresh_header()

    def _apply_theme(self) -> None:
        self.setStyleSheet(build_stylesheet(self._theme_manager.palette()))
        self._dashboard_page.set_visual_effects(
            self._theme_manager.visual_effects(), self._theme_manager.reduced_motion()
        )

    def _navigate(self, index: int) -> None:
        self._stack.setCurrentIndexAnimated(index, reduced_motion=self._theme_manager.reduced_motion())
        self._sidebar.set_active(index)

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

    def _on_profile_changed(self, profile: RpcProfile) -> None:
        self._last_actual_chain_id = None
        self.refresh_header()
        self._wallet_page.on_profile_changed(profile)

    def _open_profiles_menu(self) -> None:
        menu = QMenu(self)
        wizard_action = menu.addAction("Setup Wizard…")
        manage_action = menu.addAction("Manage Profiles…")
        wizard_action.triggered.connect(self._open_wizard)
        manage_action.triggered.connect(self._open_profiles_dialog)
        menu.exec(self.cursor().pos())

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

    def _trigger_health_check(self) -> None:
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

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._health_timer.stop()
        event.accept()
