"""Git panel UI for the IDE."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from animica_miner_gui.ide.git_integration import GitRepo, GitStatus, build_pr_url


class GitPanel(QGroupBox):
    """Minimal git UI for staging, committing, and pushing."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Git", parent)
        self._repo: Optional[GitRepo] = None
        self._status = GitStatus(False, None, "", None, False)
        self._updating = False

        self.status_label = QLabel("Git: not available")
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.refresh_button = QToolButton()
        self.refresh_button.setText("↻")
        self.refresh_button.clicked.connect(self.refresh)

        status_row = QHBoxLayout()
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        status_row.addWidget(self.refresh_button)

        self.file_list = QListWidget()
        self.file_list.itemChanged.connect(self._toggle_stage)

        stage_all_button = QPushButton("Stage All")
        stage_all_button.clicked.connect(self._stage_all)
        unstage_all_button = QPushButton("Unstage All")
        unstage_all_button.clicked.connect(self._unstage_all)

        stage_row = QHBoxLayout()
        stage_row.addWidget(stage_all_button)
        stage_row.addWidget(unstage_all_button)

        self.commit_input = QLineEdit()
        self.commit_input.setPlaceholderText("Commit message")
        self.commit_button = QPushButton("Commit")
        self.commit_button.clicked.connect(self._commit)

        commit_row = QHBoxLayout()
        commit_row.addWidget(self.commit_input)
        commit_row.addWidget(self.commit_button)

        self.push_button = QPushButton("Push")
        self.push_button.clicked.connect(self._push)
        self.pr_button = QPushButton("Open PR")
        self.pr_button.clicked.connect(self._open_pr)

        push_row = QHBoxLayout()
        push_row.addWidget(self.push_button)
        push_row.addWidget(self.pr_button)

        layout = QVBoxLayout(self)
        layout.addLayout(status_row)
        layout.addWidget(self.file_list)
        layout.addLayout(stage_row)
        layout.addLayout(commit_row)
        layout.addLayout(push_row)

        self._apply_status(self._status)

    def set_workspace(self, workspace: str) -> None:
        self._repo = None
        workspace_path = Path(workspace)
        repo_root = GitRepo.discover_repo_root(workspace_path)
        if repo_root:
            self._repo = GitRepo(repo_root)
        self.refresh()

    def refresh(self) -> None:
        if not self._repo:
            status = GitStatus(False, None, "", None, False, "Not a git repository.")
            self._apply_status(status)
            self.file_list.clear()
            return
        self._apply_status(self._repo.get_status())
        self._refresh_files()

    def _apply_status(self, status: GitStatus) -> None:
        self._status = status
        if not status.available or not status.repo_root:
            self.status_label.setText(status.message or "Git unavailable")
            self._set_controls_enabled(False)
            return
        state = "dirty" if status.dirty else "clean"
        upstream = f" → {status.upstream}" if status.upstream else ""
        message = status.message or f"{status.branch}{upstream} • {state}"
        self.status_label.setText(message)
        self._set_controls_enabled(True)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.file_list.setEnabled(enabled)
        self.commit_input.setEnabled(enabled)
        self.commit_button.setEnabled(enabled)
        self.push_button.setEnabled(enabled)
        self.pr_button.setEnabled(enabled)

    def _refresh_files(self) -> None:
        self._updating = True
        self.file_list.clear()
        if not self._repo:
            self._updating = False
            return
        for entry in self._repo.list_files():
            label = f"[{entry.status}] {entry.path}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, entry.path)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if entry.staged else Qt.Unchecked)
            if entry.unstaged and not entry.staged:
                item.setForeground(Qt.GlobalColor.darkYellow)
            self.file_list.addItem(item)
        self._updating = False

    def _toggle_stage(self, item: QListWidgetItem) -> None:
        if self._updating or not self._repo:
            return
        path = item.data(Qt.UserRole)
        if not path:
            return
        if item.checkState() == Qt.Checked:
            result = self._repo.stage_files([path])
        else:
            result = self._repo.unstage_files([path])
        if not result.ok:
            QMessageBox.warning(self, "Git", result.message)
        self.refresh()

    def _stage_all(self) -> None:
        if not self._repo:
            return
        result = self._repo.stage_all()
        if not result.ok:
            QMessageBox.warning(self, "Git", result.message)
        self.refresh()

    def _unstage_all(self) -> None:
        if not self._repo:
            return
        result = self._repo.unstage_all()
        if not result.ok:
            QMessageBox.warning(self, "Git", result.message)
        self.refresh()

    def _commit(self) -> None:
        if not self._repo:
            return
        message = self.commit_input.text().strip()
        if not message:
            QMessageBox.warning(self, "Commit", "Enter a commit message.")
            return
        result = self._repo.commit(message)
        if not result.ok:
            QMessageBox.warning(self, "Commit", result.message)
            return
        self.commit_input.clear()
        self.refresh()

    def _push(self) -> None:
        if not self._repo:
            return
        if not self._status.upstream:
            remotes = self._repo.list_remotes()
            if not remotes:
                QMessageBox.warning(self, "Push", "No git remotes configured.")
                return
            remote = remotes[0]
            if len(remotes) > 1:
                chosen, ok = QInputDialog.getItem(self, "Select Remote", "Remote:", remotes, 0, False)
                if not ok:
                    return
                remote = str(chosen)
            branch = self._status.branch if self._status.branch != "detached" else None
            if not branch:
                QMessageBox.warning(self, "Push", "Detached HEAD; check out a branch first.")
                return
            result = self._repo.push(remote=remote, branch=branch)
        else:
            result = self._repo.push()
        if not result.ok:
            QMessageBox.warning(self, "Push", result.message)
        self.refresh()

    def _open_pr(self) -> None:
        if not self._repo:
            return
        remotes = self._repo.list_remotes()
        if not remotes:
            QMessageBox.warning(self, "Open PR", "No git remotes configured.")
            return
        remote_url = self._repo.remote_url(remotes[0]) or ""
        url = build_pr_url(remote_url, self._status.branch)
        if not url:
            QMessageBox.warning(self, "Open PR", "Unsupported remote URL for PR helper.")
            return
        QDesktopServices.openUrl(QUrl(url))
