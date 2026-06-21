"""Settings dialog for Animica Studio Qt.

:class:`SettingsDialog` edits every :class:`~animica_studio.config.Settings`
field: endpoint (with presets), API key (masked), model, provider kind,
streaming, temperature, max tokens, request timeout, autonomy level, and the
system-prompt override. It offers a **Test Connection** button that exercises
:meth:`AnimicaClient.test_connection` (run on a worker thread so the GUI stays
responsive), and persists the result to both the on-disk settings JSON and the
``db`` settings KV store on accept.

The dialog never logs the raw API key — it uses :func:`config.redact`.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import ENDPOINT_PRESETS, DEFAULT_MODELS, ProviderKind, Settings, redact
from ..models import AutonomyLevel

logger = logging.getLogger(__name__)

try:  # Database is only used for the type hint / persistence; import is optional.
    from ..db import Database
except Exception:  # pragma: no cover
    Database = object  # type: ignore[assignment, misc]


class _ConnectionTester(QObject):
    """Runs ``AnimicaClient.test_connection`` off the GUI thread."""

    finished = Signal(bool, str)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

    def run(self) -> None:
        ok, message = False, "Connection test failed."
        try:
            # Lazy import so this module imports headless without httpx side effects.
            from ..services.animica_client import AnimicaClient

            client = AnimicaClient(self._settings)
            ok, message = client.test_connection()
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            ok, message = False, f"{type(exc).__name__}: {exc}"
        self.finished.emit(bool(ok), str(message))


class SettingsDialog(QDialog):
    """Edit application settings; Test Connection; persist on accept."""

    def __init__(
        self,
        settings: Settings,
        db: Optional["Database"] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._db = db
        self._result_settings: Settings = settings
        self._thread: Optional[QThread] = None
        self._tester: Optional[_ConnectionTester] = None

        self.setObjectName("Panel")
        self.setProperty("panel", "true")
        self.setWindowTitle("Animica Studio — Settings")
        self.setModal(True)
        self.resize(620, 640)
        self._build_ui()
        self._load_from(settings)

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        title = QLabel("Settings", self)
        title.setObjectName("SectionHeader")
        root.addWidget(title)

        form = QFormLayout()
        form.setSpacing(8)

        # --- Endpoint preset + endpoint -------------------------------- #
        self._preset_combo = QComboBox(self)
        self._preset_combo.addItem("Custom…", userData=None)
        for url, kind in ENDPOINT_PRESETS:
            self._preset_combo.addItem(url, userData=(url, kind.value))
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        form.addRow("Endpoint preset", self._preset_combo)

        self._endpoint_edit = QLineEdit(self)
        self._endpoint_edit.setPlaceholderText("https://pool.animica.org/v1")
        form.addRow("Endpoint", self._endpoint_edit)

        # --- Provider kind --------------------------------------------- #
        self._provider_combo = QComboBox(self)
        for kind in ProviderKind:
            self._provider_combo.addItem(kind.value.title(), userData=kind.value)
        form.addRow("Provider kind", self._provider_combo)

        # --- API key (masked) ------------------------------------------ #
        key_row = QHBoxLayout()
        self._api_key_edit = QLineEdit(self)
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("Bearer API key (stored locally)")
        key_row.addWidget(self._api_key_edit, 1)
        self._show_key_btn = QPushButton("Show", self)
        self._show_key_btn.setProperty("class", "ghost")
        self._show_key_btn.setProperty("variant", "ghost")
        self._show_key_btn.setCheckable(True)
        self._show_key_btn.toggled.connect(self._on_toggle_key_visibility)
        key_row.addWidget(self._show_key_btn)
        key_container = QWidget(self)
        key_container.setLayout(key_row)
        form.addRow("API key", key_container)

        # --- Model ----------------------------------------------------- #
        self._model_combo = QComboBox(self)
        self._model_combo.setEditable(True)
        for m in DEFAULT_MODELS:
            self._model_combo.addItem(m)
        form.addRow("Model", self._model_combo)

        # --- Streaming ------------------------------------------------- #
        self._streaming_check = QCheckBox("Stream tokens as they arrive", self)
        form.addRow("Streaming", self._streaming_check)

        # --- Temperature ----------------------------------------------- #
        self._temp_spin = QDoubleSpinBox(self)
        self._temp_spin.setRange(0.0, 2.0)
        self._temp_spin.setSingleStep(0.05)
        self._temp_spin.setDecimals(2)
        form.addRow("Temperature", self._temp_spin)

        # --- Max tokens ------------------------------------------------ #
        self._max_tokens_spin = QSpinBox(self)
        self._max_tokens_spin.setRange(1, 1_000_000)
        self._max_tokens_spin.setSingleStep(256)
        form.addRow("Max tokens", self._max_tokens_spin)

        # --- Request timeout ------------------------------------------- #
        self._timeout_spin = QDoubleSpinBox(self)
        self._timeout_spin.setRange(1.0, 3600.0)
        self._timeout_spin.setSingleStep(5.0)
        self._timeout_spin.setDecimals(1)
        self._timeout_spin.setSuffix(" s")
        form.addRow("Request timeout", self._timeout_spin)

        # --- Autonomy level -------------------------------------------- #
        self._autonomy_combo = QComboBox(self)
        for level in AutonomyLevel:
            self._autonomy_combo.addItem(level.label, userData=level.value)
        form.addRow("Autonomy level", self._autonomy_combo)

        root.addLayout(form)

        # --- System prompt override ------------------------------------ #
        prompt_label = QLabel("System prompt override (blank = built-in agent persona)", self)
        prompt_label.setObjectName("SectionHeader")
        root.addWidget(prompt_label)
        self._prompt_edit = QPlainTextEdit(self)
        self._prompt_edit.setObjectName("CodeArea")
        self._prompt_edit.setProperty("class", "code")
        self._prompt_edit.setPlaceholderText(
            "Leave empty to use the default Animica Studio Agent system prompt."
        )
        self._prompt_edit.setMinimumHeight(110)
        root.addWidget(self._prompt_edit)

        # --- Test connection ------------------------------------------- #
        test_row = QHBoxLayout()
        self._test_btn = QPushButton("Test Connection", self)
        self._test_btn.setProperty("class", "accent")
        self._test_btn.setProperty("variant", "accent")
        self._test_btn.clicked.connect(self._on_test_connection)
        test_row.addWidget(self._test_btn)
        self._test_status = QLabel("", self)
        self._test_status.setObjectName("StatusOk")
        self._test_status.setWordWrap(True)
        test_row.addWidget(self._test_status, 1)
        root.addLayout(test_row)

        # --- Buttons --------------------------------------------------- #
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------------------------------------------ #
    # Load / collect
    # ------------------------------------------------------------------ #
    def _load_from(self, settings: Settings) -> None:
        self._endpoint_edit.setText(settings.endpoint)
        self._sync_preset_to_endpoint(settings.endpoint)
        self._set_combo_data(self._provider_combo, settings.provider_kind.value)
        self._api_key_edit.setText(settings.api_key)
        if settings.model:
            idx = self._model_combo.findText(settings.model)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
            else:
                self._model_combo.setEditText(settings.model)
        self._streaming_check.setChecked(settings.streaming)
        self._temp_spin.setValue(float(settings.temperature))
        self._max_tokens_spin.setValue(int(settings.max_tokens))
        self._timeout_spin.setValue(float(settings.request_timeout))
        self._set_combo_data(self._autonomy_combo, settings.autonomy_level.value)
        self._prompt_edit.setPlainText(settings.system_prompt_override or "")

    def _collect(self) -> Settings:
        provider = ProviderKind.from_value(self._provider_combo.currentData())
        autonomy = AutonomyLevel.from_value(self._autonomy_combo.currentData())
        override = self._prompt_edit.toPlainText().strip() or None
        return replace(
            self._settings,
            endpoint=self._endpoint_edit.text().strip()
            or "https://pool.animica.org/v1",
            api_key=self._api_key_edit.text(),
            model=self._model_combo.currentText().strip() or DEFAULT_MODELS[0],
            provider_kind=provider,
            streaming=self._streaming_check.isChecked(),
            temperature=float(self._temp_spin.value()),
            max_tokens=int(self._max_tokens_spin.value()),
            request_timeout=float(self._timeout_spin.value()),
            autonomy_level=autonomy,
            system_prompt_override=override,
        )

    def result_settings(self) -> Settings:
        """Return the settings as edited (valid after :meth:`accept`)."""
        return self._result_settings

    # ------------------------------------------------------------------ #
    # Slots
    # ------------------------------------------------------------------ #
    def _on_preset_changed(self, _index: int) -> None:
        data = self._preset_combo.currentData()
        if not data:
            return
        url, kind = data
        self._endpoint_edit.setText(url)
        self._set_combo_data(self._provider_combo, kind)

    def _sync_preset_to_endpoint(self, endpoint: str) -> None:
        for i in range(self._preset_combo.count()):
            data = self._preset_combo.itemData(i)
            if data and data[0] == endpoint:
                self._preset_combo.setCurrentIndex(i)
                return
        self._preset_combo.setCurrentIndex(0)  # Custom…

    def _on_toggle_key_visibility(self, shown: bool) -> None:
        self._api_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
        )
        self._show_key_btn.setText("Hide" if shown else "Show")

    def _on_test_connection(self) -> None:
        if self._thread is not None:
            return  # a test is already running
        settings = self._collect()
        self._test_status.setObjectName("StatusBusy")
        self._test_status.setText("Testing connection…")
        self._test_btn.setEnabled(False)
        logger.info(
            "Testing connection: %s", redact(settings.endpoint, settings.api_key)
        )

        self._thread = QThread(self)
        self._tester = _ConnectionTester(settings)
        self._tester.moveToThread(self._thread)
        self._thread.started.connect(self._tester.run)
        self._tester.finished.connect(self._on_test_finished)
        self._thread.start()

    def _on_test_finished(self, ok: bool, message: str) -> None:
        self._test_status.setObjectName("StatusOk" if ok else "StatusError")
        self._test_status.setText(("OK — " if ok else "Failed — ") + redact(message))
        # Re-polish so the new objectName-based style applies.
        style = self._test_status.style()
        if style is not None:
            style.unpolish(self._test_status)
            style.polish(self._test_status)
        self._test_btn.setEnabled(True)
        self._teardown_thread()

    def _teardown_thread(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
            self._thread.deleteLater()
        self._thread = None
        self._tester = None

    def _on_accept(self) -> None:
        self._result_settings = self._collect()
        try:
            self._result_settings.save()
        except Exception as exc:  # pragma: no cover - disk dependent
            logger.warning("Failed to save settings to disk: %s", exc)
        self._persist_to_db(self._result_settings)
        self.accept()

    def _persist_to_db(self, settings: Settings) -> None:
        if self._db is None:
            return
        try:
            setter = getattr(self._db, "set_setting_json", None)
            if callable(setter):
                setter("settings", settings.to_dict())
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to persist settings to db: %s", exc)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _set_combo_data(combo: QComboBox, value: object) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._teardown_thread()
        super().closeEvent(event)


__all__ = ["SettingsDialog"]
