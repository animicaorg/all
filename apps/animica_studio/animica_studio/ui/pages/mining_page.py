"""Mining page — mine blocks, automine toggle, live mining log."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from animica_studio.models.wallet_models import is_valid_address
from animica_studio.services.mining_service import MiningService
from animica_studio.services.job_runner import JobHandle, JobRunner
from animica_studio.services.wallet_store import WalletStore
from animica_studio.services.workers import WorkerThread
from animica_studio.storage.config import Config
from animica_studio.ui.widgets.stream_console import StreamConsole

log = logging.getLogger(__name__)

_SPACING_TOKEN = "min_block_spacing"


class SpacingBackoff:
    """Tracks spacing backoff and warning throttling for readable mining logs."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._level = 0
        self._next_warning_ts = 0.0

    def on_spacing_detected(self) -> tuple[float, bool]:
        now = time.monotonic()
        delay = [1.5, 3.0, 5.0][min(self._level, 2)]
        self._level = min(self._level + 1, 2)
        should_emit_warning = now >= self._next_warning_ts
        self._next_warning_ts = now + delay
        return delay, should_emit_warning


def _validate_animica_address(address: str) -> bool:
    text = (address or "").strip()
    if not text.startswith("anim1"):
        return False

    try:
        from pq.py.address import validate_address  # noqa: PLC0415

        validate_address(text, expect_hrp="anim")
        return True
    except Exception:  # noqa: BLE001
        return is_valid_address(text)


def _parse_mining_status_line(line: str) -> dict[str, object]:
    """Extract mined progress details from known CLI output lines."""
    out: dict[str, object] = {}
    accepted = re.search(r"ACCEPTED: Block\s+(\d+)/(\d+)\s+\(height:\s*(\d+),.*reward:\s*([0-9.]+)", line)
    if accepted:
        out["accepted"] = True
        out["accepted_block"] = int(accepted.group(1))
        out["accepted_target"] = int(accepted.group(2))
        out["height"] = int(accepted.group(3))
        out["reward"] = accepted.group(4)

    summary = re.search(r"Successfully mined\s+(\d+)\s+block\(s\).*New chain height:\s*(\d+)", line)
    if summary:
        out["summary_mined"] = int(summary.group(1))
        out["height"] = int(summary.group(2))

    hash_match = re.search(r"(0x[a-fA-F0-9]{64})", line)
    if hash_match and "hash" in line.lower():
        out["hash"] = hash_match.group(1)

    return out


