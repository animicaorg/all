"""Main application window for Animica Studio Qt.

:class:`MainWindow` assembles the full IDE shell: a top toolbar, a left sidebar
(projects / file tree / recent chats / ecosystem links), a center work area
(editor tabs + chat toggle + diff viewer), a right dock (agent plan / tool calls
/ context / build status), a bottom dock (terminal / problems / logs), and a
status bar.

It is built against the CONTRACTS signatures. The concrete panel classes
(``ChatPanel``, ``EditorPanel``, ``FileTreePanel``, ``TerminalPanel``,
``SettingsDialog``, ``DiffViewer``, ``ProjectWizard``) are implemented by other
agents; this module imports them *lazily and defensively* so the window always
constructs headless even before those panels exist. Where a panel is missing a
small inert placeholder is substituted, so the shell remains importable and
runnable under ``QT_QPA_PLATFORM=offscreen``.
"""
from __future__ import annotations

import logging
import webbrowser
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..config import ECOSYSTEM_LINKS, Settings
from ..models import (
    ApprovalDecision,
    AutonomyLevel,
    CommandApproval,
    FileDiff,
    PlanStep,
    Project,
    Session,
)

logger = logging.getLogger("animica_studio.ui.main_window")


# --------------------------------------------------------------------------- #
# Defensive panel imports — never let a missing/broken panel crash the shell.
# --------------------------------------------------------------------------- #
def _placeholder_widget(title: str, note: str = "") -> QWidget:
    """A small inert widget used when a real panel cannot be imported."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(12, 12, 12, 12)
    lbl = QLabel(title)
    lbl.setObjectName("SectionHeader")
    lay.addWidget(lbl)
    if note:
        sub = QLabel(note)
        sub.setWordWrap(True)
        lay.addWidget(sub)
    lay.addStretch(1)
    return w


def _try_import(module: str, name: str):
    """Import ``name`` from ``animica_studio.ui.<module>`` or return ``None``."""
    try:
        mod = __import__(f"animica_studio.ui.{module}", fromlist=[name])
        return getattr(mod, name, None)
    except Exception as exc:  # pragma: no cover - depends on sibling agents
        logger.debug("Optional UI panel %s.%s unavailable: %s", module, name, exc)
        return None


# --------------------------------------------------------------------------- #
# Approval dialog
# --------------------------------------------------------------------------- #
class ApprovalDialog(QMessageBox):
    """Modal dialog asking the user to approve a risky command/action.

    Offers *Approve once* / *Deny* / *Always allow* and reports the chosen
    :class:`~animica_studio.models.ApprovalDecision`.
    """

    def __init__(
        self,
        action: str,
        category: str = "general",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setIcon(QMessageBox.Icon.Warning)
        self.setWindowTitle("Approval required")
        self.setText("Animica wants to run this command / action:")
        self.setInformativeText(
            f"{action}\n\nCategory: {category}\n\n"
            "Approve once, deny, or always allow this category for this project."
        )
        self._approve = self.addButton("Approve once", QMessageBox.ButtonRole.AcceptRole)
        self._always = self.addButton("Always allow", QMessageBox.ButtonRole.YesRole)
        self._deny = self.addButton("Deny", QMessageBox.ButtonRole.RejectRole)
        self.setDefaultButton(self._deny)
        self._decision: ApprovalDecision = ApprovalDecision.DENY

    def decision(self) -> ApprovalDecision:
        return self._decision

    def exec(self) -> int:  # type: ignore[override]
        result = super().exec()
        clicked = self.clickedButton()
        if clicked is self._approve:
            self._decision = ApprovalDecision.APPROVE_ONCE
        elif clicked is self._always:
            self._decision = ApprovalDecision.ALWAYS_ALLOW
        else:
            self._decision = ApprovalDecision.DENY
        return result


# --------------------------------------------------------------------------- #
# Right dock: agent plan / tool calls / context / build status
# --------------------------------------------------------------------------- #
class AgentPanel(QWidget):
    """Right-side dock showing the agent plan, live tool calls and build status."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("RightPanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        plan_header = QLabel("Agent Plan")
        plan_header.setObjectName("SectionHeader")
        lay.addWidget(plan_header)
        self.plan_list = QListWidget()
        self.plan_list.setObjectName("PlanList")
        lay.addWidget(self.plan_list, 2)

        tools_header = QLabel("Tool Calls")
        tools_header.setObjectName("SectionHeader")
        lay.addWidget(tools_header)
        self.tool_list = QListWidget()
        self.tool_list.setObjectName("ToolCallList")
        lay.addWidget(self.tool_list, 2)

        status_header = QLabel("Build Status")
        status_header.setObjectName("SectionHeader")
        lay.addWidget(status_header)
        self.build_status = QLabel("Idle")
        self.build_status.setObjectName("StatusOk")
        self.build_status.setWordWrap(True)
        lay.addWidget(self.build_status)

    # -- slots ---------------------------------------------------------- #
    def set_plan(self, steps: list[dict]) -> None:
        self.plan_list.clear()
        for raw in steps or []:
            title = ""
            done = False
            if isinstance(raw, dict):
                title = str(raw.get("title") or raw.get("detail") or "")
                done = bool(raw.get("done"))
            else:
                title = str(getattr(raw, "title", ""))
                done = bool(getattr(raw, "done", False))
            item = QListWidgetItem(("[x] " if done else "[ ] ") + title)
            self.plan_list.addItem(item)

    def add_tool_call(self, payload: dict) -> None:
        name = str(payload.get("name", "tool")) if isinstance(payload, dict) else "tool"
        status = str(payload.get("status", "")) if isinstance(payload, dict) else ""
        self.tool_list.addItem(f"-> {name} ({status})")
        self.tool_list.scrollToBottom()

    def finish_tool_call(self, payload: dict) -> None:
        name = str(payload.get("name", "tool")) if isinstance(payload, dict) else "tool"
        status = str(payload.get("status", "")) if isinstance(payload, dict) else ""
        ok = status in ("ok", "OK") if status else True
        mark = "OK" if ok else status or "done"
        self.tool_list.addItem(f"   {name}: {mark}")
        self.tool_list.scrollToBottom()

    def set_build_status(self, text: str, *, ok: bool = True, error: bool = False) -> None:
        self.build_status.setText(text)
        self.build_status.setObjectName(
            "StatusError" if error else ("StatusOk" if ok else "StatusBusy")
        )
        self.build_status.style().unpolish(self.build_status)
        self.build_status.style().polish(self.build_status)


