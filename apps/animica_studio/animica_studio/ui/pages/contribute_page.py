from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from animica_studio.services.ena_automation_service import EnaService


class ContributePage(QWidget):
    def __init__(self, service: EnaService, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Contribute (CPU)")
        self.service = service
        root = QVBoxLayout(self)
        row = QHBoxLayout()
        self.type_box = QComboBox(); self.type_box.addItems(["dataset", "eval"])
        self.intensity = QComboBox(); self.intensity.addItems(["low", "medium", "high"])
        run_btn = QPushButton("Start flow")
        run_btn.clicked.connect(self._run)
        row.addWidget(QLabel("Type")); row.addWidget(self.type_box); row.addWidget(QLabel("Intensity")); row.addWidget(self.intensity); row.addWidget(run_btn)
        root.addLayout(row)
        self.out = QTextEdit(); self.out.setReadOnly(True); root.addWidget(self.out)

    def _run(self) -> None:
        out = self.service.run_contribute_flow(Path.cwd(), self.type_box.currentText(), self.intensity.currentText())
        run = out["run"]
        lines = [f"Run {run.run_id} status={run.status}"]
        for s in run.steps:
            lines.append(f"- {s.name}: {s.status} ({s.copy_command or 'n/a'})")
        lines.append(f"Receipt: {out['receipt']}")
        self.out.setPlainText("\n".join(lines))
