"""IDE controller for build and deploy orchestration."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, QTimer


class IDEController(QObject):
    """Controller for IDE actions (build/deploy) with signals."""

    buildFinished = Signal(bool, str)
    deployFinished = Signal(bool, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    def build_project(self, workspace: str) -> None:
        """Simulate a build operation for now."""
        message = f"Build queued for workspace: {workspace or 'No workspace'}"
        QTimer.singleShot(300, lambda: self.buildFinished.emit(True, message))

    def deploy_project(self, workspace: str) -> None:
        """Simulate a deploy operation for now."""
        message = f"Deploy queued for workspace: {workspace or 'No workspace'}"
        QTimer.singleShot(300, lambda: self.deployFinished.emit(True, message))
