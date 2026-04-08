"""Studio Home page with cross-service status, warnings, and quick actions."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from animica_studio.models.studio_models import FeatureSummary, StudioSnapshot
from animica_studio.services.activity_store import ActivityStore
from animica_studio.services.studio_status_service import StudioStatusService
from animica_studio.ui.components.primitives import Badge, Card, EmptyState, SectionHeader
from animica_studio.util.qt import ui_thread_only
from animica_studio.services.workers import run_in_threadpool

log = logging.getLogger(__name__)


class DashboardPage(QWidget):
    action_requested = Signal(str)

    def __init__(
        self,
        config: Any = None,
        profile_service: Any = None,
        status_service: StudioStatusService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._profile_service = profile_service
        self._status_service = status_service or StudioStatusService(config)
        self._refresh_job = None
        self._started = False
        self._snapshot = StudioSnapshot()
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)
        root.addWidget(SectionHeader("Home", "See what needs attention and jump into the next useful step."))

        hero = Card()
        hero.layout().setSpacing(10)
        top_row = QHBoxLayout()
        title_box = QVBoxLayout()
        self._hero_title = QLabel("Animica Studio")
        self._hero_title.setStyleSheet("font-size: 28px; font-weight: 700;")
        self._hero_subtitle = QLabel("Checking wallet and node status…")
        self._hero_subtitle.setStyleSheet("color: #8f99a5;")
        title_box.addWidget(self._hero_title)
        title_box.addWidget(self._hero_subtitle)
        top_row.addLayout(title_box, 1)
        self._hero_badge = Badge("Loading")
        top_row.addWidget(self._hero_badge, alignment=Qt.AlignmentFlag.AlignTop)
        hero.layout().addLayout(top_row)

        self._hero_metrics = QLabel("Wallets: — | Balance: — | Sync: — | Peers: —")
        self._hero_metrics.setStyleSheet("font-size: 15px; font-weight: 600;")
        hero.layout().addWidget(self._hero_metrics)
        root.addWidget(hero)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        self._wallet_card = self._make_status_card("Wallet", "Loading…")
        self._node_card = self._make_status_card("Node", "Loading…")
        self._mining_card = self._make_status_card("Mining", "Loading…")
        self._ena_card = self._make_status_card("ENA", "Loading…")
        self._aicf_card = self._make_status_card("AICF", "Loading…")
        self._da_card = self._make_status_card("DA", "Loading…")

        cards = [
            self._wallet_card,
            self._node_card,
            self._mining_card,
            self._ena_card,
            self._aicf_card,
            self._da_card,
        ]
        for index, card in enumerate(cards):
            grid.addWidget(card, index // 3, index % 3)
        root.addLayout(grid)

        lower = QHBoxLayout()
        lower.setSpacing(12)

        quick = Card()
        quick.layout().addWidget(QLabel("Quick Actions"))
        for label, action_id in [
            ("Create Wallet", "wallet_create"),
            ("Receive", "wallet_receive"),
            ("Send", "wallet_send"),
            ("Start Node", "node_start"),
            ("Start Mining", "mining_open"),
            ("Open ENA", "ena_open"),
            ("Open Logs", "logs_open"),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _checked=False, action=action_id: self.action_requested.emit(action))
            quick.layout().addWidget(btn)
        quick.layout().addStretch(1)
        lower.addWidget(quick, 1)

        self._warnings_card = Card()
        self._warnings_card.layout().addWidget(QLabel("Action Needed"))
        self._warnings_box = QVBoxLayout()
        self._warnings_box.setSpacing(6)
        self._warnings_card.layout().addLayout(self._warnings_box)
        lower.addWidget(self._warnings_card, 2)
        root.addLayout(lower)

        activity = Card()
        header = QHBoxLayout()
        header.addWidget(QLabel("Recent Activity"))
        header.addStretch()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_snapshot)
        header.addWidget(refresh_btn)
        activity.layout().addLayout(header)
        self._activity_box = QVBoxLayout()
        self._activity_box.setSpacing(6)
        activity.layout().addLayout(self._activity_box)
        root.addWidget(activity)
        root.addStretch(1)

    def _make_status_card(self, title: str, detail: str) -> Card:
        card = Card()
        card.layout().setSpacing(6)
        heading = QLabel(title)
        heading.setStyleSheet("font-size: 16px; font-weight: 700;")
        card.layout().addWidget(heading)
        badge = Badge("Loading")
        detail_label = QLabel(detail)
        detail_label.setWordWrap(True)
        detail_label.setStyleSheet("color: #8f99a5;")
        card.layout().addWidget(badge)
        card.layout().addWidget(detail_label)
        card._status_badge = badge  # type: ignore[attr-defined]
        card._detail_label = detail_label  # type: ignore[attr-defined]
        return card

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        if not self._started:
            self._started = True
            self._timer = QTimer(self)
            self._timer.setInterval(20_000)
            self._timer.timeout.connect(self.refresh_snapshot)
            self._timer.start()
            QTimer.singleShot(0, self.refresh_snapshot)

    def refresh_snapshot(self) -> None:
        if self._refresh_job is not None:
            return
        self._refresh_job = run_in_threadpool(self._status_service.collect_snapshot)
        self._refresh_job.signals.result.connect(self._apply_snapshot)
        self._refresh_job.signals.error.connect(self._on_refresh_error)
        self._refresh_job.signals.finished.connect(lambda: setattr(self, "_refresh_job", None))

    @ui_thread_only(log)
    def _apply_snapshot(self, snapshot: StudioSnapshot) -> None:
        self._snapshot = snapshot
        node_state = snapshot.node.sync.state or "UNKNOWN"
        badge_label = "Ready" if snapshot.node.rpc_reachable else "Needs setup"
        if snapshot.issues:
            badge_label = f"{len(snapshot.issues)} issue(s)"
        self._hero_badge.setText(badge_label)
        self._hero_title.setText(snapshot.profile_name or "Animica Studio")
        self._hero_subtitle.setText(
            f"{snapshot.network_name or 'Network unknown'} | {snapshot.rpc_url or 'RPC not configured'}"
        )
        self._hero_metrics.setText(
            "Wallets: {wallets} | Balance: {balance} | Sync: {sync} | Peers: {peers}".format(
                wallets=snapshot.wallet.wallet_count,
                balance=snapshot.wallet.total_balance_text,
                sync=node_state,
                peers=snapshot.node.peer_count if snapshot.node.peer_count is not None else "—",
            )
        )
        self._update_card(
            self._wallet_card,
            "Ready" if snapshot.wallet.wallet_count else "No wallet",
            f"{snapshot.wallet.selected_label or 'No selected wallet'} | {snapshot.wallet.selected_balance_text}",
        )
        self._update_card(
            self._node_card,
            "Online" if snapshot.node.rpc_reachable else ("Running" if snapshot.node.running else "Offline"),
            snapshot.node.sync.detail or (snapshot.node.last_error or "RPC is unreachable."),
        )
        self._update_feature_card(self._mining_card, snapshot.mining)
        self._update_feature_card(self._ena_card, snapshot.ena)
        self._update_feature_card(self._aicf_card, snapshot.aicf)
        self._update_feature_card(self._da_card, snapshot.da)
        self._render_warnings(snapshot)
        self._render_activity()

    @ui_thread_only(log)
    def _on_refresh_error(self, message: str, _traceback: str) -> None:
        self._hero_badge.setText("Error")
        self._hero_subtitle.setText(message)

    def _update_card(self, card: Card, badge_text: str, detail: str) -> None:
        card._status_badge.setText(badge_text)  # type: ignore[attr-defined]
        card._detail_label.setText(detail)  # type: ignore[attr-defined]

    def _update_feature_card(self, card: Card, feature: FeatureSummary) -> None:
        badge = feature.state.replace("_", " ").title()
        detail = feature.detail
        if feature.warning:
            detail = f"{detail}\n{feature.warning}"
        self._update_card(card, badge, detail)

    def _render_warnings(self, snapshot: StudioSnapshot) -> None:
        self._clear_layout(self._warnings_box)
        if not snapshot.issues:
            self._warnings_box.addWidget(EmptyState("✓", "No urgent issues", "Core services look healthy."))
            return
        for issue in snapshot.issues[:5]:
            label = QLabel(f"{issue.title}\n{issue.detail}".strip())
            label.setWordWrap(True)
            label.setStyleSheet("padding: 8px 10px; border: 1px solid #2e3947; border-radius: 10px;")
            self._warnings_box.addWidget(label)

    def _render_activity(self) -> None:
        self._clear_layout(self._activity_box)
        entries = ActivityStore.instance().get_recent(6)
        if not entries:
            self._activity_box.addWidget(EmptyState("⌛", "No recent activity", "Use Refresh, Start Node, or Create Wallet to begin."))
            return
        for entry in entries:
            label = QLabel(f"{entry.status_badge} {entry.summary}  •  {entry.age_label}")
            label.setWordWrap(True)
            if entry.detail:
                label.setToolTip(entry.detail)
            self._activity_box.addWidget(label)

    def set_visual_effects(self, enabled: bool, reduced_motion: bool) -> None:
        _ = enabled
        _ = reduced_motion

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
