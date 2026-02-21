from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from animica_studio.services.ena_automation_service import EnaService
from animica_studio.services.ena_store import EnaStore
from animica_studio.storage.config import Config


class EnaDashboardPage(QWidget):
    def __init__(self, config: Config, service: EnaService | None = None, parent=None) -> None:
        super().__init__(parent)
        self.service = service or EnaService(config, EnaStore())
        self._status = QLabel()
        root = QVBoxLayout(self)
        root.addWidget(QLabel("ENA Guided Automation"))
        self._cap = QLabel("Checking capabilities...")
        root.addWidget(self._cap)
        for label, slot in [
            ("Contribute (CPU)", self._open_contribute),
            ("Watch & Fetch Checkpoints", self._open_checkpoints),
            ("Train Locally (CPU)", self._open_train),
            ("Publish to Network (DA)", self._open_publish),
            ("Use ENA (Inference)", self._open_infer),
            ("Auto mode", self._run_auto),
        ]:
            b = QPushButton(label)
            b.clicked.connect(slot)
            root.addWidget(b)
        root.addWidget(self._status)
        caps = self.service.detect_capabilities()
        self._cap.setText(f"Capabilities: AICF={caps.get('aicf')} DA={caps.get('da')} ENA={caps.get('ena')}")

    def _open_contribute(self) -> None:
        from animica_studio.ui.pages.contribute_page import ContributePage

        ContributePage(self.service, self).show()

    def _open_checkpoints(self) -> None:
        from animica_studio.ui.pages.checkpoints_page import CheckpointsPage

        CheckpointsPage(self.service, self).show()

    def _open_train(self) -> None:
        from animica_studio.ui.pages.train_page import TrainPage

        TrainPage(self.service, self).show()

    def _open_publish(self) -> None:
        from animica_studio.ui.pages.publish_page import PublishPage

        PublishPage(self.service, self).show()

    def _open_infer(self) -> None:
        from animica_studio.ui.pages.infer_page import InferPage

        InferPage(self.service, self).show()

    def _run_auto(self) -> None:
        out = self.service.run_auto_mode(__import__("pathlib").Path.cwd())
        self._status.setText(f"Auto mode done. Active checkpoint: {out.get('active_checkpoint', {}).get('id', 'n/a')}")