# --------------------------------------------------------------------------- #
# Bottom dock: terminal / problems / logs
# --------------------------------------------------------------------------- #
class BottomPanel(QTabWidget):
    """Bottom dock with terminal, problems and logs tabs."""

    def __init__(self, terminal_widget: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("BottomPanel")
        self.addTab(terminal_widget, "Terminal")

        self.problems = QListWidget()
        self.problems.setObjectName("ProblemsList")
        self.addTab(self.problems, "Problems")

        self.logs = QListWidget()
        self.logs.setObjectName("LogsList")
        self.addTab(self.logs, "Logs")

    def log(self, text: str) -> None:
        self.logs.addItem(text)
        self.logs.scrollToBottom()

    def add_problem(self, text: str) -> None:
        self.problems.addItem(text)
        self.problems.scrollToBottom()


# --------------------------------------------------------------------------- #
# MainWindow
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    """The application's main window / IDE shell."""

    projectChanged = Signal(str)  # root_path

    def __init__(
        self,
        db: Any,
        settings: Settings,
        agent_engine: Optional[QObject] = None,
    ) -> None:
        super().__init__()
        self.db = db
        self.settings = settings
        self.agent_engine = agent_engine

        self.setObjectName("MainWindow")
        self.setWindowTitle("Animica Studio Qt")
        self.resize(1440, 900)

        # Services — reuse the engine's where possible, else create our own.
        self.project_service = self._resolve_project_service()
        self.command_runner = self._resolve_command_runner()
        self.git_service = None  # built lazily once a project root is known

        self.current_project: Optional[Project] = None
        self.current_session: Optional[Session] = None

        # Build UI.
        self._build_central()
        self._build_toolbar()
        self._build_sidebar()
        self._build_right_dock()
        self._build_bottom_dock()
        self._build_status_bar()

        # Wire signals.
        self._wire_project_service()
        self._wire_agent_engine()

        # Initial population.
        self._reload_projects()
        self._reload_sessions()
        self._ensure_session()
        self.set_status("Ready", kind="ok")

    # ------------------------------------------------------------------ #
    # Service resolution
    # ------------------------------------------------------------------ #
    def _resolve_project_service(self):
        engine_ps = getattr(self.agent_engine, "_project_service", None)
        if engine_ps is not None:
            return engine_ps
        try:
            from ..services.project_service import ProjectService

            return ProjectService(self.db)
        except Exception as exc:  # pragma: no cover
            logger.warning("ProjectService unavailable: %s", exc)
            return None

    def _resolve_command_runner(self):
        engine_cr = getattr(self.agent_engine, "_command_runner", None)
        if engine_cr is not None:
            return engine_cr
        try:
            from ..services.command_runner import CommandRunner

            return CommandRunner()
        except Exception as exc:  # pragma: no cover
            logger.warning("CommandRunner unavailable: %s", exc)
            return None

    def _make_git_service(self, root: str):
        try:
            from ..services.git_service import GitService

            return GitService(root)
        except Exception as exc:  # pragma: no cover
            logger.warning("GitService unavailable: %s", exc)
            return None

    # ------------------------------------------------------------------ #
    # Central work area: editor tabs + chat toggle + diff viewer
    # ------------------------------------------------------------------ #
    def _build_central(self) -> None:
        EditorPanel = _try_import("editor_panel", "EditorPanel")
        ChatPanel = _try_import("chat_panel", "ChatPanel")
        DiffViewer = _try_import("diff_viewer", "DiffViewer")

        # Editor.
        if EditorPanel is not None and self.project_service is not None:
            try:
                self.editor_panel = EditorPanel(self.project_service)
            except Exception as exc:  # pragma: no cover
                logger.warning("EditorPanel failed: %s", exc)
                self.editor_panel = _placeholder_widget("Editor", "Editor panel unavailable.")
        else:
            self.editor_panel = _placeholder_widget("Editor", "Open a project to edit files.")

        # Chat.
        if ChatPanel is not None and self.agent_engine is not None:
            try:
                self.chat_panel = ChatPanel(self.agent_engine, self.db)
            except Exception as exc:  # pragma: no cover
                logger.warning("ChatPanel failed: %s", exc)
                self.chat_panel = _placeholder_widget("Ask Animica", "Chat panel unavailable.")
        else:
            self.chat_panel = _placeholder_widget(
                "Ask Animica", "Agent engine not available."
            )

        # Diff viewer.
        if DiffViewer is not None:
            try:
                self.diff_viewer = DiffViewer()
            except Exception as exc:  # pragma: no cover
                logger.warning("DiffViewer failed: %s", exc)
                self.diff_viewer = _placeholder_widget("Diff", "Diff viewer unavailable.")
        else:
            self.diff_viewer = _placeholder_widget("Diff", "No pending changes.")

        # Center stack lets us toggle between editor+chat split and the diff view.
        self.center_split = QSplitter(Qt.Orientation.Horizontal)
        self.center_split.setObjectName("CenterSplit")
        self.center_split.addWidget(self.editor_panel)
        self.center_split.addWidget(self.chat_panel)
        self.center_split.setStretchFactor(0, 3)
        self.center_split.setStretchFactor(1, 2)

        self.center_stack = QStackedWidget()
        self.center_stack.addWidget(self.center_split)  # index 0 — work view
        self.center_stack.addWidget(self.diff_viewer)   # index 1 — diff view

        self.setCentralWidget(self.center_stack)

    def toggle_chat(self) -> None:
        """Show/hide the chat panel within the center split."""
        visible = self.chat_panel.isVisible()
        self.chat_panel.setVisible(not visible)

    def show_diff_view(self) -> None:
        self.center_stack.setCurrentIndex(1)

    def show_work_view(self) -> None:
        self.center_stack.setCurrentIndex(0)

    # ------------------------------------------------------------------ #
    # Top toolbar
    # ------------------------------------------------------------------ #
    def _build_toolbar(self) -> None:
        bar = QToolBar("Main")
        bar.setObjectName("TopToolBar")
        bar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        self.toolbar = bar

        def add(text: str, slot: Callable[[], None], shortcut: Optional[str] = None) -> QAction:
            act = QAction(text, self)
            act.triggered.connect(slot)  # type: ignore[arg-type]
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            bar.addAction(act)
            return act

        self.act_new_project = add("New Project", self.on_new_project, "Ctrl+Shift+N")
        self.act_open_project = add("Open Project", self.on_open_project, "Ctrl+O")
        bar.addSeparator()
        self.act_new_chat = add("New Chat", self.on_new_chat, "Ctrl+N")
        self.act_save = add("Save", self.on_save, "Ctrl+S")
        bar.addSeparator()
        self.act_run = add("Run", self.on_run, "Ctrl+R")
        self.act_build = add("Build", self.on_build, "Ctrl+B")
        bar.addSeparator()
        self.act_ask = add("Ask Animica", self.on_ask_animica)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)

        # Model picker.
        self.model_combo = QComboBox()
        self.model_combo.setObjectName("ModelPicker")
        self.model_combo.setEditable(True)
        models = list(getattr(self.settings, "model", None) and (self.settings.model,) or ())
        from ..config import DEFAULT_MODELS

        for m in DEFAULT_MODELS:
            if m not in models:
                models.append(m)
        self.model_combo.addItems(models or list(DEFAULT_MODELS))
        if self.settings.model:
            self.model_combo.setCurrentText(self.settings.model)
        self.model_combo.currentTextChanged.connect(self.on_model_changed)
        bar.addWidget(QLabel("Model:"))
        bar.addWidget(self.model_combo)

        # Autonomy selector.
        self.autonomy_combo = QComboBox()
        self.autonomy_combo.setObjectName("AutonomyPicker")
        for level in AutonomyLevel:
            self.autonomy_combo.addItem(level.label, level.value)
        idx = self.autonomy_combo.findData(self.settings.autonomy_level.value)
        if idx >= 0:
            self.autonomy_combo.setCurrentIndex(idx)
        self.autonomy_combo.currentIndexChanged.connect(self.on_autonomy_changed)
        bar.addWidget(QLabel("Autonomy:"))
        bar.addWidget(self.autonomy_combo)

        bar.addSeparator()
        self.act_settings = add("Settings", self.on_settings)

    # ------------------------------------------------------------------ #
    # Left sidebar
    # ------------------------------------------------------------------ #
    def _build_sidebar(self) -> None:
        FileTreePanel = _try_import("file_tree_panel", "FileTreePanel")

        panel = QFrame()
        panel.setObjectName("Sidebar")
        panel.setMinimumWidth(240)
        panel.setMaximumWidth(420)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 12, 10, 12)
        lay.setSpacing(8)

        brand = QLabel("Animica Studio")
        brand.setObjectName("BrandTitle")
        lay.addWidget(brand)

        # Projects.
        proj_header = QLabel("Projects")
        proj_header.setObjectName("SectionHeader")
        lay.addWidget(proj_header)
        self.projects_list = QListWidget()
        self.projects_list.setObjectName("ProjectsList")
        self.projects_list.setMaximumHeight(150)
        self.projects_list.itemActivated.connect(self._on_project_item_activated)
        self.projects_list.itemClicked.connect(self._on_project_item_activated)
        lay.addWidget(self.projects_list)

        # File tree.
        tree_header = QLabel("Files")
        tree_header.setObjectName("SectionHeader")
        lay.addWidget(tree_header)
        if FileTreePanel is not None and self.project_service is not None:
            try:
                self.file_tree_panel = FileTreePanel(self.project_service)
            except Exception as exc:  # pragma: no cover
                logger.warning("FileTreePanel failed: %s", exc)
                self.file_tree_panel = _placeholder_widget("Files", "File tree unavailable.")
        else:
            self.file_tree_panel = _placeholder_widget("Files", "Open a project.")
        lay.addWidget(self.file_tree_panel, 3)

        # Recent chats.
        chats_header = QLabel("Recent Chats")
        chats_header.setObjectName("SectionHeader")
        lay.addWidget(chats_header)
        self.sessions_list = QListWidget()
        self.sessions_list.setObjectName("SessionsList")
        self.sessions_list.setMaximumHeight(150)
        self.sessions_list.itemActivated.connect(self._on_session_item_activated)
        self.sessions_list.itemClicked.connect(self._on_session_item_activated)
        lay.addWidget(self.sessions_list)

        # Ecosystem links.
        links_header = QLabel("Animica Ecosystem")
        links_header.setObjectName("SectionHeader")
        lay.addWidget(links_header)
        for label, url in ECOSYSTEM_LINKS:
            link = QLabel(f'<a href="{url}" style="color:#16d9c3;">{label}</a>')
            link.setObjectName("EcosystemLink")
            link.setProperty("role", "link")
            link.setOpenExternalLinks(False)
            link.linkActivated.connect(self._open_link)
            lay.addWidget(link)

        lay.addStretch(1)

        dock = QDockWidget("Workspace", self)
        dock.setObjectName("SidebarDock")
        dock.setWidget(panel)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.sidebar_dock = dock

    # ------------------------------------------------------------------ #
    # Right dock
    # ------------------------------------------------------------------ #
    def _build_right_dock(self) -> None:
        self.agent_panel = AgentPanel()
        dock = QDockWidget("Agent", self)
        dock.setObjectName("RightDock")
        dock.setWidget(self.agent_panel)
        dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.right_dock = dock

    # ------------------------------------------------------------------ #
    # Bottom dock
    # ------------------------------------------------------------------ #
    def _build_bottom_dock(self) -> None:
        TerminalPanel = _try_import("terminal_panel", "TerminalPanel")
        if TerminalPanel is not None and self.command_runner is not None:
            try:
                self.terminal_panel = TerminalPanel(self.command_runner)
            except Exception as exc:  # pragma: no cover
                logger.warning("TerminalPanel failed: %s", exc)
                self.terminal_panel = _placeholder_widget("Terminal", "Terminal unavailable.")
        else:
            self.terminal_panel = _placeholder_widget("Terminal", "Command runner unavailable.")

        self.bottom_panel = BottomPanel(self.terminal_panel)
        dock = QDockWidget("Console", self)
        dock.setObjectName("BottomDock")
        dock.setWidget(self.bottom_panel)
        dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        self.bottom_dock = dock

    # ------------------------------------------------------------------ #
    # Status bar
    # ------------------------------------------------------------------ #
    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        self.status_label = QLabel("Starting…")
        self.status_label.setObjectName("StatusOk")
        sb.addWidget(self.status_label, 1)

    def set_status(self, text: str, *, kind: str = "ok") -> None:
        obj = {"ok": "StatusOk", "busy": "StatusBusy", "error": "StatusError"}.get(
            kind, "StatusOk"
        )
        self.status_label.setText(text)
        self.status_label.setObjectName(obj)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    # ------------------------------------------------------------------ #
    # Signal wiring
    # ------------------------------------------------------------------ #
    def _wire_project_service(self) -> None:
        ps = self.project_service
        if ps is None:
            return
        try:
            ps.projectOpened.connect(self._on_project_opened)  # type: ignore[union-attr]
        except Exception:
            pass
        # File tree activation -> open in editor.
        ftp = getattr(self, "file_tree_panel", None)
        sig = getattr(ftp, "fileActivated", None)
        if sig is not None:
            try:
                sig.connect(self.open_file_in_editor)
            except Exception:
                pass
        # Editor save requests.
        ep = getattr(self, "editor_panel", None)
        save_sig = getattr(ep, "saveRequested", None)
        if save_sig is not None:
            try:
                save_sig.connect(self._on_editor_save_requested)
            except Exception:
                pass

    def _wire_agent_engine(self) -> None:
        eng = self.agent_engine
        if eng is None:
            return
        connections = {
            "toolCallStarted": self._on_tool_call_started,
            "toolCallFinished": self._on_tool_call_finished,
            "approvalRequested": self._on_approval_requested,
            "planReady": self._on_plan_ready,
            "statusChanged": self._on_engine_status,
            "errorOccurred": self._on_engine_error,
            "diffProposed": self._on_diff_proposed,
            "busyChanged": self._on_engine_busy,
        }
        for name, slot in connections.items():
            sig = getattr(eng, name, None)
            if sig is not None:
                try:
                    sig.connect(slot)
                except Exception as exc:  # pragma: no cover
                    logger.debug("Could not connect engine.%s: %s", name, exc)

    # ------------------------------------------------------------------ #
    # Project actions
    # ------------------------------------------------------------------ #
    def on_open_project(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Open Project", str(Path.home()))
        if directory:
            self.open_project(directory)

    def on_new_project(self) -> None:
        ProjectWizard = _try_import("project_wizard", "ProjectWizard")
        templates_service = self._make_templates_service()
        if ProjectWizard is not None and templates_service is not None:
            try:
                wizard = ProjectWizard(templates_service)
                sig = getattr(wizard, "projectCreated", None)
                if sig is not None:
                    sig.connect(self.open_project)
                # Dialog-like wizards expose exec(); widgets fall back to show().
                if hasattr(wizard, "exec"):
                    wizard.exec()
                else:  # pragma: no cover
                    wizard.show()
                return
            except Exception as exc:  # pragma: no cover
                logger.warning("ProjectWizard failed: %s", exc)
        # Fallback: pick an existing/empty directory.
        directory = QFileDialog.getExistingDirectory(
            self, "New Project Folder", str(Path.home())
        )
        if directory:
            self.open_project(directory)

    def _make_templates_service(self):
        try:
            from ..services.templates_service import TemplatesService

            return TemplatesService()
        except Exception:
            try:
                from ..services.templates import TemplatesService  # type: ignore

                return TemplatesService()
            except Exception as exc:  # pragma: no cover
                logger.debug("TemplatesService unavailable: %s", exc)
                return None

    def open_project(self, path: str) -> None:
        if not path or self.project_service is None:
            return
        try:
            project = self.project_service.open_project(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open Project", f"Could not open project:\n{exc}")
            self.set_status(f"Open project failed: {exc}", kind="error")
            return
        self.current_project = project
        self.git_service = self._make_git_service(path)
        if self.agent_engine is not None:
            try:
                self.agent_engine.set_project(path)
            except Exception as exc:  # pragma: no cover
                logger.debug("engine.set_project failed: %s", exc)
        if self.command_runner is not None:
            try:
                self.command_runner.set_cwd(path)
            except Exception:
                pass
        self.setWindowTitle(f"Animica Studio Qt — {getattr(project, 'name', path)}")
        self._refresh_file_tree()
        self._reload_projects()
        self._reload_sessions()
        self.projectChanged.emit(path)
        self.set_status(f"Opened {getattr(project, 'name', path)}", kind="ok")

    def _on_project_opened(self, root_path: str) -> None:
        self.bottom_panel.log(f"Project opened: {root_path}")

    def _on_project_item_activated(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.open_project(str(path))

    def _refresh_file_tree(self) -> None:
        ftp = getattr(self, "file_tree_panel", None)
        refresh = getattr(ftp, "refresh", None)
        if callable(refresh):
            try:
                refresh()
            except Exception as exc:  # pragma: no cover
                logger.debug("file tree refresh failed: %s", exc)

    def _reload_projects(self) -> None:
        if self.db is None:
            return
        self.projects_list.clear()
        try:
            projects = self.db.list_projects()
        except Exception as exc:  # pragma: no cover
            logger.debug("list_projects failed: %s", exc)
            return
        for proj in projects:
            item = QListWidgetItem(getattr(proj, "name", "") or getattr(proj, "root_path", ""))
            item.setData(Qt.ItemDataRole.UserRole, getattr(proj, "root_path", ""))
            self.projects_list.addItem(item)

    # ------------------------------------------------------------------ #
    # Editor / files
    # ------------------------------------------------------------------ #
    def open_file_in_editor(self, rel_path: str) -> None:
        ep = getattr(self, "editor_panel", None)
        opener = getattr(ep, "open_file", None)
        if callable(opener):
            try:
                opener(rel_path)
                self.show_work_view()
            except Exception as exc:
                self.set_status(f"Open failed: {exc}", kind="error")
                self.bottom_panel.add_problem(f"{rel_path}: {exc}")
        else:
            self.bottom_panel.log(f"Activate file: {rel_path}")

    def on_save(self) -> None:
        ep = getattr(self, "editor_panel", None)
        # Prefer an explicit save method if the editor exposes one.
        for meth in ("save_current", "save", "save_active"):
            fn = getattr(ep, meth, None)
            if callable(fn):
                try:
                    fn()
                    self.set_status("Saved", kind="ok")
                    return
                except Exception as exc:
                    self.set_status(f"Save failed: {exc}", kind="error")
                    return
        self.set_status("Nothing to save", kind="ok")

    def _on_editor_save_requested(self, path: str) -> None:
        self.bottom_panel.log(f"Saved {path}")
        self.set_status(f"Saved {path}", kind="ok")

    # ------------------------------------------------------------------ #
    # Run / build
    # ------------------------------------------------------------------ #
    def _run_in_terminal(self, cmd: str) -> None:
        runner = self.command_runner
        if runner is None:
            self.bottom_panel.log("No command runner available.")
            return
        # Approval gate for risky commands.
        try:
            is_risky = runner.is_risky(cmd)
        except Exception:
            is_risky = False
        category = "general"
        if is_risky:
            try:
                category = runner.classify(cmd) or "general"
            except Exception:
                category = "general"
            if not self.request_approval(cmd, category):
                self.bottom_panel.log(f"Denied: {cmd}")
                self.set_status("Command denied", kind="error")
                return
        # Show the terminal tab and run.
        self.bottom_dock.show()
        self.bottom_panel.setCurrentIndex(0)
        tp = self.terminal_panel
        runner_fn = getattr(tp, "run", None)
        if callable(runner_fn):
            try:
                runner_fn(cmd)
            except Exception as exc:
                self.set_status(f"Run failed: {exc}", kind="error")
        else:
            try:
                runner.run(cmd)
            except Exception as exc:
                self.set_status(f"Run failed: {exc}", kind="error")
        self.set_status(f"Running: {cmd}", kind="busy")

    def on_run(self) -> None:
        cmd = self._prompt_command("Run command", self._default_run_command())
        if cmd:
            self._run_in_terminal(cmd)

    def on_build(self) -> None:
        cmd = self._prompt_command("Build command", self._default_build_command())
        if cmd:
            self.agent_panel.set_build_status(f"Building: {cmd}", ok=False)
            self._run_in_terminal(cmd)

    def _prompt_command(self, title: str, default: str) -> Optional[str]:
        text, ok = QInputDialog.getText(self, title, "Command:", text=default)
        if ok and text.strip():
            return text.strip()
        return None

    def _project_type(self) -> str:
        ps = self.project_service
        if ps is not None and getattr(ps, "is_open", lambda: False)():
            try:
                return ps.detect_project_type()
            except Exception:
                return "unknown"
        return "unknown"

    def _default_run_command(self) -> str:
        return {
            "python": "python main.py",
            "node": "npm start",
            "rust": "cargo run",
            "go": "go run .",
        }.get(self._project_type(), "")

    def _default_build_command(self) -> str:
        return {
            "python": "python -m build",
            "node": "npm run build",
            "rust": "cargo build",
            "go": "go build ./...",
        }.get(self._project_type(), "")

    # ------------------------------------------------------------------ #
    # Chat / agent
    # ------------------------------------------------------------------ #
    def on_new_chat(self) -> None:
        if self.db is None:
            return
        try:
            project_id = getattr(self.current_project, "id", None)
            session = self.db.create_session(
                Session(
                    title="New Chat",
                    project_id=project_id,
                    model=self.settings.model,
                    autonomy_level=self.settings.autonomy_level,
                )
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("create_session failed: %s", exc)
            return
        self.current_session = session
        if self.agent_engine is not None:
            try:
                self.agent_engine.set_session(session.id)
            except Exception:
                pass
        self._reload_sessions()
        self.set_status("New chat", kind="ok")

    def _ensure_session(self) -> None:
        if self.current_session is not None or self.db is None:
            return
        try:
            sessions = self.db.list_sessions(limit=1)
            if sessions:
                self.current_session = sessions[0]
            else:
                self.current_session = self.db.create_session(
                    Session(model=self.settings.model)
                )
        except Exception as exc:  # pragma: no cover
            logger.debug("ensure_session failed: %s", exc)
            return
        if self.agent_engine is not None and self.current_session is not None:
            try:
                self.agent_engine.set_session(self.current_session.id)
            except Exception:
                pass

    def _reload_sessions(self) -> None:
        if self.db is None:
            return
        self.sessions_list.clear()
        try:
            project_id = getattr(self.current_project, "id", None)
            sessions = self.db.list_sessions(project_id=project_id)
        except Exception as exc:  # pragma: no cover
            logger.debug("list_sessions failed: %s", exc)
            return
        for sess in sessions:
            item = QListWidgetItem(getattr(sess, "title", "Chat") or "Chat")
            item.setData(Qt.ItemDataRole.UserRole, getattr(sess, "id", ""))
            self.sessions_list.addItem(item)

    def _on_session_item_activated(self, item: QListWidgetItem) -> None:
        session_id = item.data(Qt.ItemDataRole.UserRole)
        if not session_id or self.db is None:
            return
        try:
            session = self.db.get_session(str(session_id))
        except Exception:
            session = None
        if session is not None:
            self.current_session = session
            if self.agent_engine is not None:
                try:
                    self.agent_engine.set_session(session.id)
                except Exception:
                    pass
            loader = getattr(self.chat_panel, "load_session", None)
            if callable(loader):
                try:
                    loader(session.id)
                except Exception:
                    pass

    def on_ask_animica(self) -> None:
        self.chat_panel.setVisible(True)
        self.show_work_view()
        focus = getattr(self.chat_panel, "focus_input", None)
        if callable(focus):
            try:
                focus()
            except Exception:
                pass
        else:
            self.chat_panel.setFocus()

    # ------------------------------------------------------------------ #
    # Agent engine slots
    # ------------------------------------------------------------------ #
    def _on_tool_call_started(self, payload: dict) -> None:
        self.agent_panel.add_tool_call(payload)
        name = payload.get("name", "tool") if isinstance(payload, dict) else "tool"
        self.set_status(f"Tool: {name}", kind="busy")

    def _on_tool_call_finished(self, payload: dict) -> None:
        self.agent_panel.finish_tool_call(payload)

    def _on_plan_ready(self, steps: list) -> None:
        self.agent_panel.set_plan(steps)
        self.bottom_panel.log(f"Plan ready: {len(steps)} steps")

    def _on_engine_status(self, text: str) -> None:
        self.set_status(text, kind="busy")

    def _on_engine_error(self, text: str) -> None:
        self.set_status(text, kind="error")
        self.bottom_panel.add_problem(text)

    def _on_engine_busy(self, busy: bool) -> None:
        if busy:
            self.set_status("Agent working…", kind="busy")
        else:
            self.set_status("Ready", kind="ok")
            self.agent_panel.set_build_status("Idle", ok=True)

    def _on_diff_proposed(self, diffs: list) -> None:
        """Show proposed file diffs in the diff viewer."""
        file_diffs = self._coerce_diffs(diffs)
        setter = getattr(self.diff_viewer, "set_diffs", None)
        if callable(setter):
            try:
                setter(file_diffs)
                self._connect_diff_signals()
                self.show_diff_view()
                self.set_status(f"{len(file_diffs)} change(s) proposed", kind="busy")
                return
            except Exception as exc:  # pragma: no cover
                logger.warning("diff viewer set_diffs failed: %s", exc)
        self.bottom_panel.log(f"{len(file_diffs)} diff(s) proposed (no viewer).")

    def _connect_diff_signals(self) -> None:
        dv = self.diff_viewer
        for name, slot in (("accepted", self._on_diff_accepted), ("rejected", self._on_diff_rejected)):
            sig = getattr(dv, name, None)
            if sig is not None:
                try:
                    sig.connect(slot, Qt.ConnectionType.UniqueConnection)
                except Exception:
                    pass

    def _on_diff_accepted(self, paths: list) -> None:
        self.bottom_panel.log(f"Accepted: {paths}")
        self.show_work_view()
        self._refresh_file_tree()
        self.set_status("Changes applied", kind="ok")

    def _on_diff_rejected(self, paths: list) -> None:
        self.bottom_panel.log(f"Rejected: {paths}")
        self.show_work_view()
        self.set_status("Changes rejected", kind="ok")

    @staticmethod
    def _coerce_diffs(diffs: list) -> list[FileDiff]:
        out: list[FileDiff] = []
        for d in diffs or []:
            if isinstance(d, FileDiff):
                out.append(d)
            elif isinstance(d, dict):
                out.append(
                    FileDiff(
                        path=str(d.get("path", "")),
                        old_text=str(d.get("old_text", "")),
                        new_text=str(d.get("new_text", "")),
                        is_new=bool(d.get("is_new", False)),
                        is_delete=bool(d.get("is_delete", False)),
                        is_rename=bool(d.get("is_rename", False)),
                        new_path=d.get("new_path"),
                    )
                )
        return out

    # ------------------------------------------------------------------ #
    # Approval flow
    # ------------------------------------------------------------------ #
    def _on_approval_requested(self, action: str, callback: Any) -> None:
        """Engine asked for approval; show the dialog and report the result."""
        category = self._classify_action(action)
        allowed = self.request_approval(action, category)
        if callable(callback):
            try:
                callback(bool(allowed))
            except Exception as exc:  # pragma: no cover
                logger.warning("approval callback failed: %s", exc)

    def _classify_action(self, action: str) -> str:
        runner = self.command_runner
        if runner is not None:
            try:
                return runner.classify(action) or "general"
            except Exception:
                return "general"
        return "general"

    def request_approval(self, action: str, category: str = "general") -> bool:
        """Show the approval dialog (honoring persisted *always allow*).

        Returns ``True`` if the action is approved. *Always allow* persists a
        :class:`~animica_studio.models.CommandApproval` for the project/category.
        """
        project_id = getattr(self.current_project, "id", None)

        # Respect a previously recorded "always allow" for this category/project.
        if self.db is not None:
            try:
                if self.db.find_always_allow(category, project_id=project_id):
                    self.bottom_panel.log(f"Auto-approved ({category}): {action}")
                    return True
            except Exception as exc:  # pragma: no cover
                logger.debug("find_always_allow failed: %s", exc)

        dialog = ApprovalDialog(action, category, parent=self)
        dialog.exec()
        decision = dialog.decision()

        if decision is ApprovalDecision.DENY:
            self._persist_approval(action, category, decision, always=False)
            return False

        always = decision is ApprovalDecision.ALWAYS_ALLOW
        self._persist_approval(action, category, decision, always=always)
        return True

    def _persist_approval(
        self,
        action: str,
        category: str,
        decision: ApprovalDecision,
        *,
        always: bool,
    ) -> None:
        if self.db is None:
            return
        try:
            self.db.add_command_approval(
                CommandApproval(
                    project_id=getattr(self.current_project, "id", None),
                    command=action,
                    category=category,
                    decision=decision,
                    always=always,
                )
            )
        except Exception as exc:  # pragma: no cover
            logger.debug("add_command_approval failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Settings / model / autonomy
    # ------------------------------------------------------------------ #
    def on_settings(self) -> None:
        SettingsDialog = _try_import("settings_dialog", "SettingsDialog")
        if SettingsDialog is None:
            QMessageBox.information(self, "Settings", "Settings dialog unavailable.")
            return
        try:
            dialog = SettingsDialog(self.settings, self.db)
        except Exception as exc:  # pragma: no cover
            QMessageBox.warning(self, "Settings", f"Could not open settings:\n{exc}")
            return
        if dialog.exec():
            getter = getattr(dialog, "result_settings", None)
            if callable(getter):
                try:
                    self.settings = getter()
                except Exception as exc:  # pragma: no cover
                    logger.warning("result_settings failed: %s", exc)
            self._apply_settings()

    def _apply_settings(self) -> None:
        # Push new settings into the engine and refresh toolbar widgets.
        if self.agent_engine is not None:
            setter = getattr(self.agent_engine, "set_settings", None)
            if callable(setter):
                try:
                    setter(self.settings)
                except Exception as exc:  # pragma: no cover
                    logger.debug("engine.set_settings failed: %s", exc)
        if self.settings.model:
            self.model_combo.blockSignals(True)
            self.model_combo.setCurrentText(self.settings.model)
            self.model_combo.blockSignals(False)
        idx = self.autonomy_combo.findData(self.settings.autonomy_level.value)
        if idx >= 0:
            self.autonomy_combo.blockSignals(True)
            self.autonomy_combo.setCurrentIndex(idx)
            self.autonomy_combo.blockSignals(False)
        self.set_status("Settings saved", kind="ok")

    def on_model_changed(self, model: str) -> None:
        model = (model or "").strip()
        if not model:
            return
        self.settings.model = model
        try:
            self.settings.save()
        except Exception as exc:  # pragma: no cover
            logger.debug("settings.save failed: %s", exc)
        self._apply_settings_to_engine()
        self.set_status(f"Model: {model}", kind="ok")

    def on_autonomy_changed(self, _index: int) -> None:
        value = self.autonomy_combo.currentData()
        level = AutonomyLevel.from_value(value)
        self.settings.autonomy_level = level
        try:
            self.settings.save()
        except Exception as exc:  # pragma: no cover
            logger.debug("settings.save failed: %s", exc)
        if self.agent_engine is not None:
            setter = getattr(self.agent_engine, "set_autonomy", None)
            if callable(setter):
                try:
                    setter(level)
                except Exception:
                    pass
        self.set_status(f"Autonomy: {level.label}", kind="ok")

    def _apply_settings_to_engine(self) -> None:
        if self.agent_engine is None:
            return
        setter = getattr(self.agent_engine, "set_settings", None)
        if callable(setter):
            try:
                setter(self.settings)
            except Exception as exc:  # pragma: no cover
                logger.debug("engine.set_settings failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Misc
    # ------------------------------------------------------------------ #
    def _open_link(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except Exception as exc:  # pragma: no cover
            logger.debug("open link failed: %s", exc)
        self.bottom_panel.log(f"Opened {url}")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # Stop a running agent and persist settings before closing.
        if self.agent_engine is not None:
            stop = getattr(self.agent_engine, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
        try:
            self.settings.save()
        except Exception:
            pass
        super().closeEvent(event)


__all__ = ["MainWindow", "ApprovalDialog", "AgentPanel", "BottomPanel"]
