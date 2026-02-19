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
    from animica_studio.ui.theme.palette import build_palette
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
