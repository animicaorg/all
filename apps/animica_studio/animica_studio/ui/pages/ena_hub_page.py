"""Consolidated ENA hub page – single sidebar entry with tabbed sections."""

from __future__ import annotations

import logging
import pathlib

log = logging.getLogger(__name__)

from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.ena_automation_service import EnaService
from animica_studio.services.ena_store import EnaStore
from animica_studio.storage.config import Config
from animica_studio.ui.pages.checkpoints_page import CheckpointsPage
from animica_studio.ui.pages.contribute_page import ContributePage
from animica_studio.ui.pages.infer_page import InferPage
from animica_studio.ui.pages.publish_page import PublishPage
from animica_studio.ui.pages.train_page import TrainPage


class EnaHubPage(QWidget):
    """Single consolidated ENA page with tabbed sections.

    Tabs
    ----
    0 – Overview    : capabilities display + quick-launch shortcuts
    1 – Contribute  : CPU contribution flow
    2 – Checkpoints : fetch/verify checkpoints
    3 – Train       : local training
    4 – Publish     : publish checkpoint to DA network
    5 – Infer       : run inference
    """

    TAB_OVERVIEW = 0
    TAB_CONTRIBUTE = 1
    TAB_CHECKPOINTS = 2
    TAB_TRAIN = 3
    TAB_PUBLISH = 4
    TAB_INFER = 5

    def __init__(self, config: Config, service: EnaService | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service or EnaService(config, EnaStore())
        self._cap_label = QLabel("Checking capabilities...")
        self._status_label = QLabel()

        self._tabs = QTabWidget(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tabs)

        self._tabs.addTab(self._build_overview(), "Overview")
        self._tabs.addTab(ContributePage(self._service), "Contribute")
        self._tabs.addTab(CheckpointsPage(self._service), "Checkpoints")
        self._tabs.addTab(TrainPage(self._service), "Train")
        self._tabs.addTab(PublishPage(self._service), "Publish")
        self._tabs.addTab(InferPage(self._service), "Infer")

        self._refresh_capabilities()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_overview(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.addWidget(QLabel("ENA Guided Automation"))
        root.addWidget(self._cap_label)
        for label, tab_idx in [
            ("Contribute (CPU)", self.TAB_CONTRIBUTE),
            ("Watch & Fetch Checkpoints", self.TAB_CHECKPOINTS),
            ("Train Locally (CPU)", self.TAB_TRAIN),
            ("Publish to Network (DA)", self.TAB_PUBLISH),
            ("Use ENA (Inference)", self.TAB_INFER),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, i=tab_idx: self._tabs.setCurrentIndex(i))
            root.addWidget(btn)
        auto_btn = QPushButton("Auto mode")
        auto_btn.clicked.connect(self._run_auto)
        root.addWidget(auto_btn)
        root.addWidget(self._status_label)
        root.addStretch(1)
        return w

    def _refresh_capabilities(self) -> None:
        try:
            caps = self._service.detect_capabilities()
            self._cap_label.setText(
                f"Capabilities: AICF={caps.get('aicf')} DA={caps.get('da')} ENA={caps.get('ena')}"
            )
        except Exception:  # noqa: BLE001
            log.debug("ENA capability detection failed", exc_info=True)
            self._cap_label.setText("Capabilities: unavailable")

    def _run_auto(self) -> None:
        try:
            out = self._service.run_auto_mode(pathlib.Path.cwd())
            self._status_label.setText(
                f"Auto mode done. Active checkpoint: {out.get('active_checkpoint', {}).get('id', 'n/a')}"
            )
        except Exception as exc:  # noqa: BLE001
            self._status_label.setText(f"Auto mode error: {str(exc)}")

    # ------------------------------------------------------------------
    # Qt event overrides
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # type: ignore[override]
        """Refresh capability display whenever the page becomes visible."""
        super().showEvent(event)
        self._refresh_capabilities()

    # ------------------------------------------------------------------
    # Public API for deep-linking
    # ------------------------------------------------------------------

    def navigate_to_tab(self, tab_index: int) -> None:
        """Navigate to a specific tab by index."""
        if 0 <= tab_index < self._tabs.count():
            self._tabs.setCurrentIndex(tab_index)
