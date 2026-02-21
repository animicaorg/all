from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from animica_studio.services.ena_automation_service import EnaService


class TrainPage(QWidget):
    def __init__(self, service: EnaService, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Train Locally (CPU)")
        self.service = service
        root = QVBoxLayout(self)
        self.preset = QComboBox(); self.preset.addItems(["quick", "medium", "long"])
        root.addWidget(QLabel("Budget preset")); root.addWidget(self.preset)
        run_btn = QPushButton("Start training")
        run_btn.clicked.connect(self._run)
        root.addWidget(run_btn)
        self.out = QTextEdit(); self.out.setReadOnly(True); root.addWidget(self.out)

    def _run(self) -> None:
        checkpoints = self.service.list_checkpoints()
        cid = checkpoints[-1]["id"] if checkpoints else "base-stable"
        out = self.service.train_local(cid, "public-shards", preset=self.preset.currentText())
        run = out["run"]
        self.out.setPlainText("\n".join(f"{s.name}: {s.status}" for s in run.steps) + f"\n{out.get('recommendation', '')}")
