from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from animica_studio.storage.config import Config
from animica_studio.services.profile_service import ProfileService
from animica_studio.ui.main_window import MainWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def test_main_window_smoke() -> None:
    app = _app()
    cfg = Config()
    service = ProfileService(cfg)
    window = MainWindow(cfg, service)
    assert window.windowTitle() == "Animica Studio"
    window.close()
    app.quit()


def test_main_window_sync_status_bar_tracks_blocks_and_rate() -> None:
    _app()
    cfg = Config()
    service = ProfileService(cfg)
    window = MainWindow(cfg, service)

    window._on_health_result(  # noqa: SLF001
        {
            "ok": True,
            "chain_id": 1,
            "sync_state": "syncing",
            "sync_progress_pct": 50.0,
            "sync_current_height": 100,
            "sync_network_height": 200,
            "sample_ts": 10.0,
        }
    )
    assert window._sync_state_label.text() == "Sync: Syncing (50.0%)"  # noqa: SLF001
    assert window._sync_blocks_label.text() == "Blocks: local 100 / network 200"  # noqa: SLF001
    assert window._sync_rate_label.text() == "Rate: — blk/s"  # noqa: SLF001

    window._on_health_result(  # noqa: SLF001
        {
            "ok": True,
            "chain_id": 1,
            "sync_state": "syncing",
            "sync_progress_pct": 60.0,
            "sync_current_height": 120,
            "sync_network_height": 200,
            "sample_ts": 15.0,
        }
    )
    assert window._sync_rate_label.text() == "Rate: 4.00 blk/s"  # noqa: SLF001

    window.close()


def test_components_instantiate() -> None:
    """Verify all design-system primitives can be constructed."""
    _app()
    from animica_studio.ui.components.primitives import (
        Badge,
        Card,
        EmptyState,
        IconButton,
        InlineError,
        PrimaryButton,
        SecondaryButton,
        SectionHeader,
        SkeletonLoader,
        ThemedButton,
        Toast,
    )

    card = Card()
    assert card is not None

    header = SectionHeader("Title", "Subtitle")
    assert header is not None

    badge = Badge("v1.0")
    assert badge.text() == "v1.0"

    primary = PrimaryButton("Submit")
    assert primary.property("variant") == "primary"

    secondary = SecondaryButton("Cancel")
    assert secondary.property("variant") == "secondary"

    icon_btn = IconButton("⚙", tooltip="Settings")
    assert icon_btn.property("variant") == "icon"
    assert icon_btn.toolTip() == "Settings"

    themed = ThemedButton("OK", "primary")
    assert themed.property("variant") == "primary"

    error = InlineError("Something went wrong", details="stack trace here")
    assert error.objectName() == "InlineError"

    empty = EmptyState("📭", "No items", "Add one to get started")
    assert empty is not None

    skel = SkeletonLoader(200, 18)
    assert skel.width() == 200

    toast = Toast(card, "Hello world")
    assert toast.objectName() == "Toast"


def test_theme_system() -> None:
    """ThemeManager persists and emits changes correctly."""
    from animica_studio.ui.theme.theme_manager import ThemeManager
    from animica_studio.ui.theme.stylesheet import build_stylesheet

    cfg = Config()
    mgr = ThemeManager(cfg)

    # Default dark mode
    assert mgr.mode() == "dark"
    palette = mgr.palette()
    assert palette.mode == "dark"

    # Switch to light and back
    mgr.set_mode("light")
    assert mgr.mode() == "light"
    mgr.set_mode("dark")
    assert mgr.mode() == "dark"

    # Stylesheet builds without error
    ss = build_stylesheet(palette)
    assert "border-radius" in ss
    assert "#0f1522" in ss  # dark bg colour


def test_hero_visual_modes() -> None:
    """HeroVisual can switch modes without raising."""
    _app()
    from animica_studio.ui.effects.hero import HeroVisual

    hero = HeroVisual(mode="balanced", reduced_motion=False)
    hero.resize(400, 200)

    hero.set_effect_mode("off", False)
    hero.set_effect_mode("high", False)
    hero.set_effect_mode("balanced", True)  # reduced motion


