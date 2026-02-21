from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from animica_studio.services.ena_automation_service import EnaService


class InferPage(QWidget):
    def __init__(self, service: EnaService, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Use ENA (Inference)")
        self.service = service
        root = QVBoxLayout(self)
        self.network = QCheckBox("Run on network (small ANM fee)")
        root.addWidget(self.network)
        self.prompt = QTextEdit(); self.prompt.setPlaceholderText("Prompt")
        root.addWidget(self.prompt)
        b = QPushButton("Run inference")
        b.clicked.connect(self._run)
        root.addWidget(b)
        self.out = QLabel()
        root.addWidget(self.out)

    def _run(self) -> None:
        out = self.service.infer(self.prompt.toPlainText().strip() or "hello", network_mode=self.network.isChecked())
        self.out.setText(f"mode={out['mode']} latency={out['latency_ms']}ms tokens={out['tokens']} fee={out['fees']['fee_total_anm']}")
