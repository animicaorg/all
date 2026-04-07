from __future__ import annotations

from urllib.parse import quote_plus

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.tx_service import TxService, TxServiceResult
from animica_studio.services.wallet_repository import WalletRepository
from animica_studio.services.workers import WorkerThread
from animica_studio.storage.config import Config, load_config
from animica_studio.models.wallet_models import ANM_BASE_UNITS

class TxSendPage(QWidget):
    def __init__(self, config: Config | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config or load_config()
        self._repo = WalletRepository()
        self._tx_service = TxService(self._config)
        self._thread: WorkerThread | None = None
        self._last_tx_hash: str | None = None
        self._build_ui()
        self.reload_wallets()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("TX Send")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        subtitle = QLabel("Build, sign, and submit transfers through the canonical Animica CLI pipeline.")
        subtitle.setStyleSheet("color: #8c95a1;")
        root.addWidget(title)
        root.addWidget(subtitle)

        box = QGroupBox("Transfer")
        form = QFormLayout(box)
        form.setVerticalSpacing(10)

        self._from = QComboBox()
        self._from.setMinimumWidth(400)
        form.addRow("From", self._from)

        self._to = QLineEdit()
        self._to.setPlaceholderText("anim1… recipient address")
        form.addRow("To", self._to)

        amount_row = QWidget()
        amount_layout = QHBoxLayout(amount_row)
        amount_layout.setContentsMargins(0, 0, 0, 0)
        self._amount = QLineEdit()
        self._amount.setPlaceholderText("0.0")
        amount_layout.addWidget(self._amount)
        unit = QLabel("ANM")
        unit.setStyleSheet("color: #8c95a1; font-weight: 600;")
        amount_layout.addWidget(unit)
        form.addRow("Amount", amount_row)

        self._auto_fee = QCheckBox("Auto fee")
        self._auto_fee.setChecked(True)
        self._auto_fee.setEnabled(False)
        form.addRow("Fee", self._auto_fee)

        self._validation = QLabel("")
        self._validation.setStyleSheet("color: #d9534f;")
        form.addRow("", self._validation)

        root.addWidget(box)

        btn_row = QHBoxLayout()
        self._simulate_btn = QPushButton("Simulate")
        self._simulate_btn.clicked.connect(self._simulate)
        btn_row.addWidget(self._simulate_btn)

        self._send_btn = QPushButton("Send")
        self._send_btn.setObjectName("primaryButton")
        self._send_btn.clicked.connect(self._send)
        btn_row.addWidget(self._send_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._clear)
        btn_row.addWidget(self._clear_btn)
        btn_row.addStretch(1)

        self._copy_hash_btn = QPushButton("Copy Tx Hash")
        self._copy_hash_btn.setEnabled(False)
        self._copy_hash_btn.clicked.connect(self._copy_tx_hash)
        btn_row.addWidget(self._copy_hash_btn)

        self._explorer_btn = QPushButton("View on Explorer")
        self._explorer_btn.setEnabled(False)
        self._explorer_btn.clicked.connect(self._open_explorer)
        btn_row.addWidget(self._explorer_btn)

        root.addLayout(btn_row)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("Pipeline output…")
        self._log.setMinimumHeight(180)
        root.addWidget(self._log, 1)

    def reload_wallets(self) -> None:
        wallets = self._repo.load_wallets()
        current = self._from.currentData()
        self._from.clear()
        for wallet in wallets:
            text = f"{wallet.label}  ·  {wallet.address}"
            self._from.addItem(text, wallet.address)
        if current:
            idx = self._from.findData(current)
            if idx >= 0:
                self._from.setCurrentIndex(idx)

    def set_from_wallet(self, address: str | None) -> None:
        if not address:
            return
        self.reload_wallets()
        idx = self._from.findData(address)
        if idx >= 0:
            self._from.setCurrentIndex(idx)

    def _append_log(self, text: str) -> None:
        self._log.append(text)

    def _resolve_profile(self):
        return self._config.get_active_profile()

    def _validate_inputs(self) -> tuple[str, str, int] | None:
        from_addr = str(self._from.currentData() or "").strip()
        to_addr = self._to.text().strip()
        amount_text = self._amount.text().strip()
        if not from_addr:
            self._validation.setText("Select a source wallet.")
            return None
        if not TxService.validate_to_address(to_addr):
            self._validation.setText("Recipient must be a valid anim1… or 0x… address.")
            return None
        try:
            amount_wei = TxService.parse_amount(amount_text)
        except ValueError as exc:
            self._validation.setText(str(exc))
            return None
        self._validation.setText("")
        return from_addr, to_addr, amount_wei

    def _set_busy(self, busy: bool) -> None:
        self._send_btn.setEnabled(not busy)
        self._simulate_btn.setEnabled(not busy)
        self._clear_btn.setEnabled(not busy)

    def _simulate(self) -> None:
        validated = self._validate_inputs()
        if not validated:
            return
        _, to_addr, amount_wei = validated
        amount_anm = amount_wei / ANM_BASE_UNITS
        self._append_log(f"[simulate] to={to_addr} amount={amount_anm:g} ANM")

    def _send(self) -> None:
        validated = self._validate_inputs()
        if not validated:
            return
        from_addr, to_addr, amount_wei = validated
        profile = self._resolve_profile()
        rpc_url = profile.get_rpc_url()
        chain_id = profile.chain_id_expected

        self._set_busy(True)
        self._last_tx_hash = None
        self._copy_hash_btn.setEnabled(False)
        self._explorer_btn.setEnabled(False)
        self._append_log("Building transaction…")
        self._append_log("Signing transaction…")
        self._append_log("Submitting transaction…")

        self._thread = WorkerThread(
            self._tx_service.send_via_cli,
            from_addr=from_addr,
            to_addr=to_addr,
            amount_wei=amount_wei,
            rpc_url=rpc_url,
            chain_id=chain_id,
        )
        self._thread.worker.result.connect(self._on_send_result)
        self._thread.worker.error.connect(self._on_send_error)
        self._thread.worker.finished.connect(lambda: self._set_busy(False))
        self._thread.start()

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        self.reload_wallets()

    def _on_send_result(self, result: TxServiceResult) -> None:
        if result.ok:
            self._last_tx_hash = result.tx_hash
            self._append_log(f"✅ Success: {result.tx_hash}")
            self._copy_hash_btn.setEnabled(True)
            self._explorer_btn.setEnabled(True)
            return
        self._append_log(f"❌ Failed: {result.error or 'Unknown send error'}")
        if result.details:
            self._append_log(f"details: {result.details}")

    def _on_send_error(self, message: str, _traceback: str) -> None:
        self._append_log(f"❌ Worker error: {message}")

    def _clear(self) -> None:
        self._to.clear()
        self._amount.clear()
        self._validation.setText("")
        self._log.clear()

    def _copy_tx_hash(self) -> None:
        if not self._last_tx_hash:
            return
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415

        QApplication.clipboard().setText(self._last_tx_hash)

    def _open_explorer(self) -> None:
        if not self._last_tx_hash:
            return
        base = str((self._config.wallet_settings or {}).get("explorer_base_url", "")).strip().rstrip("/")
        if not base:
            QMessageBox.information(self, "Explorer", "Explorer URL not configured")
            return
        QDesktopServices.openUrl(QUrl(f"{base}/tx/{quote_plus(self._last_tx_hash)}"))