def test_sidebar_toggle() -> None:
    """Sidebar toggle changes width and emits signal."""
    _app()
    from animica_studio.ui.shell.sidebar import Sidebar

    sidebar = Sidebar()
    sidebar.add_item("Dashboard", "◈", 0)
    sidebar.add_item("Wallet", "◉", 1)
    sidebar.set_active(0)

    assert sidebar.width() == Sidebar._EXPANDED_W
    sidebar.toggle(animate=False)
    assert sidebar.width() == Sidebar._COLLAPSED_W
    sidebar.toggle(animate=False)
    assert sidebar.width() == Sidebar._EXPANDED_W


def test_command_palette_filter() -> None:
    """CommandPalette filters items correctly."""
    _app()
    from animica_studio.ui.shell.command_palette import CommandPalette

    palette = CommandPalette(["Dashboard", "Wallet", "Mining", "Settings"])
    # After filtering by 'et', only 'Wallet' and 'Settings' should show
    palette._refilter("et")
    assert palette._list.count() == 2
    palette._refilter("")
    assert palette._list.count() == 4


def test_top_level_imports_smoke() -> None:
    """Top-level module imports should not crash at import-time."""
    import importlib

    app_mod = importlib.import_module("animica_studio.app")
    wallet_page_mod = importlib.import_module("animica_studio.ui.pages.wallet_page")

    assert app_mod is not None
    assert wallet_page_mod is not None


def test_console_page_smoke() -> None:
    _app()
    from animica_studio.storage.config import Config
    from animica_studio.ui.pages.console_page import ConsolePage

    page = ConsolePage(config=Config())
    assert page is not None
    page.close()


def test_create_wallet_dialog_label_validation() -> None:
    _app()
    from animica_studio.ui.pages.wallet_page import _CreateWalletDialog

    dlg = _CreateWalletDialog()

    # Empty label => blocked
    dlg._label_edit.setText("   ")
    assert not dlg._create_btn.isEnabled()

    # Invalid chars => blocked
    dlg._label_edit.setText("bad/label")
    assert not dlg._create_btn.isEnabled()

    # Valid label => enabled
    dlg._label_edit.setText("wallet_01")
    assert dlg._create_btn.isEnabled()

    dlg.close()


def test_setup_wizard_smoke() -> None:
    _app()
    from animica_studio.ui.wizard.wizard_window import SetupWizard

    cfg = Config()
    service = ProfileService(cfg)
    wizard = SetupWizard(service)
    assert wizard.windowTitle() == "Animica Studio Setup"
    wizard.close()


def test_setup_wizard_verification_waits_for_local_node_rpc(monkeypatch) -> None:
    from animica_studio.models.studio_models import OnboardingProbe, StudioSnapshot
    from animica_studio.services.studio_status_service import ServiceActionResult
    from animica_studio.ui.wizard.wizard_window import _run_verification

    class _FakeStatusService:
        def __init__(self) -> None:
            self.probe_calls = 0

        def start_node(self) -> ServiceActionResult:
            return ServiceActionResult(True, "Node start requested.")

        def collect_snapshot(self) -> StudioSnapshot:
            return StudioSnapshot(rpc_url="http://127.0.0.1:8545/rpc")

        def probe_onboarding(self) -> OnboardingProbe:
            self.probe_calls += 1
            return OnboardingProbe(
                has_wallet=True,
                node_running=True,
                rpc_reachable=self.probe_calls >= 3,
            )

    svc = _FakeStatusService()
    monkeypatch.setattr("animica_studio.ui.wizard.wizard_window.time.sleep", lambda _s: None)
    result = _run_verification(svc, start_local_node=True)

    assert svc.probe_calls >= 3
    assert isinstance(result["probe"], OnboardingProbe)
    assert result["probe"].rpc_reachable is True
    assert isinstance(result["start_result"], ServiceActionResult)
    assert result["start_result"].ok is True
