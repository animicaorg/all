"""Dashboard page — modern hero + status cards with network summary."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from animica_studio.ui.components.primitives import (
    Badge,
    Card,
    EmptyState,
    SectionHeader,
    SkeletonLoader,
)
from animica_studio.ui.effects.hero import HeroVisual


class DashboardPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addWidget(SectionHeader("Dashboard", "Overview of wallet, network, and latest activity."))

        # Hero section
        self.hero = HeroVisual(mode="balanced")
        hero_card = Card()
        hero_card.layout().setContentsMargins(0, 0, 0, 0)
        hero_card.layout().addWidget(self.hero)
        layout.addWidget(hero_card)

        # Status row
        row = QHBoxLayout()
        row.setSpacing(12)

        status_card = Card()
        status_card.layout().addWidget(QLabel("Network status"))
        badge_row = QHBoxLayout()
        self._status_badge = Badge("● Checking…")
        badge_row.addWidget(self._status_badge)
        badge_row.addStretch()
        status_card.layout().addLayout(badge_row)
        row.addWidget(status_card, 1)

        wallet_card = Card()
        wallet_card.layout().addWidget(QLabel("Total Balance"))
        self._balance_skel = SkeletonLoader(140, 14)
        wallet_card.layout().addWidget(self._balance_skel)
        row.addWidget(wallet_card, 1)

        layout.addLayout(row)

        # Recent activity placeholder
        activity_card = Card()
        activity_card.layout().addWidget(QLabel("Recent Activity"))
        activity_card.layout().addWidget(SkeletonLoader(360, 12))
        activity_card.layout().addWidget(SkeletonLoader(280, 12))
        activity_card.layout().addWidget(SkeletonLoader(320, 12))
        layout.addWidget(activity_card)

        layout.addStretch()

    def set_visual_effects(self, mode: str, reduced_motion: bool) -> None:
        self.hero.set_effect_mode(mode, reduced_motion)
