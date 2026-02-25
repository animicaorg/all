"""Consolidated ENA hub page – single sidebar entry with tabbed sections."""

from __future__ import annotations

import logging
import pathlib
import time

log = logging.getLogger(__name__)

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.ena_automation_service import EnaService
from animica_studio.services.ena_store import EnaStore
from animica_studio.storage.config import Config
from animica_studio.ui.pages.checkpoints_page import CheckpointsPage
from animica_studio.ui.pages.contribute_page import ContributePage
from animica_studio.ui.pages.ena_contribute_page import EnaContributePage
from animica_studio.ui.pages.infer_page import InferPage
from animica_studio.ui.pages.publish_page import PublishPage
from animica_studio.ui.pages.train_page import TrainPage
from animica_studio.services.ena_contribution_engine import EnaContributionEngine
from animica_studio.services.ena_earnings_service import EnaEarningsService
from animica_studio.services.ena_full_auto_engine import EnaFullAutoEngine, FullAutoConfig, FullAutoState
from animica_studio.services.wallet_repository import WalletRepository
from animica_studio.storage.config import save_config


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
    TAB_ALWAYS_ON = 6

    def __init__(self, config: Config, service: EnaService | None = None, contrib_engine: EnaContributionEngine | None = None, full_auto_engine: EnaFullAutoEngine | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._service = service or EnaService(config, EnaStore())
        self._contrib_engine = contrib_engine
        self._full_auto_engine = full_auto_engine
        self._cap_label = QLabel("Checking capabilities...")
        self._status_label = QLabel()

        self._tabs = QTabWidget(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tabs)

        self._tabs.addTab(self._build_overview(), "Overview")
        self._tabs.addTab(ContributePage(self._service), "Contribute")
        self._tabs.addTab(CheckpointsPage(self._service), "Checkpoints")
        self._tabs.addTab(TrainPage(config), "Train")
        self._tabs.addTab(PublishPage(self._service), "Publish")
        self._tabs.addTab(InferPage(config), "Infer")
        if self._contrib_engine is not None:
            self._tabs.addTab(EnaContributePage(config, self._contrib_engine), "ENA Contribute")

        self._refresh_capabilities()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_overview(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.addWidget(QLabel("ENA Guided Automation"))
        if self._full_auto_engine is not None:
            root.addWidget(EnaFullAutoPanel(self._config, self._full_auto_engine))
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


class EnaFullAutoPanel(QGroupBox):
    def __init__(self, config: Config, engine: EnaFullAutoEngine, parent: QWidget | None = None) -> None:
        super().__init__("FULL AUTO", parent)
        self._config = config
        self._engine = engine
        self._wallets = WalletRepository()
        self._earnings = EnaEarningsService(config.get_active_profile().node.rpc_local_url, self)
        self._build_ui()
        self._load_settings()
        self._engine.stateChanged.connect(self._on_state)
        self._engine.progressUpdated.connect(self._on_progress)
        self._engine.logLine.connect(self._on_log)
        self._earnings.earningsUpdated.connect(self._on_earnings)
        self._earnings.logLine.connect(self._on_log)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        self._toggle = QPushButton("FULL AUTO (Train + Publish + Sync)")
        self._toggle.clicked.connect(self._toggle_start_stop)
        root.addWidget(self._toggle)

        status_row = QHBoxLayout()
        self._mode = QLabel("Mode: IDLE")
        self._step = QLabel("Step: IDLE")
        self._version = QLabel("Version: -")
        status_row.addWidget(self._mode)
        status_row.addWidget(self._step)
        status_row.addWidget(self._version)
        root.addLayout(status_row)
        self._times = QLabel("Last upload: - | Last sync: -")
        root.addWidget(self._times)

        form = QFormLayout()
        self._wallet_combo = QComboBox()
        self._wallet_combo.addItem("Manual", "")
        for w in self._wallets.load_wallets():
            self._wallet_combo.addItem(f"{w.label} ({w.address[:10]}…)", w.address)
        self._wallet_combo.currentIndexChanged.connect(self._wallet_selected)
        self._addr = QLineEdit()
        self._addr.setPlaceholderText("anim1...")
        self._addr.editingFinished.connect(self._save_settings)
        self._intensity = QComboBox()
        for k in ["low", "medium", "high", "max"]:
            self._intensity.addItem(k.capitalize(), k)
        self._intensity.currentIndexChanged.connect(self._save_settings)
        self._upload_steps = QSpinBox(); self._upload_steps.setRange(100, 1_000_000); self._upload_steps.setValue(5000)
        self._upload_minutes = QSpinBox(); self._upload_minutes.setRange(1, 1440); self._upload_minutes.setValue(15)
        self._sync_minutes = QSpinBox(); self._sync_minutes.setRange(1, 1440); self._sync_minutes.setValue(30)
        self._rule = QComboBox(); self._rule.addItem("latest", "latest"); self._rule.addItem("best (lowest eval loss)", "best"); self._rule.addItem("majority tip", "majority")
        self._channel = QLineEdit("ena-main")
        self._namespace = QLineEdit("0")
        self._require_upload = QCheckBox("Require DA uploads enabled")
        self._auto_fallback = QCheckBox("Auto-fallback when allow_remote_put=false")
        self._auto_fallback.setChecked(True)
        self._keep_last_k = QSpinBox(); self._keep_last_k.setRange(1, 100); self._keep_last_k.setValue(5)
        self._max_daily_min = QSpinBox(); self._max_daily_min.setRange(1, 24 * 60); self._max_daily_min.setValue(24 * 60)
        for w in [self._upload_steps, self._upload_minutes, self._sync_minutes, self._rule, self._channel, self._namespace, self._require_upload, self._auto_fallback, self._keep_last_k, self._max_daily_min]:
            if hasattr(w, 'valueChanged'):
                w.valueChanged.connect(self._save_settings)
            if hasattr(w, 'currentIndexChanged'):
                w.currentIndexChanged.connect(self._save_settings)
            if hasattr(w, 'editingFinished'):
                w.editingFinished.connect(self._save_settings)
            if hasattr(w, 'toggled'):
                w.toggled.connect(self._save_settings)
        form.addRow("Payout address:", self._addr)
        form.addRow("Wallet:", self._wallet_combo)
        form.addRow("Intensity:", self._intensity)
        form.addRow("Upload every N steps:", self._upload_steps)
        form.addRow("Upload every N minutes:", self._upload_minutes)
        form.addRow("Sync every N minutes:", self._sync_minutes)
        form.addRow("Selection rule:", self._rule)
        form.addRow("Model channel:", self._channel)
        form.addRow("DA namespace:", self._namespace)
        form.addRow("Keep last K checkpoints:", self._keep_last_k)
        form.addRow("Max daily training minutes:", self._max_daily_min)
        form.addRow("", self._require_upload)
        form.addRow("", self._auto_fallback)
        root.addLayout(form)

        self._progress = QLabel("steps=0 loss=- sps=-")
        root.addWidget(self._progress)
        self._upload_progress = QLabel("Upload: idle")
        self._sync_progress = QLabel("Sync: idle")
        root.addWidget(self._upload_progress)
        root.addWidget(self._sync_progress)

        earn_box = QGroupBox("Earnings")
        earn_layout = QVBoxLayout(earn_box)
        self._earnings_label = QLabel("Address: -\nANM earned today/session: - / -\nAICF claimable: -\nLast claim: -")
        earn_layout.addWidget(self._earnings_label)
        earn_btns = QHBoxLayout()
        copy_addr = QPushButton("Copy address")
        copy_addr.clicked.connect(lambda: self._addr.selectAll() or self._addr.copy())
        claim_btn = QPushButton("Claim now")
        claim_btn.clicked.connect(self._earnings.claim_now)
        explorer_btn = QPushButton("View on explorer")
        explorer_btn.clicked.connect(self._view_on_explorer)
        earn_btns.addWidget(copy_addr); earn_btns.addWidget(claim_btn); earn_btns.addWidget(explorer_btn)
        earn_layout.addLayout(earn_btns)
        root.addWidget(earn_box)

        controls = QHBoxLayout()
        pause = QPushButton("Pause"); pause.clicked.connect(self._engine.pause)
        resume = QPushButton("Resume"); resume.clicked.connect(self._engine.resume)
        stop = QPushButton("Stop"); stop.clicked.connect(self._engine.stop)
        diag = QPushButton("Copy diagnostics"); diag.clicked.connect(self._copy_diag)
        controls.addWidget(pause); controls.addWidget(resume); controls.addWidget(stop); controls.addWidget(diag)
        root.addLayout(controls)

        self._doing = QLabel("What it's doing now: idle")
        root.addWidget(self._doing)
        self._logs = QPlainTextEdit(); self._logs.setReadOnly(True); self._logs.setMaximumBlockCount(1200)
        root.addWidget(self._logs)

    def _cfg(self) -> dict:
        ena = dict(self._config.ena or {})
        full = dict(ena.get("full_auto") or {})
        full.update(
            {
                "enabled": True,
                "payout_address": self._addr.text().strip(),
                "intensity": self._intensity.currentData(),
                "upload_every_steps": int(self._upload_steps.value()),
                "upload_every_minutes": int(self._upload_minutes.value()),
                "sync_every_minutes": int(self._sync_minutes.value()),
                "selection_rule": self._rule.currentData(),
                "keep_last_k": int(self._keep_last_k.value()),
                "da_namespace": self._namespace.text().strip() or "0",
                "model_channel": self._channel.text().strip() or "ena-main",
                "require_da_uploads": self._require_upload.isChecked(),
                "auto_fallback_on_remote_put_block": self._auto_fallback.isChecked(),
                "max_daily_training_minutes": int(self._max_daily_min.value()),
            }
        )
        ena["full_auto"] = full
        self._config.ena = ena
        return full

    def _load_settings(self) -> None:
        full = dict((self._config.ena or {}).get("full_auto") or {})
        self._addr.setText(str(full.get("payout_address") or ""))
        self._set_combo(self._intensity, str(full.get("intensity") or "medium"))
        self._upload_steps.setValue(int(full.get("upload_every_steps") or 5000))
        self._upload_minutes.setValue(int(full.get("upload_every_minutes") or 15))
        self._sync_minutes.setValue(int(full.get("sync_every_minutes") or 30))
        self._set_combo(self._rule, str(full.get("selection_rule") or "latest"))
        self._channel.setText(str(full.get("model_channel") or "ena-main"))
        self._namespace.setText(str(full.get("da_namespace") or "0"))
        self._require_upload.setChecked(bool(full.get("require_da_uploads", False)))
        self._auto_fallback.setChecked(bool(full.get("auto_fallback_on_remote_put_block", True)))
        self._keep_last_k.setValue(int(full.get("keep_last_k") or 5))
        self._max_daily_min.setValue(int(full.get("max_daily_training_minutes") or 24 * 60))

    def _set_combo(self, combo: QComboBox, value: str) -> None:
        idx = combo.findData(value)
        combo.setCurrentIndex(max(0, idx))

    def _wallet_selected(self) -> None:
        address = str(self._wallet_combo.currentData() or "").strip()
        if address:
            self._addr.setText(address)
            self._save_settings()

    def _save_settings(self) -> None:
        full = self._cfg()
        save_config(self._config)
        self._engine.apply_config(FullAutoConfig(**full), self._config.get_active_profile().node.rpc_local_url)
        self._earnings.configure(
            rpc_url=self._config.get_active_profile().node.rpc_local_url,
            address=str(full.get("payout_address") or ""),
        )

    def _toggle_start_stop(self) -> None:
        if self._engine.state in {FullAutoState.TRAINING, FullAutoState.STARTING, FullAutoState.PUBLISHING, FullAutoState.SYNCING}:
            self._engine.stop()
            self._earnings.stop()
            self._toggle.setText("FULL AUTO (Train + Publish + Sync)")
            return
        self._save_settings()
        self._engine.start()
        self._earnings.start()
        self._toggle.setText("Stop FULL AUTO")

    def _on_state(self, state: str, detail: str) -> None:
        snap = self._engine.snapshot
        self._mode.setText(f"Mode: {state}")
        self._step.setText(f"Step: {detail}")
        self._version.setText(f"Version: {snap.model_version}")
        lu = time.strftime("%H:%M:%S", time.localtime(snap.last_upload_time)) if snap.last_upload_time else "-"
        ls = time.strftime("%H:%M:%S", time.localtime(snap.last_sync_time)) if snap.last_sync_time else "-"
        self._times.setText(f"Last upload: {lu} | Last sync: {ls}")
        self._doing.setText(f"What it's doing now: {detail}")

    def _on_progress(self, payload: dict) -> None:
        kind = payload.get("kind")
        if kind == "training":
            self._progress.setText(
                f"steps={payload.get('step')} loss={payload.get('loss')} sps={payload.get('steps_per_sec')} countdown={payload.get('checkpoint_countdown_steps')}"
            )
        elif kind == "upload":
            self._upload_progress.setText(
                f"Upload: {payload.get('chunks_done')}/{payload.get('chunks_total')} commitment={payload.get('latest_commitment')}"
            )
        elif kind == "sync":
            self._sync_progress.setText(
                f"Sync: version={payload.get('current_version')} progress={payload.get('bytes_done')}/{payload.get('bytes_total')}"
            )

    def _on_log(self, kind: str, text: str) -> None:
        self._logs.appendPlainText(f"[{kind}] {text}")

    def _on_earnings(self, snap) -> None:
        claim_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(snap.last_claim_time)) if snap.last_claim_time else "-"
        self._earnings_label.setText(
            f"Address: {snap.address or '-'}\nANM earned today/session: {snap.today_delta_wei} / {snap.session_delta_wei}\n"
            f"AICF claimable: {snap.claimable_credits}\nLast claim: {claim_at}"
        )

    def _copy_diag(self) -> None:
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.clipboard().setText(self._engine.copy_diagnostics())
        self._on_log("system", "diagnostics copied")

    def _view_on_explorer(self) -> None:
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        base = str((self._config.wallet_settings or {}).get("explorer_base_url") or "https://explorer.animica.org").rstrip("/")
        addr = self._addr.text().strip()
        if addr:
            QDesktopServices.openUrl(QUrl(f"{base}/address/{addr}"))
