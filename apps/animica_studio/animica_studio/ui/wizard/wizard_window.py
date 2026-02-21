"""First-run setup wizard for Animica Studio.

Five pages:
1. WelcomePage     — Choose connection mode (Remote RPC or Local Node)
2. RemoteRpcPage   — Configure remote endpoint + test connection
3. LocalNodePage   — Configure local node + write-permission check
4. ProfileNamePage — Name the profile
5. FinishPage      — Summary + save
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.models.profile_models import ProfileType, RpcProfile
from animica_studio.services.workers import WorkerThread
from animica_studio.util.fs import check_writable_dir
from animica_studio.util.paths import default_chain_data_dir, running_as_root

log = logging.getLogger(__name__)

_REMOTE_DEFAULT_URL = "https://mainnet.animica.org/rpc"
_LOCAL_DEFAULT_URL = "http://127.0.0.1:8545/rpc"
_DEFAULT_CHAIN_ID = 1
_DEFAULT_START_CMD = "animica node start"


# ---------------------------------------------------------------------------
# Shared style helpers
# ---------------------------------------------------------------------------


def _make_header(title: str, subtitle: str = "") -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(4)
    title_lbl = QLabel(title)
    title_lbl.setObjectName("wizardPageTitle")
    layout.addWidget(title_lbl)
    if subtitle:
        sub_lbl = QLabel(subtitle)
        sub_lbl.setObjectName("wizardPageSubtitle")
        sub_lbl.setWordWrap(True)
        layout.addWidget(sub_lbl)
    return layout


def _status_label(text: str = "", color: str = "#a6adc8") -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color: {color}; font-size: 12px;")
    return lbl


# ---------------------------------------------------------------------------
# Page 1: Welcome
# ---------------------------------------------------------------------------


class WelcomePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 16)
        layout.setSpacing(16)

        for widget_layout in [_make_header(
            "Welcome to Animica Studio",
            "Let's get you set up. First, choose how you want to connect to the Animica network.",
        )]:
            layout.addLayout(widget_layout)

        self.radio_remote = QRadioButton("🌐  Remote RPC  (recommended)")
        self.radio_remote.setChecked(True)
        self.radio_local = QRadioButton("🖥️  Local Node  (run on this machine)")

        for rb in (self.radio_remote, self.radio_local):
            rb.setStyleSheet("font-size: 14px; padding: 8px 0;")
            layout.addWidget(rb)

        layout.addStretch()

    @property
    def profile_type(self) -> ProfileType:
        return ProfileType.REMOTE_RPC if self.radio_remote.isChecked() else ProfileType.LOCAL_NODE


# ---------------------------------------------------------------------------
# Page 2: Remote RPC
# ---------------------------------------------------------------------------


class RemoteRpcPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 16)
        layout.setSpacing(12)

        for widget_layout in [_make_header(
            "Remote RPC Connection",
            "Enter the URL and expected Chain ID of the Animica RPC node you want to connect to.",
        )]:
            layout.addLayout(widget_layout)

        layout.addWidget(QLabel("RPC URL:"))
        self.url_edit = QLineEdit(_REMOTE_DEFAULT_URL)
        self.url_edit.setPlaceholderText("https://mainnet.animica.org/rpc")
        layout.addWidget(self.url_edit)

        layout.addWidget(QLabel("Expected Chain ID:"))
        self.chain_id_edit = QLineEdit(str(_DEFAULT_CHAIN_ID))
        self.chain_id_edit.setPlaceholderText("1")
        layout.addWidget(self.chain_id_edit)

        self.ignore_chainid_cb = QCheckBox("Ignore chain ID mismatch")
        self.ignore_chainid_cb.setChecked(False)
        layout.addWidget(self.ignore_chainid_cb)

        test_btn = QPushButton("🔌  Test Connection")
        test_btn.setObjectName("primaryButton")
        test_btn.clicked.connect(self._test_connection)
        layout.addWidget(test_btn)

        self._status_lbl = _status_label()
        layout.addWidget(self._status_lbl)

        layout.addStretch()

        self._worker: WorkerThread | None = None
        self._last_test_ok: bool = False
        self._last_actual_chain_id: int | None = None
        self._last_drift_s: float | None = None

    def _set_status(self, text: str, color: str = "#a6adc8") -> None:
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(f"color: {color}; font-size: 12px;")

    def _test_connection(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            self._set_status("Please enter a URL.", "#f38ba8")
            return

        try:
            chain_id_expected = int(self.chain_id_edit.text().strip())
        except ValueError:
            self._set_status("Invalid Chain ID — must be an integer.", "#f38ba8")
            return

        self._set_status("Testing connection…", "#a6adc8")
        self._last_test_ok = False

        def _do_test() -> dict[str, Any]:
            from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415

            client = RpcClient(url, connect_timeout=5.0, read_timeout=10.0, max_retries=1)
            try:
                discover = client.discover()
                methods = discover.get("methods", [])
                method_names: set[str] = set()
                for m in methods:
                    if isinstance(m, dict):
                        method_names.add(m.get("name", ""))
                    else:
                        method_names.add(str(m))

                head = client.get_head()
                actual_chain_id: int | None = None
                try:
                    actual_chain_id = client.get_chain_id()
                except Exception:  # noqa: BLE001
                    pass

                drift_s: float | None = None
                if head.timestamp is not None:
                    drift_s = abs(time.time() - head.timestamp)

                return {
                    "ok": True,
                    "head_number": head.number,
                    "head_hash": head.hash,
                    "method_count": len(method_names),
                    "actual_chain_id": actual_chain_id,
                    "drift_s": drift_s,
                }
            finally:
                client.close()

        self._worker = WorkerThread(_do_test)
        self._worker.worker.result.connect(self._on_test_result)
        self._worker.worker.error.connect(self._on_test_error)
        self._worker.start()

    def _on_test_result(self, result: dict[str, Any]) -> None:
        self._last_actual_chain_id = result.get("actual_chain_id")
        self._last_drift_s = result.get("drift_s")

        try:
            chain_id_expected = int(self.chain_id_edit.text().strip())
        except ValueError:
            chain_id_expected = None

        chain_ok = True
        chain_note = ""
        if self._last_actual_chain_id is not None and chain_id_expected is not None:
            if self._last_actual_chain_id != chain_id_expected:
                chain_ok = self.ignore_chainid_cb.isChecked()
                chain_note = (
                    f"  ⚠️ Chain ID mismatch: expected {chain_id_expected}, "
                    f"got {self._last_actual_chain_id}."
                )

        drift_note = ""
        drift_color = "#a6e3a1"  # green by default
        if self._last_drift_s is not None:
            if self._last_drift_s > 120:
                drift_note = f"  🔴 Time drift: {self._last_drift_s:.0f}s (severe — check system clock)."
                drift_color = "#f38ba8"
            elif self._last_drift_s > 30:
                drift_note = f"  🟡 Time drift: {self._last_drift_s:.0f}s (warning)."
                drift_color = "#f9e2af"

        self._last_test_ok = chain_ok
        status = (
            f"✅  Connected — head #{result['head_number']}, "
            f"{result['method_count']} methods discovered."
            + chain_note
            + drift_note
        )
        color = drift_color if chain_ok else "#f38ba8"
        self._set_status(status, color)

    def _on_test_error(self, msg: str, _tb: str) -> None:
        self._last_test_ok = False
        self._set_status(f"❌  Connection failed: {msg}", "#f38ba8")

    @property
    def rpc_url(self) -> str:
        return self.url_edit.text().strip()

    @property
    def chain_id_expected(self) -> int:
        try:
            return int(self.chain_id_edit.text().strip())
        except ValueError:
            return _DEFAULT_CHAIN_ID


# ---------------------------------------------------------------------------
# Page 3: Local Node
# ---------------------------------------------------------------------------


class LocalNodePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 16)
        layout.setSpacing(10)

        for widget_layout in [_make_header(
            "Local Node Configuration",
            "Configure the local Animica node that Studio will manage.",
        )]:
            layout.addLayout(widget_layout)

        # Datadir
        layout.addWidget(QLabel("Data Directory:"))
        self._chain_id = _DEFAULT_CHAIN_ID
        self._datadir_is_custom = False
        datadir_row = QHBoxLayout()
        self.datadir_edit = QLineEdit(str(self._default_data_dir()))
        self.datadir_edit.textChanged.connect(self._on_datadir_text_changed)
        datadir_row.addWidget(self.datadir_edit, stretch=1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_datadir)
        datadir_row.addWidget(browse_btn)
        reset_btn = QPushButton("Reset to default")
        reset_btn.clicked.connect(self._reset_datadir_to_default)
        datadir_row.addWidget(reset_btn)
        layout.addLayout(datadir_row)

        self._datadir_default_lbl = _status_label()
        layout.addWidget(self._datadir_default_lbl)
        self._root_warn_lbl = _status_label(color="#f9e2af")
        self._root_warn_lbl.setVisible(False)
        layout.addWidget(self._root_warn_lbl)
        self._refresh_datadir_hints()

        # RPC URL
        layout.addWidget(QLabel("Local RPC URL:"))
        self.rpc_url_edit = QLineEdit(_LOCAL_DEFAULT_URL)
        layout.addWidget(self.rpc_url_edit)

        # Start command
        layout.addWidget(QLabel("Start Command (space-separated):"))
        self.start_cmd_edit = QLineEdit(_DEFAULT_START_CMD)
        layout.addWidget(self.start_cmd_edit)

        # Buttons
        perm_btn = QPushButton("🔍  Check Write Permissions")
        perm_btn.clicked.connect(self._check_permissions)
        layout.addWidget(perm_btn)

        start_btn = QPushButton("🚀  Start & Validate Node")
        start_btn.clicked.connect(self._start_and_validate)
        layout.addWidget(start_btn)

        self._status_lbl = _status_label()
        layout.addWidget(self._status_lbl)

        layout.addStretch()

        self._worker: WorkerThread | None = None
        self._last_actual_chain_id: int | None = None

    def _browse_datadir(self) -> None:
        current = self.datadir_edit.text().strip() or str(self._default_data_dir())
        chosen = QFileDialog.getExistingDirectory(self, "Select Node Data Directory", current)
        if chosen:
            self.datadir_edit.setText(chosen)

    def _default_data_dir(self) -> Path:
        return default_chain_data_dir(self._chain_id)

    def _refresh_datadir_hints(self) -> None:
        default_txt = str(self._default_data_dir())
        self._datadir_default_lbl.setText(f"Default: {default_txt}")
        if running_as_root():
            self._root_warn_lbl.setVisible(True)
            self._root_warn_lbl.setText(
                "⚠ Running as root. Default path uses /root/.animica/... "
                "Run Studio as non-root for consistent wallet/data paths."
            )
        else:
            self._root_warn_lbl.setVisible(False)

    def _on_datadir_text_changed(self, _text: str) -> None:
        current = self.datadir_edit.text().strip()
        self._datadir_is_custom = bool(current and current != str(self._default_data_dir()))

    def _reset_datadir_to_default(self) -> None:
        self._datadir_is_custom = False
        self.datadir_edit.setText(str(self._default_data_dir()))
        self._refresh_datadir_hints()

    def set_chain_id(self, chain_id: int) -> None:
        self._chain_id = int(chain_id)
        if not self._datadir_is_custom:
            self.datadir_edit.setText(str(self._default_data_dir()))
        self._refresh_datadir_hints()

    def _set_status(self, text: str, color: str = "#a6adc8") -> None:
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(f"color: {color}; font-size: 12px;")

    def _check_permissions(self) -> None:
        datadir = self.datadir_edit.text().strip()
        if not datadir:
            self._set_status("Please enter a data directory.", "#f38ba8")
            return
        self._set_status("Checking permissions…", "#a6adc8")

        def _do_check() -> tuple[bool, str | None]:
            return check_writable_dir(datadir)

        self._worker = WorkerThread(_do_check)
        self._worker.worker.result.connect(self._on_perm_result)
        self._worker.worker.error.connect(lambda m, _t: self._set_status(f"Error: {m}", "#f38ba8"))
        self._worker.start()

    def _on_perm_result(self, result: tuple[bool, str | None]) -> None:
        ok, err = result
        if ok:
            self._set_status("✅  Directory is writable.", "#a6e3a1")
        else:
            self._set_status(f"❌  {err or 'Not writable'}", "#f38ba8")

    def _start_and_validate(self) -> None:
        rpc_url = self.rpc_url_edit.text().strip()
        start_cmd = self.start_cmd_edit.text().strip().split()
        datadir = self.datadir_edit.text().strip()

        if not rpc_url or not start_cmd:
            self._set_status("Please fill in all fields.", "#f38ba8")
            return

        self._set_status("Starting node…", "#a6adc8")

        def _do_start() -> dict[str, Any]:
            from animica_studio.services.process_manager import ProcessManager  # noqa: PLC0415
            from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415

            pm = ProcessManager(
                start_cmd=start_cmd,
                rpc_url=rpc_url,
                data_dir=Path(datadir) if datadir else None,
            )
            status = pm.start()
            if not status.get("rpc_reachable"):
                return {"ok": False, "error": "Node started but RPC not reachable"}

            client = RpcClient(rpc_url, connect_timeout=5.0, read_timeout=10.0, max_retries=1)
            try:
                head = client.get_head()
                chain_id = None
                try:
                    chain_id = client.get_chain_id()
                except Exception:  # noqa: BLE001
                    pass
                return {
                    "ok": True,
                    "head_number": head.number,
                    "chain_id": chain_id,
                    "pid": status.get("pid"),
                }
            finally:
                client.close()

        self._worker = WorkerThread(_do_start)
        self._worker.worker.result.connect(self._on_start_result)
        self._worker.worker.error.connect(lambda m, _t: self._set_status(f"❌  {m}", "#f38ba8"))
        self._worker.start()

    def _on_start_result(self, result: dict[str, Any]) -> None:
        if result.get("ok"):
            self._last_actual_chain_id = result.get("chain_id")
            if self._last_actual_chain_id is not None:
                self.set_chain_id(self._last_actual_chain_id)
            self._set_status(
                f"✅  Node running (pid={result.get('pid')}), "
                f"head #{result.get('head_number')}.",
                "#a6e3a1",
            )
        else:
            self._set_status(f"❌  {result.get('error', 'Unknown error')}", "#f38ba8")

    @property
    def rpc_url(self) -> str:
        return self.rpc_url_edit.text().strip()

    @property
    def node_datadir(self) -> str:
        return self.datadir_edit.text().strip()

    @property
    def node_start_cmd(self) -> list[str]:
        return self.start_cmd_edit.text().strip().split()

    @property
    def chain_id_expected(self) -> int:
        return int(self._last_actual_chain_id or self._chain_id)

    @property
    def datadir_is_custom(self) -> bool:
        return self._datadir_is_custom


# ---------------------------------------------------------------------------
# Page 4: Profile Name
# ---------------------------------------------------------------------------


class ProfileNamePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._profile_type: ProfileType = ProfileType.REMOTE_RPC

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 16)
        layout.setSpacing(12)

        for widget_layout in [_make_header(
            "Name Your Profile",
            "Give this connection a memorable name.",
        )]:
            layout.addLayout(widget_layout)

        layout.addWidget(QLabel("Profile Name:"))
        self.name_edit = QLineEdit("Mainnet Remote")
        layout.addWidget(self.name_edit)

        self.set_default_cb = QCheckBox("Set as default/active profile")
        self.set_default_cb.setChecked(True)
        layout.addWidget(self.set_default_cb)

        layout.addStretch()

    def set_profile_type(self, pt: ProfileType) -> None:
        self._profile_type = pt
        if pt == ProfileType.LOCAL_NODE:
            self.name_edit.setText("Local Node")
        else:
            self.name_edit.setText("Mainnet Remote")

    @property
    def profile_name(self) -> str:
        return self.name_edit.text().strip() or (
            "Local Node" if self._profile_type == ProfileType.LOCAL_NODE else "Mainnet Remote"
        )

    @property
    def set_as_default(self) -> bool:
        return self.set_default_cb.isChecked()


# ---------------------------------------------------------------------------
# Page 5: Finish
# ---------------------------------------------------------------------------


class FinishPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 16)
        layout.setSpacing(12)

        for widget_layout in [_make_header(
            "Ready to Go!",
            "Your profile is configured. Click Finish to save and start using Animica Studio.",
        )]:
            layout.addLayout(widget_layout)

        self._summary_lbl = QLabel("")
        self._summary_lbl.setWordWrap(True)
        self._summary_lbl.setObjectName("wizardSummary")
        layout.addWidget(self._summary_lbl)

        layout.addStretch()

    def set_summary(self, profile: RpcProfile) -> None:
        lines = [
            f"<b>Name:</b> {profile.name}",
            f"<b>Mode:</b> {'Local Node' if profile.is_local() else 'Remote RPC'}",
            f"<b>RPC URL:</b> {profile.rpc_url}",
            f"<b>Chain ID:</b> {profile.chain_id_expected}",
        ]
        if profile.is_local():
            lines.append(f"<b>Data Dir:</b> {profile.node_datadir or '—'}")
        self._summary_lbl.setText("<br>".join(lines))


# ---------------------------------------------------------------------------
# Main Wizard Dialog
# ---------------------------------------------------------------------------


class SetupWizard(QDialog):
    """First-run setup wizard.

    Usage::

        wizard = SetupWizard(profile_service, parent=self)
        if wizard.exec() == QDialog.DialogCode.Accepted:
            # profile was saved
            pass
    """

    def __init__(self, profile_service: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = profile_service
        self._current_idx = 0
        self._profile_type = ProfileType.REMOTE_RPC

        self.setWindowTitle("Animica Studio — Setup Wizard")
        self.setMinimumSize(540, 480)
        self.setModal(True)

        self._build_ui()
        self._go_to(0)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Progress indicator
        self._progress_lbl = QLabel("Step 1 of 5")
        self._progress_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._progress_lbl.setStyleSheet(
            "background: #181825; color: #a6adc8; padding: 6px; font-size: 11px;"
        )
        root.addWidget(self._progress_lbl)

        # Stacked pages
        self._stack = QStackedWidget()
        self._page_welcome = WelcomePage()
        self._page_remote = RemoteRpcPage()
        self._page_local = LocalNodePage()
        self._page_name = ProfileNamePage()
        self._page_finish = FinishPage()

        for page in (
            self._page_welcome,
            self._page_remote,
            self._page_local,
            self._page_name,
            self._page_finish,
        ):
            self._stack.addWidget(page)

        root.addWidget(self._stack, stretch=1)

        # Button row
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #313244;")
        root.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(16, 8, 16, 8)

        self._back_btn = QPushButton("← Back")
        self._back_btn.clicked.connect(self._on_back)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setObjectName("primaryButton")
        self._next_btn.clicked.connect(self._on_next)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)

        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._back_btn)
        btn_row.addWidget(self._next_btn)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Page indices
    # ---------------------

    # Page layout:
    # 0 = Welcome
    # 1 = Remote RPC
    # 2 = Local Node
    # 3 = Profile Name
    # 4 = Finish

    def _pages_for_type(self) -> list[int]:
        """Return the ordered list of page indices for the current profile type."""
        if self._profile_type == ProfileType.REMOTE_RPC:
            return [0, 1, 3, 4]
        return [0, 2, 3, 4]

    def _go_to(self, logical_step: int) -> None:
        """Navigate to a logical step (index into _pages_for_type())."""
        pages = self._pages_for_type()
        if logical_step < 0 or logical_step >= len(pages):
            return
        self._current_idx = logical_step
        page_idx = pages[logical_step]
        self._stack.setCurrentIndex(page_idx)

        total = len(pages)
        self._progress_lbl.setText(f"Step {logical_step + 1} of {total}")
        self._back_btn.setEnabled(logical_step > 0)

        is_last = logical_step == total - 1
        self._next_btn.setText("Finish" if is_last else "Next →")

    def _on_back(self) -> None:
        self._go_to(self._current_idx - 1)

    def _on_next(self) -> None:
        pages = self._pages_for_type()
        total = len(pages)

        # Capture welcome choice
        if self._current_idx == 0:
            self._profile_type = self._page_welcome.profile_type
            self._page_name.set_profile_type(self._profile_type)

        if self._current_idx == total - 1:
            self._finish()
        else:
            self._go_to(self._current_idx + 1)

    # ------------------------------------------------------------------
    # Finish
    # ------------------------------------------------------------------

    def _finish(self) -> None:
        """Build the RpcProfile from collected data and save via ProfileService."""
        import uuid as _uuid  # noqa: PLC0415

        if self._profile_type == ProfileType.REMOTE_RPC:
            rpc_url = self._page_remote.rpc_url
            chain_id = self._page_remote.chain_id_expected
            node_start_cmd = None
            node_datadir = None
            node_rpc_url = None
        else:
            rpc_url = self._page_local.rpc_url
            chain_id = self._page_local.chain_id_expected
            node_start_cmd = self._page_local.node_start_cmd or ["animica", "node", "start"]
            node_datadir = self._page_local.node_datadir or str(default_chain_data_dir(chain_id))
            node_rpc_url = rpc_url

        profile = RpcProfile(
            id=str(_uuid.uuid4()),
            name=self._page_name.profile_name,
            type=self._profile_type,
            rpc_url=rpc_url,
            chain_id_expected=chain_id,
            node_start_cmd=node_start_cmd,
            node_datadir=node_datadir,
            node_datadir_custom=self._page_local.datadir_is_custom,
            node_rpc_url=node_rpc_url,
        )

        self._page_finish.set_summary(profile)
        self._service.add_profile(profile)

        if self._page_name.set_as_default:
            self._service.set_active(profile.id)

        # Mark first run completed via public service method
        self._service.mark_first_run_complete()

        log.info("SetupWizard: profile saved: %r (%s)", profile.name, profile.id)
        self.accept()
