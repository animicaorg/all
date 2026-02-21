from __future__ import annotations

from pathlib import Path
from PySide6.QtWidgets import QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from animica_studio.services.ena_automation_service import EnaService


class CheckpointsPage(QWidget):
    def __init__(self, service: EnaService, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Watch & Fetch Checkpoints")
        self.service = service
        root = QVBoxLayout(self)
        root.addWidget(QLabel("Tabs: latest | stable | experimental"))
        b = QPushButton("Download & Verify latest")
        b.clicked.connect(self._fetch)
        root.addWidget(b)
        self.out = QTextEdit(); self.out.setReadOnly(True); root.addWidget(self.out)

    def _fetch(self) -> None:
        out = self.service.fetch_latest_checkpoint(Path.cwd() / ".ena")
        run = out["run"]
        self.out.setPlainText(f"active={out.get('active')}\n" + "\n".join(f"{s.name}: {s.status}" for s in run.steps))
