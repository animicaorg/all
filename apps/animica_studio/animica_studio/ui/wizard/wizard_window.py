"""Beginner-first setup wizard for Animica Studio."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.models.profile_models import ProfileType, RpcProfile
from animica_studio.models.studio_models import OnboardingProbe, StudioSnapshot
from animica_studio.services.profile_service import ProfileService
from animica_studio.services.settings_service import SettingsService
from animica_studio.services.studio_status_service import ServiceActionResult, StudioStatusService
from animica_studio.services.wallet_repository import WalletRepository
from animica_studio.services.workers import WorkerThread
from animica_studio.storage.config import Config
from animica_studio.util.paths import animica_wallets_file, default_chain_data_dir

log = logging.getLogger(__name__)


def _make_header(title: str, subtitle: str) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(4)
    title_label = QLabel(title)
    title_label.setStyleSheet("font-size: 24px; font-weight: 700;")
    subtitle_label = QLabel(subtitle)
    subtitle_label.setWordWrap(True)
    subtitle_label.setStyleSheet("color: #8f99a5;")
    layout.addWidget(title_label)
    layout.addWidget(subtitle_label)
    return layout


def _status_style(level: str) -> str:
    colors = {
        "info": "#8f99a5",
        "success": "#9cd67a",
        "warning": "#f3c86a",
        "error": "#f18787",
    }
    return f"color: {colors.get(level, colors['info'])}; font-size: 12px;"


def _auto_profile_name(network_label: str, local_node: bool) -> str:
    suffix = "Local Node" if local_node else "Remote RPC"
    return f"{network_label} {suffix}"


def _run_verification(status_service: StudioStatusService, *, start_local_node: bool) -> dict[str, Any]:
    start_result = None
    if start_local_node:
        start_result = status_service.start_node()
        if isinstance(start_result, ServiceActionResult) and start_result.ok:
            deadline = time.time() + 45.0
            while time.time() < deadline:
                probe = status_service.probe_onboarding()
                if probe.rpc_reachable:
                    break
                time.sleep(1.0)
    snapshot = status_service.collect_snapshot()
    probe = status_service.probe_onboarding()
    if (
        isinstance(start_result, ServiceActionResult)
        and start_result.ok
        and not probe.rpc_reachable
    ):
        wait_note = "RPC is still unreachable after waiting for local node startup."
        start_result.details = "\n".join(part for part in [start_result.details, wait_note] if part).strip()
    return {
        "probe": probe,
        "snapshot": snapshot,
        "start_result": start_result,
    }


class _NetworkPage(QWidget):
    def __init__(self, settings_service: SettingsService, profile: RpcProfile) -> None:
        super().__init__()
        self._settings = settings_service
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 18)
        layout.setSpacing(14)
        layout.addLayout(
            _make_header(
                "Welcome to Animica Studio",
                "Pick the network you want to use first. Studio will fill sensible defaults and keep advanced options out of the way.",
            )
        )

        form = QFormLayout()
        form.setVerticalSpacing(10)
        self._network = QComboBox()
        for preset in self._settings.network_presets():
            self._network.addItem(preset.label, preset.key)
        current_network = self._settings.detect_network(profile)
        index = self._network.findData(current_network)
        self._network.setCurrentIndex(max(0, index))
        form.addRow("Network", self._network)

        self._mode = QComboBox()
        self._mode.addItem("Managed local node", ProfileType.LOCAL_NODE.value)
        self._mode.addItem("External RPC", ProfileType.REMOTE_RPC.value)
        desired_mode = ProfileType.LOCAL_NODE.value if profile.type == ProfileType.LOCAL_NODE else ProfileType.REMOTE_RPC.value
        mode_index = self._mode.findData(desired_mode)
        self._mode.setCurrentIndex(max(0, mode_index))
        form.addRow("Connection", self._mode)

        self._profile_name = QLineEdit()
        self._profile_name.setPlaceholderText("Mainnet Local Node")
        self._profile_name.setText(profile.name or _auto_profile_name(self.network_label(), self.local_node()))
        form.addRow("Profile Name", self._profile_name)
        layout.addLayout(form)

        note = QLabel(
            "Beginner default: use a managed local node if you want Studio to start and monitor the node for you. "
            "Choose External RPC if you already have a working endpoint."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #8f99a5;")
        layout.addWidget(note)
        layout.addStretch(1)

        self._network.currentIndexChanged.connect(self._sync_name_if_blank)
        self._mode.currentIndexChanged.connect(self._sync_name_if_blank)

    def _sync_name_if_blank(self) -> None:
        if self._profile_name.text().strip():
            return
        self._profile_name.setText(_auto_profile_name(self.network_label(), self.local_node()))

    def network_key(self) -> str:
        return str(self._network.currentData() or "mainnet")

    def network_label(self) -> str:
        return self._network.currentText() or "Mainnet"

    def local_node(self) -> bool:
        return str(self._mode.currentData() or "") == ProfileType.LOCAL_NODE.value

    def profile_name(self) -> str:
        return self._profile_name.text().strip() or _auto_profile_name(self.network_label(), self.local_node())


class _WalletPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 18)
        layout.setSpacing(14)
        layout.addLayout(
            _make_header(
                "Set Up Your Wallet",
                "You need at least one wallet to receive funds, mine, and use most Animica features.",
            )
        )

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet("color: #8f99a5;")
        layout.addWidget(self._summary)

        self._use_existing = QRadioButton("Use the wallet(s) already on this machine")
        self._create = QRadioButton("Create a new wallet now")
        self._import = QRadioButton("Import an existing wallets.json file")
        self._use_existing.setChecked(True)
        for control in (self._use_existing, self._create, self._import):
            layout.addWidget(control)

        create_box = QFrame()
        create_box.setFrameShape(QFrame.Shape.StyledPanel)
        create_form = QFormLayout(create_box)
        self._create_label = QLineEdit()
        self._create_label.setPlaceholderText("wallet_01")
        create_form.addRow("Wallet Label", self._create_label)
        self._create_alg = QComboBox()
        self._create_alg.addItem("Dilithium3", "dilithium3")
        self._create_alg.addItem("SPHINCS+ 128s", "sphincs_shake_128s")
        create_form.addRow("Algorithm", self._create_alg)
        self._create_help = QLabel("")
        self._create_help.setWordWrap(True)
        self._create_help.setStyleSheet("color: #8f99a5;")
        create_form.addRow("", self._create_help)
        self._allow_insecure = QCheckBox("Allow insecure fallback if native PQ libraries are unavailable")
        create_form.addRow("", self._allow_insecure)
        layout.addWidget(create_box)

        import_box = QFrame()
        import_box.setFrameShape(QFrame.Shape.StyledPanel)
        import_form = QFormLayout(import_box)
        path_row = QHBoxLayout()
        self._import_path = QLineEdit()
        self._import_path.setPlaceholderText(str(animica_wallets_file()))
        path_row.addWidget(self._import_path, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_import)
        path_row.addWidget(browse)
        import_form.addRow("Wallet File", path_row)
        layout.addWidget(import_box)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(_status_style("info"))
        layout.addWidget(self._status)
        layout.addStretch(1)

        self._use_existing.toggled.connect(self._refresh_mode)
        self._create.toggled.connect(self._refresh_mode)
        self._import.toggled.connect(self._refresh_mode)
        self._create_alg.currentIndexChanged.connect(self._sync_create_help)
        self._create_box = create_box
        self._import_box = import_box
        self._wallet_count = 0
        self._sync_create_help()
        self._refresh_mode()

    def refresh_wallet_summary(self, wallets: list[Any], last_error: str | None = None) -> None:
        self._wallet_count = len(wallets)
        if wallets:
            example = str(getattr(wallets[0], "address", "") or "")
            self._summary.setText(
                f"Studio found {len(wallets)} wallet(s) in {animica_wallets_file()}.\n"
                f"First address: {example}"
            )
            self._use_existing.setEnabled(True)
            self._use_existing.setChecked(True)
        else:
            detail = last_error or "No wallets were found yet."
            self._summary.setText(
                f"Studio has not found any wallets in {animica_wallets_file()} yet.\n{detail}"
            )
            self._use_existing.setEnabled(False)
            self._create.setChecked(True)
        self._refresh_mode()

    def mode(self) -> str:
        if self._create.isChecked():
            return "create"
        if self._import.isChecked():
            return "import"
        return "existing"

    def create_label(self) -> str:
        return self._create_label.text().strip()

    def create_alg(self) -> str:
        return str(self._create_alg.currentData() or "dilithium3")

    def allow_insecure(self) -> bool:
        return self._allow_insecure.isChecked()

    def import_path(self) -> str:
        return self._import_path.text().strip()

    def wallet_count(self) -> int:
        return self._wallet_count

    def set_status(self, text: str, level: str = "info") -> None:
        self._status.setText(text)
        self._status.setStyleSheet(_status_style(level))

    def _refresh_mode(self) -> None:
        mode = self.mode()
        self._create_box.setVisible(mode == "create")
        self._import_box.setVisible(mode == "import")

    def _sync_create_help(self) -> None:
        if self.create_alg() == "dilithium3":
            self._create_help.setText("Recommended default. Balanced for normal desktop use.")
        else:
            self._create_help.setText("Heavier but more conservative. Use this only if you specifically want SPHINCS+.")

    def _browse_import(self) -> None:
        start_dir = str(Path.home())
        selected, _ = QFileDialog.getOpenFileName(self, "Select wallets.json", start_dir, "JSON Files (*.json)")
        if selected:
            self._import_path.setText(selected)


class _ConnectionPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 18)
        layout.setSpacing(14)
        self._header_box = _make_header(
            "Connect Studio to Animica",
            "Choose whether Studio should start a local node for you or connect to an already-running RPC endpoint.",
        )
        layout.addLayout(self._header_box)

        form = QFormLayout()
        form.setVerticalSpacing(10)
        self._rpc_url = QLineEdit()
        form.addRow("RPC URL", self._rpc_url)
        self._explorer_url = QLineEdit()
        self._explorer_url.setPlaceholderText("https://explorer.animica.org")
        form.addRow("Explorer URL", self._explorer_url)
        self._chain_id = QLineEdit()
        form.addRow("Chain ID", self._chain_id)
        self._node_cmd = QLineEdit()
        form.addRow("Local Node Command", self._node_cmd)
        self._data_dir = QLineEdit()
        form.addRow("Local Data Directory", self._data_dir)
        layout.addLayout(form)

        self._start_now = QCheckBox("Start the local node during verification")
        layout.addWidget(self._start_now)
        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(_status_style("info"))
        layout.addWidget(self._status)
        layout.addStretch(1)
        self._is_local_node = False

    def set_from_profile(self, profile: RpcProfile, *, network_label: str, local_node: bool) -> None:
        self._is_local_node = local_node
        title = "Configure Your Local Node" if local_node else "Connect to an Existing RPC"
        subtitle = (
            f"Studio will use the {network_label} defaults, but you can change them here."
            if local_node
            else "Enter the RPC details Studio should use. Studio will verify the endpoint before finishing."
        )
        while self._header_box.count():
            item = self._header_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        fresh_header = _make_header(title, subtitle)
        while fresh_header.count():
            item = fresh_header.takeAt(0)
            if item.widget() is not None:
                self._header_box.addWidget(item.widget())
        self._rpc_url.setText(profile.effective_rpc_url())
        self._explorer_url.setText(profile.explorer_base_url)
        self._chain_id.setText(str(profile.chain_id_expected))
        self._node_cmd.setText(" ".join(profile.node_start_cmd or ["animica", "node", "start"]))
        default_dir = profile.node_datadir or str(default_chain_data_dir(profile.chain_id_expected))
        self._data_dir.setText(default_dir)
        self._node_cmd.setVisible(local_node)
        self._data_dir.setVisible(local_node)
        self._start_now.setVisible(local_node)
        self._start_now.setChecked(local_node)
        self.set_status("", "info")

    def rpc_url(self) -> str:
        return self._rpc_url.text().strip()

    def explorer_url(self) -> str:
        return self._explorer_url.text().strip()

    def chain_id(self) -> int:
        return int(self._chain_id.text().strip() or "1")

    def node_cmd(self) -> str:
        return self._node_cmd.text().strip()

    def data_dir(self) -> str:
        return self._data_dir.text().strip()

    def start_now(self) -> bool:
        return self._is_local_node and self._start_now.isChecked()

    def set_status(self, text: str, level: str = "info") -> None:
        self._status.setText(text)
        self._status.setStyleSheet(_status_style(level))


class _VerificationPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 18)
        layout.setSpacing(14)
        layout.addLayout(
            _make_header(
                "Verify Setup",
                "Studio is checking that your wallet, connection, and sync status are usable before you land on the dashboard.",
            )
        )

        self._summary = QLabel("Verification has not started yet.")
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(self._summary)

        self._checks = QTextEdit()
        self._checks.setReadOnly(True)
        self._checks.setMinimumHeight(160)
        layout.addWidget(self._checks)

        self._issues = QTextEdit()
        self._issues.setReadOnly(True)
        self._issues.setMinimumHeight(120)
        layout.addWidget(self._issues)

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(_status_style("info"))
        layout.addWidget(self._hint)
        layout.addStretch(1)
        self._probe = OnboardingProbe()

    def set_busy(self, text: str) -> None:
        self._summary.setText(text)
        self._checks.setPlainText("Checking wallet, node, and sync state…")
        self._issues.setPlainText("")
        self._hint.setText("")

    def apply(self, probe: OnboardingProbe, snapshot: StudioSnapshot, start_result: ServiceActionResult | None = None) -> None:
        self._probe = probe
        check_lines = [
            f"Wallet ready: {'Yes' if probe.has_wallet else 'No'}",
            f"RPC reachable: {'Yes' if probe.rpc_reachable else 'No'}",
            f"Node running: {'Yes' if probe.node_running else 'No'}",
            f"Sync complete: {'Yes' if probe.sync_complete else 'Still syncing'}",
            f"Network: {probe.selected_network}",
            f"Wallets found: {probe.wallet_count}",
            f"Current RPC: {snapshot.rpc_url}",
            f"Peers: {snapshot.node.peer_count if snapshot.node.peer_count is not None else 'unknown'}",
            f"Head height: {snapshot.node.head_number if snapshot.node.head_number is not None else 'unknown'}",
        ]
        if start_result is not None:
            check_lines.append("")
            check_lines.append(f"Local node start: {start_result.summary}")
            if start_result.details:
                check_lines.append(start_result.details)
        self._checks.setPlainText("\n".join(check_lines))

        if probe.issues:
            issue_lines = []
            for issue in probe.issues:
                issue_lines.append(f"[{issue.level.upper()}] {issue.title}")
                if issue.detail:
                    issue_lines.append(issue.detail)
                issue_lines.append("")
            self._issues.setPlainText("\n".join(issue_lines).strip())
        else:
            self._issues.setPlainText("No blocking issues detected.")

        if probe.has_wallet and probe.rpc_reachable:
            if probe.sync_complete:
                self._summary.setText("Studio is ready to use.")
                self._hint.setText("You can finish now and land on the Home page.")
                self._hint.setStyleSheet(_status_style("success"))
            else:
                self._summary.setText("Studio is connected. The chain is still syncing.")
                self._hint.setText("You can finish now. Home and Node will keep showing progress until the node catches up.")
                self._hint.setStyleSheet(_status_style("warning"))
        else:
            self._summary.setText("Studio still needs attention before first use.")
            self._hint.setText("Go back, fix the missing wallet or connection issue, then run verification again.")
            self._hint.setStyleSheet(_status_style("error"))

    def can_finish(self) -> bool:
        return self._probe.has_wallet and self._probe.rpc_reachable


class _FinishPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 18)
        layout.setSpacing(14)
        layout.addLayout(
            _make_header(
                "You’re Ready to Use Studio",
                "Home will show your wallet, node, sync progress, and quick actions in one place.",
            )
        )
        self._summary = QTextEdit()
        self._summary.setReadOnly(True)
        self._summary.setMinimumHeight(220)
        layout.addWidget(self._summary)
        layout.addStretch(1)

    def set_summary(self, profile: RpcProfile, probe: OnboardingProbe, snapshot: StudioSnapshot) -> None:
        lines = [
            f"Profile: {profile.name}",
            f"Mode: {'Managed local node' if profile.type == ProfileType.LOCAL_NODE else 'External RPC'}",
            f"Network: {probe.selected_network}",
            f"RPC URL: {profile.effective_rpc_url()}",
            f"Wallet count: {probe.wallet_count}",
            f"Selected wallet: {snapshot.wallet.selected_label or snapshot.wallet.primary_address or 'Not selected'}",
            f"Balance: {snapshot.wallet.selected_balance_text}",
            f"Node running: {'Yes' if probe.node_running else 'No'}",
            f"Sync: {'Complete' if probe.sync_complete else 'In progress'}",
            "",
            "Next steps:",
            "1. Use Home to confirm wallet balance and node status.",
            "2. Use Wallet to copy an address, receive funds, or send a transaction.",
            "3. Use Mining, ENA, AICF, or DA after the basics are working.",
        ]
        self._summary.setPlainText("\n".join(lines))


class SetupWizard(QDialog):
    """First-run setup wizard for Studio."""

    def __init__(
        self,
        profile_service: ProfileService,
        parent: QWidget | None = None,
        *,
        settings_service: SettingsService | None = None,
        status_service: StudioStatusService | None = None,
    ) -> None:
        super().__init__(parent)
        self._profile_service = profile_service
        self._config = self._resolve_config(profile_service, settings_service)
        self._settings = settings_service or SettingsService(self._config)
        self._status_service = status_service or StudioStatusService(self._config, self._settings)
        self._wallet_repo = WalletRepository()
        self._active_profile = self._profile_service.get_active()
        self._probe = self._status_service.probe_onboarding()
        self._verify_worker: WorkerThread | None = None
        self._wallet_worker: WorkerThread | None = None

        self.setWindowTitle("Animica Studio Setup")
        self.setMinimumSize(620, 560)
        self.setModal(True)
        self._build_ui()
        self._load_initial_state()
        self._go_to(0)

    @staticmethod
    def _resolve_config(profile_service: ProfileService, settings_service: SettingsService | None) -> Config:
        config = getattr(profile_service, "_config", None)
        if isinstance(config, Config):
            return config
        if settings_service is not None and isinstance(getattr(settings_service, "_config", None), Config):
            return settings_service._config  # noqa: SLF001
        raise RuntimeError("SetupWizard requires access to the shared Studio config.")

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._progress = QLabel("")
        self._progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._progress.setStyleSheet("background: #151c25; color: #8f99a5; padding: 8px; font-size: 12px;")
        root.addWidget(self._progress)

        self._stack = QStackedWidget()
        self._network_page = _NetworkPage(self._settings, self._active_profile)
        self._wallet_page = _WalletPage()
        self._connection_page = _ConnectionPage()
        self._verification_page = _VerificationPage()
        self._finish_page = _FinishPage()
        for page in (
            self._network_page,
            self._wallet_page,
            self._connection_page,
            self._verification_page,
            self._finish_page,
        ):
            self._stack.addWidget(page)
        root.addWidget(self._stack, 1)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(16, 10, 16, 10)
        self._cancel = QPushButton("Cancel")
        self._cancel.clicked.connect(self.reject)
        self._back = QPushButton("Back")
        self._back.clicked.connect(self._on_back)
        self._next = QPushButton("Next")
        self._next.clicked.connect(self._on_next)
        buttons.addWidget(self._cancel)
        buttons.addStretch(1)
        buttons.addWidget(self._back)
        buttons.addWidget(self._next)
        root.addLayout(buttons)

    def _load_initial_state(self) -> None:
        self._refresh_wallet_page()
        self._apply_network_defaults_to_connection()

    def _apply_network_defaults_to_connection(self) -> None:
        profile = RpcProfile.from_dict(self._active_profile.to_dict())
        profile.name = self._network_page.profile_name()
        profile.type = ProfileType.LOCAL_NODE if self._network_page.local_node() else ProfileType.REMOTE_RPC
        profile = self._settings.apply_network_preset(
            profile,
            self._network_page.network_key(),
            local_node=self._network_page.local_node(),
        )
        if profile.type == ProfileType.LOCAL_NODE:
            profile.node_rpc_url = profile.effective_rpc_url()
        self._connection_page.set_from_profile(
            profile,
            network_label=self._network_page.network_label(),
            local_node=self._network_page.local_node(),
        )

    def _refresh_wallet_page(self) -> None:
        wallets = self._wallet_repo.load_wallets()
        self._wallet_page.refresh_wallet_summary(wallets, self._wallet_repo.last_error)

    def _go_to(self, step: int) -> None:
        self._stack.setCurrentIndex(step)
        self._progress.setText(f"Step {step + 1} of 5")
        self._back.setEnabled(step > 0 and self._wallet_worker is None and self._verify_worker is None)
        labels = {
            0: "Next",
            1: "Next",
            2: "Verify",
            3: "Next",
            4: "Finish",
        }
        self._next.setText(labels.get(step, "Next"))

    def _set_busy(self, busy: bool, next_text: str | None = None) -> None:
        self._cancel.setEnabled(not busy)
        self._back.setEnabled(not busy and self._stack.currentIndex() > 0)
        self._next.setEnabled(not busy)
        if next_text is not None:
            self._next.setText(next_text)

    def _on_back(self) -> None:
        self._go_to(max(0, self._stack.currentIndex() - 1))

    def _on_next(self) -> None:
        step = self._stack.currentIndex()
        if step == 0:
            self._apply_network_defaults_to_connection()
            self._go_to(1)
            return
        if step == 1:
            self._run_wallet_step()
            return
        if step == 2:
            self._start_verification()
            return
        if step == 3:
            if not self._verification_page.can_finish():
                QMessageBox.warning(self, "Setup Incomplete", "Studio still needs a working wallet and RPC connection before finishing setup.")
                return
            snapshot = self._status_service.collect_snapshot()
            self._probe = self._status_service.probe_onboarding()
            self._finish_page.set_summary(self._active_profile, self._probe, snapshot)
            self._go_to(4)
            return
        self._finish()

    def _run_wallet_step(self) -> None:
        mode = self._wallet_page.mode()
        if mode == "existing":
            if self._wallet_page.wallet_count() < 1:
                self._wallet_page.set_status("Create or import a wallet before continuing.", "error")
                return
            self._go_to(2)
            return

        if self._wallet_worker is not None:
            return

        if mode == "create":
            label = self._wallet_page.create_label()
            if not label:
                self._wallet_page.set_status("Enter a wallet label before continuing.", "error")
                return
            self._wallet_page.set_status("Creating wallet…", "info")
            self._set_busy(True, "Creating…")
            self._wallet_worker = WorkerThread(
                self._status_service.create_wallet,
                label,
                self._wallet_page.create_alg(),
                allow_insecure_fallback=self._wallet_page.allow_insecure(),
            )
            self._wallet_worker.worker.result.connect(self._on_wallet_created)
            self._wallet_worker.worker.error.connect(self._on_wallet_error)
            self._wallet_worker.worker.finished.connect(self._clear_wallet_worker)
            self._wallet_worker.start()
            return

        import_path = self._wallet_page.import_path()
        if not import_path:
            self._wallet_page.set_status("Choose a wallet file to import before continuing.", "error")
            return
        self._wallet_page.set_status("Importing wallet file…", "info")
        self._set_busy(True, "Importing…")
        self._wallet_worker = WorkerThread(self._status_service.import_wallet_store, import_path)
        self._wallet_worker.worker.result.connect(self._on_wallet_imported)
        self._wallet_worker.worker.error.connect(self._on_wallet_error)
        self._wallet_worker.worker.finished.connect(self._clear_wallet_worker)
        self._wallet_worker.start()

    def _on_wallet_created(self, account: Any) -> None:
        address = str(getattr(account, "address", "") or "")
        if address:
            self._status_service.refresh_wallet_selection(address)
        self._refresh_wallet_page()
        self._wallet_page.set_status(f"Wallet created successfully. Address: {address}", "success")
        self._go_to(2)

    def _on_wallet_imported(self, result: ServiceActionResult) -> None:
        self._refresh_wallet_page()
        level = "success" if result.ok else "error"
        self._wallet_page.set_status(f"{result.summary}\n{result.details}".strip(), level)
        if result.ok:
            self._go_to(2)

    def _on_wallet_error(self, message: str, _traceback: str) -> None:
        self._wallet_page.set_status(message, "error")

    def _clear_wallet_worker(self) -> None:
        self._wallet_worker = None
        self._set_busy(False)
        self._go_to(self._stack.currentIndex())

    def _save_profile_from_wizard(self) -> RpcProfile:
        profile = self._profile_service.get_active()
        local_node = self._network_page.local_node()
        profile.name = self._network_page.profile_name()
        profile.type = ProfileType.LOCAL_NODE if local_node else ProfileType.REMOTE_RPC
        profile = self._settings.apply_network_preset(
            profile,
            self._network_page.network_key(),
            local_node=local_node,
        )

        rpc_url = self._connection_page.rpc_url()
        if local_node:
            profile.rpc_url = rpc_url
            profile.node_rpc_url = rpc_url
            profile.node_datadir = self._connection_page.data_dir().strip() or str(default_chain_data_dir(profile.chain_id_expected))
        else:
            profile.node_rpc_url = profile.node_rpc_url or profile.effective_rpc_url()

        profile = self._settings.save_active_profile_settings(
            profile,
            rpc_url=rpc_url,
            explorer_url=self._connection_page.explorer_url(),
            chain_id=self._connection_page.chain_id(),
            node_start_cmd=self._connection_page.node_cmd(),
            node_datadir=self._connection_page.data_dir(),
        )
        if local_node:
            profile.node_rpc_url = rpc_url
        self._profile_service.update_profile(profile)
        self._active_profile = profile
        return profile

    def _start_verification(self) -> None:
        if self._verify_worker is not None:
            return
        try:
            profile = self._save_profile_from_wizard()
        except Exception as exc:  # noqa: BLE001
            self._connection_page.set_status(str(exc), "error")
            return
        self._connection_page.set_status("Settings saved. Running verification…", "success")
        self._go_to(3)
        self._verification_page.set_busy("Running onboarding checks…")
        self._set_busy(True, "Verifying…")
        self._verify_worker = WorkerThread(
            _run_verification,
            self._status_service,
            start_local_node=self._connection_page.start_now(),
        )
        self._verify_worker.worker.result.connect(self._on_verification_result)
        self._verify_worker.worker.error.connect(self._on_verification_error)
        self._verify_worker.worker.finished.connect(self._clear_verify_worker)
        self._verify_worker.start()
        log.info("SetupWizard: verifying profile %s (%s)", profile.name, profile.id)

    def _on_verification_result(self, result: dict[str, Any]) -> None:
        probe = result.get("probe")
        snapshot = result.get("snapshot")
        start_result = result.get("start_result")
        if not isinstance(probe, OnboardingProbe) or not isinstance(snapshot, StudioSnapshot):
            self._verification_page._summary.setText("Verification returned unexpected data.")  # noqa: SLF001
            return
        self._probe = probe
        self._verification_page.apply(probe, snapshot, start_result if isinstance(start_result, ServiceActionResult) else None)

    def _on_verification_error(self, message: str, _traceback: str) -> None:
        self._verification_page._summary.setText("Verification failed.")  # noqa: SLF001
        self._verification_page._issues.setPlainText(message)  # noqa: SLF001

    def _clear_verify_worker(self) -> None:
        self._verify_worker = None
        self._set_busy(False)
        self._go_to(self._stack.currentIndex())

    def _finish(self) -> None:
        self._settings.mark_onboarding_complete(self._network_page.network_key())
        log.info("SetupWizard: completed network=%s profile=%s", self._network_page.network_key(), self._active_profile.id)
        self.accept()