class MiningPage(QWidget):
    """Mining controls: mine-blocks, automine, live log."""

    def __init__(self, config: Config | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from animica_studio.storage.config import load_config  # noqa: PLC0415

        self._config = config or load_config()
        self._service = MiningService(self._config)
        self._runner = JobRunner.instance()
        self._wallet_store = WalletStore()
        self._job: JobHandle | None = None
        self._target_blocks = 0
        self._mined_blocks = 0
        self._attempts_made = 0
        self._spacing_seen_this_attempt = False
        self._cancel_requested = False
        self._last_height: int | None = None
        self._last_hash: str | None = None
        self._last_reward: str | None = None
        self._backoff = SpacingBackoff()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("⛏️  Mining")
        title.setObjectName("placeholderLabel")
        layout.addWidget(title)

        mine_group = QGroupBox("Mine Blocks (local CPU)")
        mine_form = QFormLayout(mine_group)

        self._count_spin = QSpinBox()
        self._count_spin.setRange(1, 1000)
        self._count_spin.setValue(1)
        mine_form.addRow("Blocks to mine:", self._count_spin)

        self._payout_input = QComboBox()
        self._payout_input.setEditable(True)
        self._payout_input.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._payout_input.setMinimumContentsLength(28)
        self._payout_input.lineEdit().setPlaceholderText("Wallet label or anim1… address")
        self._refresh_wallet_labels()
        mine_form.addRow("Payout destination:", self._payout_input)

        self._threads_spin = QSpinBox()
        self._threads_spin.setRange(0, 256)
        self._threads_spin.setValue(0)
        self._threads_spin.setToolTip("0 = auto-detect CPU threads")
        mine_form.addRow("Threads:", self._threads_spin)

        attempts_row = QWidget()
        attempts_layout = QHBoxLayout(attempts_row)
        attempts_layout.setContentsMargins(0, 0, 0, 0)
        self._attempts_unlimited = QCheckBox("Unlimited")
        self._attempts_unlimited.setChecked(True)
        self._attempts_unlimited.toggled.connect(self._on_toggle_attempt_limit)
        self._attempt_limit_spin = QSpinBox()
        self._attempt_limit_spin.setRange(1, 100000)
        self._attempt_limit_spin.setValue(500)
        self._attempt_limit_spin.setEnabled(False)
        attempts_layout.addWidget(self._attempts_unlimited)
        attempts_layout.addWidget(self._attempt_limit_spin)
        attempts_layout.addStretch()
        mine_form.addRow("Attempt limit:", attempts_row)

        self._backoff_check = QCheckBox("Enable spacing backoff")
        self._backoff_check.setChecked(True)
        self._backoff_check.setToolTip("Avoid spamming template requests when min_block_spacing is active")
        mine_form.addRow("Backoff mode:", self._backoff_check)

        self._validation_label = QLabel("")
        self._validation_label.setStyleSheet("color: #d9534f;")
        mine_form.addRow("", self._validation_label)

        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        self._mine_btn = QPushButton("▶  Start Mining")
        self._mine_btn.clicked.connect(self._on_mine)
        btn_layout.addWidget(self._mine_btn)
        self._cancel_btn = QPushButton("⏹  Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self._cancel_btn)
        btn_layout.addStretch()
        mine_form.addRow("", btn_row)

        self._progress_label = QLabel("Mined 0 / 0")
        self._details_label = QLabel("Height: —   Hash: —   Reward: —")
        mine_form.addRow("Progress:", self._progress_label)
        mine_form.addRow("Last result:", self._details_label)

        layout.addWidget(mine_group)

        auto_group = QGroupBox("Automine (RPC)")
        auto_layout = QHBoxLayout(auto_group)
        self._automine_check = QCheckBox("Enable automine")
        auto_layout.addWidget(self._automine_check)
        self._automine_btn = QPushButton("Apply")
        self._automine_btn.clicked.connect(self._on_automine)
        auto_layout.addWidget(self._automine_btn)
        auto_layout.addStretch()
        layout.addWidget(auto_group)

        self._console = StreamConsole()
        layout.addWidget(self._console, stretch=1)

    def _refresh_wallet_labels(self) -> None:
        records = self._wallet_store.reload_local_wallets(Path.home() / ".animica" / "wallets.json")
        current = self._payout_input.currentText().strip() if hasattr(self, "_payout_input") else ""
        labels = [record.label for record in records if record.label]
        if hasattr(self, "_payout_input"):
            self._payout_input.clear()
            self._payout_input.addItems(labels)
            self._payout_input.setCurrentText(current)

    def _on_toggle_attempt_limit(self, unlimited: bool) -> None:
        self._attempt_limit_spin.setEnabled(not unlimited)

    def _set_running_state(self, running: bool) -> None:
        self._mine_btn.setEnabled(not running)
        self._cancel_btn.setEnabled(running)

    def _max_attempts(self) -> int | None:
        if self._attempts_unlimited.isChecked():
            return None
        return self._attempt_limit_spin.value()

    def resolve_payout_address(self) -> str | None:
        raw = self._payout_input.currentText().strip()
        if not raw:
            self._validation_label.setText("Payout destination is required.")
            return None

        records = self._wallet_store.reload_local_wallets(Path.home() / ".animica" / "wallets.json")
        by_label = {record.label: record.address for record in records if record.label and record.address}
        if raw in by_label:
            resolved = by_label[raw].strip()
            if _validate_animica_address(resolved):
                self._validation_label.setText("")
                return resolved
            self._validation_label.setText(f"Wallet label '{raw}' does not resolve to a valid anim1 address.")
            return None

        if _validate_animica_address(raw):
            self._validation_label.setText("")
            return raw

        self._validation_label.setText("Invalid payout destination. Use a wallet label or valid anim1 address.")
        return None

    def _schedule_next_attempt(self, delay_s: float) -> None:
        if self._cancel_requested:
            self._finish_cancelled()
            return
        QTimer.singleShot(int(delay_s * 1000), self._start_single_block_attempt)

    def _start_single_block_attempt(self) -> None:
        if self._cancel_requested:
            self._finish_cancelled()
            return
        if self._mined_blocks >= self._target_blocks:
            self._finish_success()
            return

        max_attempts = self._max_attempts()
        if max_attempts is not None and self._attempts_made >= max_attempts:
            self._console.append_system(f"⚠ Reached configured attempt limit ({max_attempts}).")
            self._set_running_state(False)
            return

        payout_address = self.resolve_payout_address()
        if not payout_address:
            self._set_running_state(False)
            return

        self._attempts_made += 1
        self._spacing_seen_this_attempt = False
        self._progress_label.setText(f"Mined {self._mined_blocks} / {self._target_blocks} (attempt {self._attempts_made})")

        try:
            cmd, env = self._service.build_mine_blocks_command(1, payout_address, self._threads_spin.value())
        except Exception as exc:  # noqa: BLE001
            self._set_running_state(False)
            diag = self._service.mining_diagnostics()
            self._console.append_system(f"❌ {exc}")
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Mining CLI unsupported")
            box.setText("Your animica CLI does not support mine-blocks from this Studio build.")
            box.setDetailedText(diag)
            box.exec()
            return

        self._job = self._runner.run_cli(cmd, env=env or None, timeout_s=180)
        self._job.output.connect(self._on_job_output)
        self._job.error.connect(lambda _jid, msg, _details: self._console.append_system(f"❌ Error: {msg}"))
        self._job.finished.connect(self._on_job_finished)

    def _on_mine(self) -> None:
        payout_address = self.resolve_payout_address()
        if not payout_address:
            self._console.append_system("❌ Mining start blocked: invalid payout destination.")
            return

        self._target_blocks = self._count_spin.value()
        self._mined_blocks = 0
        self._attempts_made = 0
        self._cancel_requested = False
        self._last_hash = None
        self._last_height = None
        self._last_reward = None
        self._backoff.reset()

        limit_text = "Unlimited" if self._max_attempts() is None else str(self._max_attempts())
        self._console.append_system(
            f"Mining {self._target_blocks} block(s) with threads={self._threads_spin.value()}, "
            f"attempt_limit={limit_text}, backoff={'on' if self._backoff_check.isChecked() else 'off'}"
        )
        self._set_running_state(True)
        self._start_single_block_attempt()

    def _on_job_output(self, _job_id: str, stream: str, text: str) -> None:
        line = text.strip()
        if not line:
            return

        lower_line = line.lower()
        if _SPACING_TOKEN in lower_line and self._backoff_check.isChecked():
            delay_s, should_emit = self._backoff.on_spacing_detected()
            self._spacing_seen_this_attempt = True
            if should_emit:
                self._console.append_system(
                    f"Warning: Block template unavailable (min_block_spacing). Retrying in {delay_s:.1f}s."
                )
            return

        parsed = _parse_mining_status_line(line)
        if "height" in parsed:
            self._last_height = int(parsed["height"])
        if "hash" in parsed:
            self._last_hash = str(parsed["hash"])
        if "reward" in parsed:
            self._last_reward = str(parsed["reward"])
        if parsed:
            self._details_label.setText(
                f"Height: {self._last_height if self._last_height is not None else '—'}   "
                f"Hash: {self._last_hash or '—'}   "
                f"Reward: {self._last_reward or '—'}"
            )

        self._console.append_line(stream, text)

    def _on_job_finished(self, _job_id: str, exit_code: int, _payload: object) -> None:
        self._job = None

        if self._cancel_requested:
            self._finish_cancelled()
            return

        if exit_code == 0:
            self._mined_blocks += 1
            self._backoff.reset()
            self._progress_label.setText(f"Mined {self._mined_blocks} / {self._target_blocks}")
            if self._mined_blocks >= self._target_blocks:
                self._finish_success()
                return
            self._schedule_next_attempt(0.25)
            return

        if self._spacing_seen_this_attempt and self._backoff_check.isChecked():
            delay_s, _ = self._backoff.on_spacing_detected()
            self._schedule_next_attempt(delay_s)
            return

        self._console.append_system(f"⚠ Mining attempt failed (rc={exit_code}); retrying.")
        self._schedule_next_attempt(0.5)

    def _finish_success(self) -> None:
        self._set_running_state(False)
        self._console.append_system(f"✅ Done. Mined {self._mined_blocks} / {self._target_blocks} block(s).")

    def _finish_cancelled(self) -> None:
        self._set_running_state(False)
        self._job = None
        self._console.append_system("[Mining cancelled]")

    def _on_automine(self) -> None:
        enabled = self._automine_check.isChecked()
        service = self._service
        console = self._console

        def _task():
            return service.set_automine(enabled)

        def _done(result):
            if result.get("ok"):
                console.append_system(f"✅ Automine {'enabled' if enabled else 'disabled'}.")
            else:
                console.append_system(f"⚠ {result.get('error', 'Unknown error')}")

        def _err(msg, _tb):
            console.append_system(f"❌ {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()

    def _on_cancel(self) -> None:
        self._cancel_requested = True
        if self._job is not None:
            self._runner.cancel(self._job.job_id)
        self._cancel_btn.setEnabled(False)
        self._console.append_system("[Cancellation requested…]")
