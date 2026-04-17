"""IDE page — Monaco editor embedded via QWebEngineView with QWebChannel bridge."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.ide_service import IdeService
from animica_studio.services.template_service import TemplateService
from animica_studio.services.token_template_service import TokenTemplateService
from animica_studio.services.vm_toolchain_service import VmToolchainService
from animica_studio.storage.config import load_config, save_config
from animica_studio.ui.dialogs.template_dialog import NewFromTemplateDialog
from animica_studio.ui.dialogs.token_template_wizard import TokenTemplateWizard
from animica_studio.ui.widgets.ena_panel import EnaPanel
from animica_studio.ui.widgets.stream_console import StreamConsole

log = logging.getLogger(__name__)

_WEB_DIR = Path(__file__).parent.parent / "web"


def _try_import_webengine():
    """Attempt to import QtWebEngineWidgets; return None tuple on failure."""
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: PLC0415
        from PySide6.QtWebEngineCore import QWebEngineSettings  # noqa: PLC0415
        from PySide6.QtWebChannel import QWebChannel  # noqa: PLC0415
        return QWebEngineView, QWebEngineSettings, QWebChannel
    except ImportError:
        return None, None, None


@dataclass
class _TabInfo:
    rel_path: str
    editor: QWidget
    dirty: bool = False
    cursor_pos: int = 0


class IdePage(QWidget):
    """IDE page with Monaco editor, project tree, and script runner."""

    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self._svc = IdeService()
        self._cfg = load_config()
        self._template_service = TemplateService(user_templates_dir=self._cfg.templates_user_path)
        self._template_service.load_builtin_templates()
        self._template_service.load_user_templates()
        self._token_template_service = TokenTemplateService()
        self._toolchain = VmToolchainService()
        self._current_rel_path = ""
        self._QWebEngineView, self._QWebEngineSettings, self._QWebChannel = _try_import_webengine()
        self._webview = None
        self._bridge = None
        self._plain_editor = None
        self._editor_tabs = None
        self._open_tabs: dict[str, _TabInfo] = {}
        self._build_ui()
        self._restore_workspace_from_config()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        root.addWidget(self._build_toolbar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_tree_panel())

        right = QSplitter(Qt.Orientation.Vertical)
        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(self._build_editor_area())
        top.addWidget(self._build_ena_panel())
        top.setSizes([720, 320])
        right.addWidget(top)
        right.addWidget(self._build_output_panel())
        right.setSizes([500, 200])
        splitter.addWidget(right)
        splitter.setSizes([220, 780])
        root.addWidget(splitter, stretch=1)

        root.addWidget(self._build_status_bar())

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        row.addWidget(QLabel("Workspace:"))
        self._ws_label = QLabel("(none)")
        self._ws_label.setObjectName("headerMeta")
        row.addWidget(self._ws_label, stretch=1)

        change_btn = QPushButton("📂 Change…")
        change_btn.clicked.connect(self._on_change_workspace)
        row.addWidget(change_btn)

        new_file_btn = QPushButton("📄 New File")
        new_file_btn.clicked.connect(self._on_new_file)
        row.addWidget(new_file_btn)

        new_template_btn = QPushButton("🧩 New from Template")
        new_template_btn.clicked.connect(self._on_new_from_template)
        row.addWidget(new_template_btn)

        new_token_btn = QPushButton("🪙 New Token…")
        new_token_btn.clicked.connect(self._on_new_token_template)
        row.addWidget(new_token_btn)

        new_dir_btn = QPushButton("📁 New Folder")
        new_dir_btn.clicked.connect(self._on_new_folder)
        row.addWidget(new_dir_btn)

        self._save_btn = QPushButton("💾 Save")
        self._save_btn.clicked.connect(self._on_save)
        row.addWidget(self._save_btn)

        self._save_all_btn = QPushButton("💾 Save All")
        self._save_all_btn.clicked.connect(self._on_save_all)
        row.addWidget(self._save_all_btn)

        self._compile_btn = QPushButton("🧱 Compile")
        self._compile_btn.clicked.connect(self._on_compile_current)
        row.addWidget(self._compile_btn)

        self._run_btn = QPushButton("▶ Run")
        self._run_btn.clicked.connect(self._on_run_script)
        row.addWidget(self._run_btn)

        explore_btn = QPushButton("🗂 Open Folder")
        explore_btn.clicked.connect(self._on_open_folder)
        row.addWidget(explore_btn)

        return bar

    def _build_tree_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(180)
        panel.setMaximumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(QLabel("📁 Project"))

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.itemDoubleClicked.connect(self._on_tree_double_click)
        self._tree.itemActivated.connect(self._on_tree_activated)
        self._tree.itemExpanded.connect(self._on_tree_item_expanded)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        layout.addWidget(self._tree, stretch=1)
        return panel

    def _build_editor_area(self) -> QWidget:
        if self._QWebEngineView is not None:
            return self._build_webengine_editor()
        return self._build_fallback_editor()

    def _build_webengine_editor(self) -> QWidget:
        """Build the Monaco editor using QWebEngineView."""
        from animica_studio.services.ide_bridge import IdeBridge  # noqa: PLC0415

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self._webview = self._QWebEngineView()  # type: ignore[misc]

        # Security: restrict web features
        settings = self._webview.settings()
        settings.setAttribute(self._QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(self._QWebEngineSettings.WebAttribute.LocalStorageEnabled, False)
        settings.setAttribute(
            self._QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False
        )

        # Set up QWebChannel
        self._channel = self._QWebChannel(self._webview.page())
        self._bridge = IdeBridge(self._svc, parent=self)
        self._channel.registerObject("bridge", self._bridge)
        self._webview.page().setWebChannel(self._channel)

        # Load local IDE HTML
        ide_html = _WEB_DIR / "ide.html"
        if ide_html.exists():
            self._webview.load(QUrl.fromLocalFile(str(ide_html)))
        else:
            self._webview.setHtml(
                "<html><body style='color:white;background:#1e1e2e;font-family:monospace'>"
                "<h3>Monaco assets not found</h3>"
                "<p>Run <code>python scripts/setup_monaco.py</code> to download Monaco.</p>"
                "</body></html>"
            )

        layout.addWidget(self._webview)
        return container

    def _build_fallback_editor(self) -> QWidget:
        """Fallback: plain text editor when QtWebEngine is unavailable."""
        from PySide6.QtGui import QFont  # noqa: PLC0415

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        banner = QLabel(
            "⚠️  QtWebEngine not available — using plain text editor. "
            "Install PySide6-WebEngine for full Monaco IDE."
        )
        banner.setWordWrap(True)
        banner.setStyleSheet("background:#45475a; color:#f9e2af; padding:4px;")
        layout.addWidget(banner)

        self._plain_editor = QPlainTextEdit()
        font = QFont("Courier New", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._plain_editor.setFont(font)

        self._editor_tabs = QTabWidget()
        self._editor_tabs.setTabsClosable(True)
        self._editor_tabs.tabCloseRequested.connect(self._close_editor_tab)
        self._editor_tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._editor_tabs, stretch=1)

        self._save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self._save_shortcut.activated.connect(self._on_save)
        self._save_all_shortcut = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        self._save_all_shortcut.activated.connect(self._on_save_all)
        return container

    def _build_output_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(QLabel("📤 Output"))
        self._output = StreamConsole()
        layout.addWidget(self._output, stretch=1)
        return panel


    def _build_ena_panel(self) -> QWidget:
        panel = EnaPanel(
            get_workspace=lambda: self._svc.workspace,
            get_current_file_text=self._get_current_file_and_text,
            get_selection_text=self._get_selection_text,
            get_cursor_position=self._get_cursor_position,
            get_open_tabs=self._get_open_tabs,
            ena_config=self._cfg.ena,
            parent=self,
        )
        return panel

    def _get_current_file_and_text(self) -> tuple[str, str]:
        rel = self._current_rel_path
        if not rel:
            return "", ""
        try:
            return rel, self._svc.read_file(rel)
        except Exception:
            return rel, ""

    def _get_selection_text(self) -> str:
        if self._plain_editor is not None:
            return self._plain_editor.textCursor().selectedText()
        return ""


    def _get_cursor_position(self) -> tuple[int, int]:
        info = self._current_tab_info()
        if info is None:
            return 1, 1
        cursor = info.editor.textCursor()
        return cursor.blockNumber() + 1, cursor.positionInBlock() + 1

    def _get_open_tabs(self) -> list[str]:
        if self._open_tabs:
            return [info.rel_path for info in self._open_tabs.values()]
        return list(self._cfg.ide_open_tabs or [])

    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        self._status_bar_label = QLabel("Ready")
        self._status_bar_label.setObjectName("headerMeta")
        row.addWidget(self._status_bar_label)
        self._status_meta = QLabel(f"VM: {self._toolchain.get_vm_version()}")
        self._status_meta.setObjectName("headerMeta")
        row.addWidget(self._status_meta)
        row.addStretch()
        return bar


    def new_script_from_template(self) -> None:
        self._on_new_from_template()

    def new_token_from_template(self) -> None:
        self._on_new_token_template()


    # ------------------------------------------------------------------
    # Workspace
    # ------------------------------------------------------------------

    def _restore_workspace_from_config(self) -> None:
        configured = self._cfg.ide_workspace_root or self._cfg.workspace_root
        if not configured:
            return
        try:
            self._svc.set_workspace(configured)
        except ValueError:
            log.debug("IdePage: ignoring invalid configured workspace %s", configured)
            return
        self._ws_label.setText(configured)
        self._ws_label.setToolTip(configured)
        self._refresh_tree()
        if self._bridge is not None:
            self._bridge.setWorkspace(configured)
        self._restore_editor_session_from_config()

    def _persist_workspace(self, path: str) -> None:
        self._cfg.ide_workspace_root = path
        self._cfg.workspace_root = path
        self._persist_editor_state()

    def _persist_editor_state(self) -> None:
        if self._open_tabs:
            self._cfg.ide_open_tabs = [info.rel_path for info in self._open_tabs.values()]
        if self._current_rel_path:
            self._cfg.ide_last_active_file = self._current_rel_path
        try:
            save_config(self._cfg)
        except Exception as exc:  # noqa: BLE001
            log.debug("IdePage: failed to persist editor state: %s", exc)

    def _restore_editor_session_from_config(self) -> None:
        ws = self._svc.workspace
        if ws is None:
            return
        restored_any = False
        for rel_path in list(self._cfg.ide_open_tabs or [])[:8]:
            if not rel_path:
                continue
            target = ws / rel_path
            if target.exists() and target.is_file():
                self.open_file_in_editor(rel_path, focus=False)
                restored_any = True
        active = str(self._cfg.ide_last_active_file or "").strip()
        if active:
            target = ws / active
            if target.exists() and target.is_file():
                self.open_file_in_editor(active, focus=True)
                return
        if restored_any and self._cfg.ide_open_tabs:
            self.open_file_in_editor(self._cfg.ide_open_tabs[0], focus=True)

    def _remember_open_file(self, rel_path: str) -> None:
        rel_path = str(rel_path or "").strip()
        if not rel_path:
            return
        self._current_rel_path = rel_path
        recent = [rel_path]
        recent.extend(item for item in list(self._cfg.ide_recent_files or []) if item != rel_path)
        self._cfg.ide_recent_files = recent[:20]
        tracked_tabs = [item for item in list(self._cfg.ide_open_tabs or []) if item != rel_path]
        tracked_tabs.append(rel_path)
        self._cfg.ide_open_tabs = tracked_tabs[:12]
        self._cfg.ide_last_active_file = rel_path
        self._persist_editor_state()

    def _reset_editor_session(self, *, persist: bool = True) -> None:
        self._current_rel_path = ""
        self._cfg.ide_open_tabs = []
        self._cfg.ide_last_active_file = None
        if self._webview is not None:
            self._webview.page().runJavaScript(
                "if(window.resetStudioEditor){window.resetStudioEditor();}"
            )
        if self._editor_tabs is not None:
            while self._editor_tabs.count():
                widget = self._editor_tabs.widget(0)
                self._editor_tabs.removeTab(0)
                if widget is not None:
                    widget.deleteLater()
            self._open_tabs.clear()
        self._status_bar_label.setText("Ready")
        if persist:
            self._persist_editor_state()

    def _on_change_workspace(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Workspace Folder")
        if not path:
            return
        try:
            self._svc.set_workspace(path)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Workspace", str(exc))
            return
        self._reset_editor_session(persist=False)
        self._ws_label.setText(path)
        self._ws_label.setToolTip(path)
        self._refresh_tree()
        if self._bridge is not None:
            self._bridge.setWorkspace(path)
        self._persist_workspace(path)

    def _refresh_tree(self) -> None:
        self._tree.clear()
        ws = self._svc.workspace
        if ws is None:
            return
        try:
            entries = self._svc.list_dir(".")
        except Exception as exc:  # noqa: BLE001
            log.warning("IdePage: tree refresh failed: %s", exc)
            return
        root_item = QTreeWidgetItem([ws.name])
        root_item.setData(0, Qt.ItemDataRole.UserRole, ".")
        self._tree.addTopLevelItem(root_item)
        self._populate_tree_item(root_item, entries)
        root_item.setExpanded(True)
        self._sync_tree_selection(self._current_rel_path)

    def _on_tree_item_expanded(self, item: QTreeWidgetItem) -> None:
        self._load_tree_children_if_needed(item)

    def _load_tree_children_if_needed(self, item: QTreeWidgetItem) -> None:
        if item.childCount() != 1 or item.child(0).data(0, Qt.ItemDataRole.UserRole) is not None:
            return
        rel_path = item.data(0, Qt.ItemDataRole.UserRole)
        if not rel_path:
            return
        item.takeChildren()
        try:
            entries = self._svc.list_dir(rel_path)
        except Exception as exc:  # noqa: BLE001
            log.debug("IdePage: list dir failed for %s: %s", rel_path, exc)
            return
        self._populate_tree_item(item, entries)

    def _populate_tree_item(self, parent: QTreeWidgetItem, entries: list[dict]) -> None:
        for entry in entries:
            item = QTreeWidgetItem([entry["name"]])
            item.setData(0, Qt.ItemDataRole.UserRole, entry["path"])
            if entry["is_dir"]:
                item.setIcon(0, self.style().standardIcon(
                    self.style().StandardPixmap.SP_DirIcon
                ))
                placeholder = QTreeWidgetItem(["..."])
                placeholder.setData(0, Qt.ItemDataRole.UserRole, None)
                item.addChild(placeholder)
            else:
                item.setIcon(0, self.style().standardIcon(
                    self.style().StandardPixmap.SP_FileIcon
                ))
            parent.addChild(item)

    def _on_tree_double_click(self, item: QTreeWidgetItem, _col: int) -> None:
        self._open_selected_tree_item(item)

    def _on_tree_activated(self, item: QTreeWidgetItem, _col: int) -> None:
        self._open_selected_tree_item(item)

    def _open_selected_tree_item(self, item: QTreeWidgetItem) -> None:
        rel_path = item.data(0, Qt.ItemDataRole.UserRole)
        if not rel_path or rel_path == ".":
            return
        if self._is_dir(rel_path):
            item.setExpanded(not item.isExpanded())
            self._load_tree_children_if_needed(item)
            return
        self.open_file_in_editor(rel_path, focus=True)

    def open_file_in_editor(self, rel_path: str, focus: bool = True) -> None:
        ws = self._svc.workspace
        if ws is None:
            return
        abs_path = str((ws / rel_path).resolve())

        if self._webview is not None:
            script = f"if(window.openFileFromHost){{window.openFileFromHost({rel_path!r}, {abs_path!r});}}"
            self._webview.page().runJavaScript(script)
            self._current_rel_path = rel_path
            self._status_bar_label.setText(rel_path)
            self._sync_tree_selection(rel_path)
            self._remember_open_file(rel_path)
            return

        if self._editor_tabs is None:
            return

        existing = self._open_tabs.get(abs_path)
        if existing is not None:
            idx = self._editor_tabs.indexOf(existing.editor)
            if idx >= 0:
                self._editor_tabs.setCurrentIndex(idx)
                if focus:
                    existing.editor.setFocus()
                self._current_rel_path = existing.rel_path
                self._status_bar_label.setText(existing.rel_path)
                self._sync_tree_selection(existing.rel_path)
                self._remember_open_file(existing.rel_path)
            return

        try:
            content = self._svc.read_file(rel_path)
        except Exception as exc:  # noqa: BLE001
            log.debug("IdePage: cannot read %s: %s", rel_path, exc)
            return

        editor = QPlainTextEdit()
        editor.setPlainText(content)
        editor.textChanged.connect(lambda: self._mark_tab_dirty(abs_path, True))
        editor.cursorPositionChanged.connect(lambda: self._update_cursor(abs_path))

        tab_idx = self._editor_tabs.addTab(editor, Path(rel_path).name)
        self._editor_tabs.setTabToolTip(tab_idx, rel_path)
        self._open_tabs[abs_path] = _TabInfo(rel_path=rel_path, editor=editor)
        self._editor_tabs.setCurrentIndex(tab_idx)
        if focus:
            editor.setFocus()
        self._current_rel_path = rel_path
        self._status_bar_label.setText(rel_path)
        self._sync_tree_selection(rel_path)
        self._remember_open_file(rel_path)

    def _open_relative_file(self, rel_path: str) -> None:
        self.open_file_in_editor(rel_path, focus=True)

    def _on_tree_context_menu(self, pos: "QPoint") -> None:
        from PySide6.QtWidgets import QMenu  # noqa: PLC0415
        item = self._tree.itemAt(pos)
        if not item:
            return
        rel_path = item.data(0, Qt.ItemDataRole.UserRole) or "."
        menu = QMenu(self)
        open_action = menu.addAction("Open")
        menu.addSeparator()
        new_file_action = menu.addAction("New File…")
        new_dir_action = menu.addAction("New Folder…")
        menu.addSeparator()
        rename_action = menu.addAction("Rename…")
        delete_action = menu.addAction("Delete")
        menu.addSeparator()
        copy_path_action = menu.addAction("Copy path")
        action = menu.exec(self._tree.viewport().mapToGlobal(pos))
        if action == open_action:
            self._open_selected_tree_item(item)
        elif action == new_file_action:
            self._prompt_create_file(rel_path)
        elif action == new_dir_action:
            self._prompt_create_dir(rel_path)
        elif action == rename_action:
            self._prompt_rename(rel_path)
        elif action == delete_action:
            self._confirm_delete(rel_path)
        elif action == copy_path_action:
            ws = self._svc.workspace
            full = str((ws / rel_path).resolve()) if ws is not None else rel_path
            QGuiApplication.clipboard().setText(full)

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def _on_new_file(self) -> None:
        self._prompt_create_file(".")

    def _on_new_from_template(self) -> None:
        if self._svc.workspace is None:
            QMessageBox.information(self, "Template", "Select a workspace first.")
            return
        dlg = NewFromTemplateDialog(self._template_service, self)
        if dlg.exec() != dlg.DialogCode.Accepted or dlg.selection() is None:
            return
        sel = dlg.selection()
        assert sel is not None
        template = self._template_service.get(sel.template_id)
        try:
            rendered = self._template_service.render(sel.template_id, sel.params)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Template", str(exc))
            return
        filename = template.default_filename
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Create Script From Template",
            str(self._svc.workspace / filename),
            "Python Files (*.py);;All Files (*)",
        )
        if not filename:
            return
        ws = self._svc.workspace
        if ws is None:
            return
        try:
            rel = str(Path(filename).resolve().relative_to(ws.resolve()))
        except Exception:
            QMessageBox.warning(self, "Template", "Target file must be inside workspace.")
            return
        try:
            self._svc.write_file(rel, rendered)
            self._refresh_tree()
            self.open_file_in_editor(rel)
            self._status_bar_label.setText(rel)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Template", str(exc))

    def _on_new_token_template(self) -> None:
        if self._svc.workspace is None:
            QMessageBox.information(self, "Token Template", "Select a workspace first.")
            return
        dlg = TokenTemplateWizard(self._token_template_service, self._svc.workspace, self)
        if dlg.exec() != dlg.DialogCode.Accepted or dlg.selection() is None:
            return
        sel = dlg.selection()
        assert sel is not None
        try:
            rendered = self._token_template_service.render(sel.template_id, sel.params)
            written = self._token_template_service.write_to_project(
                rendered,
                self._svc.workspace / sel.output_dir,
                overwrite=False,
            )
        except FileExistsError as exc:
            reply = QMessageBox.question(
                self,
                "Files exist",
                f"{exc}\nOverwrite existing files?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            rendered = self._token_template_service.render(sel.template_id, sel.params)
            written = self._token_template_service.write_to_project(
                rendered,
                self._svc.workspace / sel.output_dir,
                overwrite=True,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Token Template", str(exc))
            return
        self._refresh_tree()
        template = self._token_template_service.get(sel.template_id)
        if sel.open_after_create:
            main_path = (self._svc.workspace / sel.output_dir / template.main_file).resolve()
            try:
                rel = str(main_path.relative_to(self._svc.workspace.resolve())).replace('\\', '/')
                self._open_relative_file(rel)
            except Exception:
                pass
        QMessageBox.information(self, "Token Template", f"Generated {len(written)} files.")

    def _on_new_folder(self) -> None:
        self._prompt_create_dir(".")

    def _prompt_create_file(self, parent_rel: str) -> None:
        name, ok = QInputDialog.getText(self, "New File", "File name:")
        if not ok or not name:
            return
        rel = name if parent_rel == "." else f"{parent_rel}/{name}"
        try:
            self._svc.create_file(rel)
            self._refresh_tree()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Error", str(exc))

    def _prompt_create_dir(self, parent_rel: str) -> None:
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok or not name:
            return
        rel = name if parent_rel == "." else f"{parent_rel}/{name}"
        try:
            self._svc.create_dir(rel)
            self._refresh_tree()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Error", str(exc))

    def _prompt_rename(self, old_rel: str) -> None:
        old_name = Path(old_rel).name
        new_name, ok = QInputDialog.getText(self, "Rename", "New name:", text=old_name)
        if not ok or not new_name or new_name == old_name:
            return
        new_rel = str(Path(old_rel).parent / new_name)
        try:
            self._svc.rename_path(old_rel, new_rel)
            self._refresh_tree()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Error", str(exc))

    def _confirm_delete(self, rel_path: str) -> None:
        reply = QMessageBox.question(
            self,
            "Delete",
            f"Delete '{rel_path}'? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._svc.delete_path(rel_path)
            self._refresh_tree()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Error", str(exc))

    def _on_save(self) -> None:
        if self._webview is not None:
            self._webview.page().runJavaScript("if(window.saveCurrentFile) saveCurrentFile();")
            if self._current_rel_path:
                self._remember_open_file(self._current_rel_path)
            return
        current = self._current_tab_info()
        if current is None:
            QMessageBox.information(self, "Save", "No file open.")
            return
        content = current.editor.toPlainText()
        try:
            self._svc.write_file(current.rel_path, content)
            self._mark_tab_dirty(self._abs_path(current.rel_path), False)
            self._status_bar_label.setText(f"{current.rel_path} [saved]")
            self._remember_open_file(current.rel_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Save Error", str(exc))

    def _on_save_all(self) -> None:
        if self._webview is not None:
            self._webview.page().runJavaScript("if(window.saveAllFiles) saveAllFiles();")
            return
        for abs_path, tab_info in list(self._open_tabs.items()):
            if not tab_info.dirty:
                continue
            try:
                self._svc.write_file(tab_info.rel_path, tab_info.editor.toPlainText())
                self._mark_tab_dirty(abs_path, False)
                self._remember_open_file(tab_info.rel_path)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Save Error", f"{tab_info.rel_path}: {exc}")

    def _with_current_file_saved(self, action_title: str, callback) -> None:  # type: ignore[no-untyped-def]
        if self._webview is not None:
            self._webview.page().runJavaScript(
                "window.getCurrentEditorState ? window.getCurrentEditorState() : ''",
                lambda raw: self._handle_web_editor_state(raw, action_title, callback),
            )
            return
        current = self._current_tab_info()
        if current is None:
            QMessageBox.information(self, action_title, "No file open.")
            return
        try:
            self._svc.write_file(current.rel_path, current.editor.toPlainText())
            self._mark_tab_dirty(self._abs_path(current.rel_path), False)
            self._status_bar_label.setText(f"{current.rel_path} [saved]")
            self._remember_open_file(current.rel_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, f"{action_title} Error", str(exc))
            return
        callback(current.rel_path)

    def _handle_web_editor_state(self, raw: object, action_title: str, callback) -> None:  # type: ignore[no-untyped-def]
        try:
            payload = json.loads(str(raw or "{}"))
        except Exception:
            payload = {}
        rel_path = str(payload.get("path") or "").strip()
        content = str(payload.get("content") or "")
        if not rel_path:
            QMessageBox.information(self, action_title, "No file open in editor.")
            return
        try:
            self._svc.write_file(rel_path, content)
            self._status_bar_label.setText(f"{rel_path} [saved]")
            self._remember_open_file(rel_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, f"{action_title} Error", str(exc))
            return
        callback(rel_path)

    def _on_compile_current(self) -> None:
        self._with_current_file_saved("Compile", self._compile_file)

    def _compile_file(self, rel_path: str) -> None:
        ws = self._svc.workspace
        if ws is None:
            QMessageBox.warning(self, "Compile", "No workspace selected.")
            return

        full_path = ws / rel_path
        self._output.clear()
        self._output.set_running(True)
        self._output.append_system(f"Compiling {rel_path}")
        self._status_meta.setText(f"VM: {self._toolchain.get_vm_version()} | compiling")

        def _task() -> dict[str, object]:
            started = time.perf_counter()
            compile_result = self._toolchain.compile_contract(
                full_path,
                {"manifest": str(ws / "manifest.json"), "out_dir": str(ws / "build")},
            )
            build_result = None
            if Path(rel_path).name in {"contract.py", "main.py"}:
                build_result = self._toolchain.build_package(ws)
            duration_ms = int((time.perf_counter() - started) * 1000)
            return {
                "compile": compile_result,
                "build": build_result,
                "duration_ms": duration_ms,
                "target": rel_path,
            }

        from animica_studio.services.workers import WorkerThread  # noqa: PLC0415

        worker = WorkerThread(_task)
        worker.worker.result.connect(self._on_compile_result)
        worker.worker.error.connect(self._on_compile_error)
        worker.start()

    def _on_compile_result(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        compile_result = data.get("compile")
        build_result = data.get("build")
        duration_ms = int(data.get("duration_ms") or 0)
        target = str(data.get("target") or self._current_rel_path or "(unknown)")

        self._output.append_system(f"Compile target: {target}")
        if compile_result is not None:
            success = bool(getattr(compile_result, "success", False))
            bytecode_hash = str(getattr(compile_result, "bytecode_hash", "") or "")
            raw_output = str(getattr(compile_result, "raw_output", "") or "").strip()
            diagnostics = list(getattr(compile_result, "diagnostics", []) or [])
            if success:
                self._output.append_system("Compile succeeded.")
                if bytecode_hash:
                    self._output.append_system(f"Bytecode hash: {bytecode_hash}")
            else:
                self._output.append_error("Compile failed.")
            for line in self._format_diagnostics(diagnostics):
                self._output.append_line(line)
            if raw_output:
                self._output.append_system(raw_output)
            if build_result is not None and getattr(build_result, "success", False):
                package_path = str(getattr(build_result, "package_path", "") or "")
                manifest_path = str(getattr(build_result, "manifest_path", "") or "")
                if package_path:
                    self._output.append_system(f"Artifact: {package_path}")
                if manifest_path:
                    self._output.append_system(f"Build manifest: {manifest_path}")
            self._output.set_exit_status(0 if success else 1, duration_ms, False)
            self._status_meta.setText(
                f"VM: {self._toolchain.get_vm_version()} | {'ready' if success else 'compile error'}"
            )
            return

        self._output.append_error("Compile returned no result.")
        self._output.set_exit_status(1, duration_ms, False)
        self._status_meta.setText(f"VM: {self._toolchain.get_vm_version()} | compile error")

    def _on_compile_error(self, message: str, traceback_text: str) -> None:
        self._output.append_error(message)
        if traceback_text:
            self._output.append_line(traceback_text)
        self._output.set_exit_status(1, 0, False)
        self._status_meta.setText(f"VM: {self._toolchain.get_vm_version()} | compile error")

    def _format_diagnostics(self, diagnostics: list[object]) -> list[str]:
        lines: list[str] = []
        for diag in diagnostics:
            file = str(getattr(diag, "file", "") or "")
            line = int(getattr(diag, "line", 0) or 0)
            column = int(getattr(diag, "column", 0) or 0)
            severity = str(getattr(diag, "severity", "info") or "info").upper()
            code = str(getattr(diag, "code", "") or "")
            message = str(getattr(diag, "message", "") or "").strip()
            hint = str(getattr(diag, "hint", "") or "").strip()
            location = ""
            if file:
                location = file
                if line > 0:
                    location += f":{line}"
                    if column > 0:
                        location += f":{column}"
            prefix = " ".join(part for part in [severity, code, location] if part).strip()
            lines.append(f"{prefix} {message}".strip())
            if hint:
                lines.append(f"HINT {hint}")
        return lines

    def _on_run_script(self) -> None:
        self._with_current_file_saved("Run", self._do_run_script)

    def _do_run_script(self, rel_path: str) -> None:
        from animica_studio.services.deterministic_runner import DeterministicRunner  # noqa: PLC0415
        from animica_studio.services.workers import WorkerThread  # noqa: PLC0415
        from animica_studio.util.cancel import CancelToken  # noqa: PLC0415

        ws = self._svc.workspace
        if ws is None:
            QMessageBox.warning(self, "Run Script", "No workspace selected.")
            return

        full_path = str(ws / rel_path)
        self._output.clear()
        token = CancelToken()
        self._output.set_cancel_token(token)
        self._output.set_running(True)
        output = self._output
        output.append_system(f"Running {rel_path}")
        self._status_meta.setText(f"VM: {self._toolchain.get_vm_version()} | running")

        runner = DeterministicRunner()

        def _task():
            return runner.run_script(
                full_path,
                on_line=lambda line: output.append_line(line),
                cancel_token=token,
            )

        worker = WorkerThread(_task)
        worker.worker.result.connect(
            lambda r: (
                output.set_exit_status(r.exit_code, r.duration_ms, r.cancelled),
                self._status_meta.setText(
                    f"VM: {self._toolchain.get_vm_version()} | {'ready' if r.success else 'run error'}"
                ),
            )
        )
        worker.worker.error.connect(
            lambda msg, _tb: (
                output.append_line(f"[error] {msg}"),
                self._status_meta.setText(f"VM: {self._toolchain.get_vm_version()} | run error"),
            )
        )
        worker.start()

    def _on_open_folder(self) -> None:
        ws = self._svc.workspace
        if ws is None:
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(ws)])  # noqa: S603, S607
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(ws)])  # noqa: S603, S607
            else:
                subprocess.Popen(["xdg-open", str(ws)])  # noqa: S603, S607
        except OSError as exc:
            log.warning("IdePage: cannot open folder: %s", exc)


    def _current_tab_info(self) -> _TabInfo | None:
        if self._editor_tabs is None:
            return None
        editor = self._editor_tabs.currentWidget()
        if editor is None:
            return None
        for info in self._open_tabs.values():
            if info.editor is editor:
                return info
        return None

    def _close_editor_tab(self, index: int) -> None:
        if self._editor_tabs is None:
            return
        editor = self._editor_tabs.widget(index)
        abs_path = None
        tab_info = None
        for key, info in self._open_tabs.items():
            if info.editor is editor:
                abs_path = key
                tab_info = info
                break
        if abs_path is None or tab_info is None:
            return

        if tab_info.dirty:
            reply = QMessageBox.question(
                self,
                "Unsaved changes",
                f"Save changes to {tab_info.rel_path}?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            if reply == QMessageBox.StandardButton.Save:
                try:
                    self._svc.write_file(tab_info.rel_path, tab_info.editor.toPlainText())
                    self._mark_tab_dirty(abs_path, False)
                except Exception as exc:  # noqa: BLE001
                    QMessageBox.warning(self, "Save Error", str(exc))
                    return

        self._editor_tabs.removeTab(index)
        self._open_tabs.pop(abs_path, None)
        remaining = [info.rel_path for info in self._open_tabs.values()]
        self._cfg.ide_open_tabs = remaining
        if self._current_rel_path == tab_info.rel_path:
            self._current_rel_path = remaining[0] if remaining else ""
            self._cfg.ide_last_active_file = self._current_rel_path or None
        self._persist_editor_state()

    def _on_tab_changed(self, _index: int) -> None:
        info = self._current_tab_info()
        if info is None:
            return
        self._current_rel_path = info.rel_path
        self._status_bar_label.setText(info.rel_path)
        cursor = info.editor.textCursor()
        cursor.setPosition(info.cursor_pos)
        info.editor.setTextCursor(cursor)
        self._sync_tree_selection(info.rel_path)
        self._remember_open_file(info.rel_path)

    def _mark_tab_dirty(self, abs_path: str, dirty: bool) -> None:
        info = self._open_tabs.get(abs_path)
        if info is None:
            return
        info.dirty = dirty
        if self._editor_tabs is None:
            return
        idx = self._editor_tabs.indexOf(info.editor)
        if idx < 0:
            return
        label = Path(info.rel_path).name
        self._editor_tabs.setTabText(idx, f"*{label}" if dirty else label)

    def _update_cursor(self, abs_path: str) -> None:
        info = self._open_tabs.get(abs_path)
        if info is not None:
            info.cursor_pos = info.editor.textCursor().position()

    def _sync_tree_selection(self, rel_path: str) -> None:
        if not rel_path:
            return
        item = self._find_tree_item_by_rel_path(rel_path)
        if item is None:
            return
        self._tree.setCurrentItem(item)
        self._tree.scrollToItem(item)

    def _find_tree_item_by_rel_path(self, rel_path: str) -> QTreeWidgetItem | None:
        root = self._tree.topLevelItem(0)
        if root is None:
            return None
        stack = [root]
        while stack:
            item = stack.pop()
            if item.data(0, Qt.ItemDataRole.UserRole) == rel_path:
                return item
            for i in range(item.childCount()):
                stack.append(item.child(i))
        return None

    def _is_dir(self, rel_path: str) -> bool:
        ws = self._svc.workspace
        if ws is None:
            return False
        return (ws / rel_path).is_dir()

    def _abs_path(self, rel_path: str) -> str:
        ws = self._svc.workspace
        if ws is None:
            return rel_path
        return str((ws / rel_path).resolve())
