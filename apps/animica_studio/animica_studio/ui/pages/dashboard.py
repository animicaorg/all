"""Dashboard page — modern hero + status cards."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from animica_studio.ui.components.primitives import Card, SectionHeader, SkeletonLoader
from animica_studio.ui.effects.hero import HeroVisual


class DashboardPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(SectionHeader("Dashboard", "Overview of wallet, network, and latest activity."))

        self.hero = HeroVisual(mode="balanced")
        hero_card = Card()
        hero_card.layout().addWidget(self.hero)
        layout.addWidget(hero_card)

        status = Card()
        status.layout().addWidget(QLabel("Balances loading"))
        status.layout().addWidget(SkeletonLoader(400, 12))
        status.layout().addWidget(SkeletonLoader(320, 12))
        layout.addWidget(status)
        layout.addStretch()

    def set_visual_effects(self, mode: str, reduced_motion: bool) -> None:
        self.hero.set_effect_mode(mode, reduced_motion)
