from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from animica_studio.services.ena_automation_service import EnaService


class PublishPage(QWidget):
    def __init__(self, service: EnaService, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Publish to Network (DA)")
        self.service = service
        root = QVBoxLayout(self)
        self.dev = QCheckBox("Use local dev DA stub")
        root.addWidget(self.dev)
        b = QPushButton("Publish selected checkpoint")
        b.clicked.connect(self._run)
        root.addWidget(b)
        self.out = QTextEdit(); self.out.setReadOnly(True); root.addWidget(self.out)

    def _run(self) -> None:
        cps = self.service.list_checkpoints()
        if not cps:
            self.out.setPlainText("No local checkpoints available")
            return
        out = self.service.publish_checkpoint(cps[-1]["sha256"], dev_mode=self.dev.isChecked())
        self.out.setPlainText(str(out))
