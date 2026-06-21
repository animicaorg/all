"""Tabbed code editor for Animica Studio Qt.

:class:`EditorPanel` is a :class:`QTabWidget` of open files. Each tab hosts a
:class:`CodeEditor` (a :class:`QPlainTextEdit` subclass) with a line-number
gutter, a Pygments-driven syntax highlighter, a dirty indicator, save / save-all,
find-in-file, and a right-click context menu exposing agent actions ("Ask
Animica about this file", "Fix this code", "Explain this code", "Generate
tests"). The panel forwards those actions as Qt signals the main window routes
to the agent.

All file access goes through :class:`~animica_studio.services.project_service.ProjectService`
so every read/write is sandboxed to the project root.

The module imports and constructs headless (``QT_QPA_PLATFORM=offscreen``).
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QShortcut,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# ---- Pygments (optional but expected to be present) ----------------------
try:  # pragma: no cover - import guard
    from pygments import lex as _pyg_lex
    from pygments.lexers import get_lexer_by_name, guess_lexer_for_filename
    from pygments.token import Token as _PygToken
    from pygments.util import ClassNotFound

    _PYGMENTS_AVAILABLE = True
except Exception:  # pragma: no cover - pygments missing
    _PYGMENTS_AVAILABLE = False
    _PygToken = None  # type: ignore[assignment]
    ClassNotFound = Exception  # type: ignore[assignment,misc]


# Animica-branded dark token palette (hex -> applied to Pygments token types).
_TOKEN_COLORS: dict[str, str] = {
    "Keyword": "#3a9bff",
    "Keyword.Constant": "#3a9bff",
    "Keyword.Namespace": "#3a9bff",
    "Name.Builtin": "#16d9c3",
    "Name.Function": "#39ff14",
    "Name.Class": "#39ff14",
    "Name.Decorator": "#16d9c3",
    "Name.Namespace": "#cfe0f2",
    "Name.Tag": "#3a9bff",
    "Name.Attribute": "#16d9c3",
    "String": "#8df7b8",
    "String.Doc": "#6fae8c",
    "String.Escape": "#39ff14",
    "Number": "#ffd479",
    "Comment": "#5f7796",
    "Comment.Preproc": "#7f93b0",
    "Operator": "#7fb3ff",
    "Punctuation": "#a8bcd6",
    "Error": "#ff7a9c",
    "Generic.Deleted": "#ff9bb3",
    "Generic.Inserted": "#8df7b8",
}


def _resolve_token_format(token) -> Optional[QTextCharFormat]:
    """Return a cached char format for a Pygments token type, or None."""
    # Walk from the most specific token type up to the root to find a color.
    parts: list[str] = []
    t = token
    chain: list[str] = []
    try:
        # pygment tokens are tuples; str(token) yields e.g. "Token.Keyword".
        as_str = str(token)
    except Exception:  # pragma: no cover
        return None
    if as_str.startswith("Token."):
        chain = as_str[len("Token.") :].split(".")
    for i in range(len(chain), 0, -1):
        key = ".".join(chain[:i])
        if key in _TOKEN_COLORS:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(_TOKEN_COLORS[key]))
            return fmt
    parts = parts  # noqa: F841 - keep mypy calm about unused
    return None


class PygmentsHighlighter(QSyntaxHighlighter):
    """A :class:`QSyntaxHighlighter` backed by a Pygments lexer.

    Highlighting is whole-document (block-by-block re-tokenization is avoided to
    keep multi-line constructs such as docstrings correct). Pygments lexing is
    fast for typical source files; very large files fall back to no highlighting.
    """

    MAX_HIGHLIGHT_BYTES = 400_000

    def __init__(self, document: QTextDocument, filename: str = "") -> None:
        super().__init__(document)
        self._filename = filename
        self._lexer = None
        self._format_cache: dict[str, Optional[QTextCharFormat]] = {}
        self._enabled = _PYGMENTS_AVAILABLE
        self._block_formats: dict[int, list[tuple[int, int, QTextCharFormat]]] = {}
        self._dirty = True
        if _PYGMENTS_AVAILABLE:
            self._init_lexer()

    def set_filename(self, filename: str) -> None:
        self._filename = filename
        if _PYGMENTS_AVAILABLE:
            self._init_lexer()
        self._dirty = True
        self.rehighlight()

    def _init_lexer(self) -> None:
        text = ""
        try:
            text = self.document().toPlainText()
        except Exception:  # pragma: no cover
            text = ""
        try:
            if self._filename:
                self._lexer = guess_lexer_for_filename(self._filename, text[:4000])
            else:  # pragma: no cover - exercised via set_filename
                self._lexer = get_lexer_by_name("text")
        except ClassNotFound:
            try:
                self._lexer = get_lexer_by_name("text")
            except Exception:  # pragma: no cover
                self._lexer = None
        except Exception:  # pragma: no cover
            self._lexer = None

    def _format_for(self, token) -> Optional[QTextCharFormat]:
        key = str(token)
        if key not in self._format_cache:
            self._format_cache[key] = _resolve_token_format(token)
        return self._format_cache[key]

    def _retokenize(self) -> None:
        """Tokenize the whole document and bucket formats per block index."""
        self._block_formats = {}
        if not self._enabled or self._lexer is None:
            self._dirty = False
            return
        doc = self.document()
        text = doc.toPlainText()
        if len(text.encode("utf-8", errors="ignore")) > self.MAX_HIGHLIGHT_BYTES:
            self._dirty = False
            return
        block_index = 0
        col = 0
        try:
            tokens = list(_pyg_lex(text, self._lexer))
        except Exception as exc:  # pragma: no cover - lexer robustness
            logger.debug("Lexing failed for %s: %s", self._filename, exc)
            self._dirty = False
            return
        for token_type, value in tokens:
            fmt = self._format_for(token_type)
            for piece_idx, piece in enumerate(value.split("\n")):
                if piece_idx > 0:
                    block_index += 1
                    col = 0
                length = len(piece)
                if length and fmt is not None:
                    self._block_formats.setdefault(block_index, []).append(
                        (col, length, fmt)
                    )
                col += length
        self._dirty = False

    def rehighlight(self) -> None:  # type: ignore[override]
        self._dirty = True
        super().rehighlight()

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt override)
        if not self._enabled or self._lexer is None:
            return
        if self._dirty:
            self._retokenize()
        block_no = self.currentBlock().blockNumber()
        for start, length, fmt in self._block_formats.get(block_no, ()):  # type: ignore[union-attr]
            self.setFormat(start, length, fmt)


class _LineNumberArea(QWidget):
    """Gutter widget that paints line numbers for a :class:`CodeEditor`."""

    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        self._editor.line_number_area_paint(event)


class CodeEditor(QPlainTextEdit):
    """A code editor with a line-number gutter and Pygments highlighting.

    Emits :data:`agentActionRequested` (action, rel_path, selected_or_full_text)
    when the user picks an agent action from the context menu.
    """

    agentActionRequested = Signal(str, str, str)  # (action, rel_path, text)

    GUTTER_PADDING = 16

    def __init__(self, rel_path: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rel_path = rel_path
        self.setObjectName("Editor")
        self.setProperty("class", "code")
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))

        mono = QFont("JetBrains Mono")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(11)
        self.setFont(mono)

        self._line_area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_area_width)
        self.updateRequest.connect(self._update_line_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_line_area_width(0)
        self._highlight_current_line()

        self._highlighter = PygmentsHighlighter(self.document(), rel_path)

    # -- identity ----------------------------------------------------------
    @property
    def rel_path(self) -> str:
        return self._rel_path

    def set_rel_path(self, rel_path: str) -> None:
        self._rel_path = rel_path
        self._highlighter.set_filename(rel_path)

    # -- line number gutter ------------------------------------------------
    def line_number_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return self.GUTTER_PADDING + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_line_area_width(self, _block_count: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self._line_area.scroll(0, dy)
        else:
            self._line_area.update(
                0, rect.y(), self._line_area.width(), rect.height()
            )
        if rect.contains(self.viewport().rect()):
            self._update_line_area_width(0)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_area.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height())
        )

    def line_number_area_paint(self, event: QPaintEvent) -> None:
        painter = QPainter(self._line_area)
        painter.fillRect(event.rect(), QColor("#060a16"))
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(
            self.blockBoundingGeometry(block)
            .translated(self.contentOffset())
            .top()
        )
        bottom = top + int(self.blockBoundingRect(block).height())
        cur_line = self.textCursor().blockNumber()
        width = self._line_area.width() - 6
        height = self.fontMetrics().height()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(
                    QColor("#16d9c3") if block_number == cur_line else QColor("#3d4d66")
                )
                painter.drawText(
                    0, top, width, height, Qt.AlignmentFlag.AlignRight, number
                )
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1

    def _highlight_current_line(self) -> None:
        selections: list[QTextEdit.ExtraSelection] = []
        if not self.isReadOnly():
            selection = QTextEdit.ExtraSelection()
            line_color = QColor("#0a1322")
            selection.format.setBackground(line_color)
            selection.format.setProperty(
                QTextCharFormat.Property.FullWidthSelection, True
            )
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            selections.append(selection)
        self.setExtraSelections(selections)

    # -- context menu ------------------------------------------------------
    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu: QMenu = self.createStandardContextMenu()
        menu.addSeparator()
        actions = (
            ("Ask Animica about this file", "ask"),
            ("Fix this code", "fix"),
            ("Explain this code", "explain"),
            ("Generate tests", "tests"),
        )
        for label, key in actions:
            act = menu.addAction(label)
            act.triggered.connect(lambda _checked=False, k=key: self._emit_action(k))
        menu.exec(event.globalPos())

    def _emit_action(self, action: str) -> None:
        cursor = self.textCursor()
        selected = cursor.selectedText().replace(" ", "\n")
        text = selected if selected.strip() else self.toPlainText()
        self.agentActionRequested.emit(action, self._rel_path, text)


class _EditorTab(QWidget):
    """A single editor tab: a :class:`CodeEditor` plus an inline find bar."""

    findVisibilityChanged = Signal(bool)

    def __init__(self, rel_path: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.rel_path = rel_path
        self.dirty = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.editor = CodeEditor(rel_path, self)
        layout.addWidget(self.editor, 1)

        self.find_bar = self._build_find_bar()
        self.find_bar.setVisible(False)
        layout.addWidget(self.find_bar)

        find_sc = QShortcut(QKeySequence.StandardKey.Find, self.editor)
        find_sc.activated.connect(self.show_find)
        esc_sc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.find_input)
        esc_sc.activated.connect(self.hide_find)

    def _build_find_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("Panel")
        h = QHBoxLayout(bar)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(6)

        self.find_input = QLineEdit(bar)
        self.find_input.setPlaceholderText("Find in file…")
        self.find_input.returnPressed.connect(self.find_next)
        h.addWidget(self.find_input, 1)

        prev_btn = QPushButton("Prev", bar)
        prev_btn.setProperty("class", "ghost")
        prev_btn.clicked.connect(self.find_prev)
        h.addWidget(prev_btn)

        next_btn = QPushButton("Next", bar)
        next_btn.setProperty("class", "ghost")
        next_btn.clicked.connect(self.find_next)
        h.addWidget(next_btn)

        close_btn = QPushButton("✕", bar)
        close_btn.setProperty("class", "ghost")
        close_btn.setFixedWidth(28)
        close_btn.clicked.connect(self.hide_find)
        h.addWidget(close_btn)
        return bar

    def show_find(self) -> None:
        sel = self.editor.textCursor().selectedText()
        if sel:
            self.find_input.setText(sel.replace(" ", " "))
        self.find_bar.setVisible(True)
        self.find_input.setFocus()
        self.find_input.selectAll()
        self.findVisibilityChanged.emit(True)

    def hide_find(self) -> None:
        self.find_bar.setVisible(False)
        self.editor.setFocus()
        self.findVisibilityChanged.emit(False)

    def find_next(self) -> None:
        self._find(forward=True)

    def find_prev(self) -> None:
        self._find(forward=False)

    def _find(self, forward: bool) -> None:
        text = self.find_input.text()
        if not text:
            return
        flags = QTextDocument.FindFlag(0)
        if not forward:
            flags |= QTextDocument.FindFlag.FindBackward
        if not self.editor.find(text, flags):
            # Wrap around.
            cursor = self.editor.textCursor()
            cursor.movePosition(
                QTextCursor.MoveOperation.Start
                if forward
                else QTextCursor.MoveOperation.End
            )
            self.editor.setTextCursor(cursor)
            self.editor.find(text, flags)


class EditorPanel(QTabWidget):
    """Tabbed multi-file code editor bound to a :class:`ProjectService`.

    Signals
    -------
    dirtyChanged(str path, bool dirty)
        Emitted when a tab transitions between clean and modified.
    saveRequested(str path)
        Emitted just after a successful save (so the host can refresh state).
    agentActionRequested(str action, str path, str text)
        Forwarded from a tab's context menu ("ask"/"fix"/"explain"/"tests").
    """

    dirtyChanged = Signal(str, bool)
    saveRequested = Signal(str)
    agentActionRequested = Signal(str, str, str)

    def __init__(self, project_service) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self._project = project_service
        self._tabs: dict[str, _EditorTab] = {}

        self.setObjectName("EditorPanel")
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.tabCloseRequested.connect(self._on_tab_close_requested)

        # Save shortcuts apply panel-wide.
        save_sc = QShortcut(QKeySequence.StandardKey.Save, self)
        save_sc.activated.connect(self.save_current)
        save_all_sc = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        save_all_sc.activated.connect(self.save_all)

        self._build_placeholder()

    # -- placeholder for the empty state ----------------------------------
    def _build_placeholder(self) -> None:
        self._placeholder = QWidget(self)
        v = QVBoxLayout(self._placeholder)
        v.setContentsMargins(24, 24, 24, 24)
        label = QLabel("Open a file from the project tree to start editing.")
        label.setObjectName("SectionHeader")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addStretch(1)
        v.addWidget(label)
        v.addStretch(1)
        self.addTab(self._placeholder, "Welcome")

    def _remove_placeholder(self) -> None:
        idx = self.indexOf(self._placeholder)
        if idx != -1:
            self.removeTab(idx)

    # -- public API --------------------------------------------------------
    def open_file(self, rel_path: str) -> None:
        """Open ``rel_path`` (relative to the project root) in a tab."""
        if not rel_path:
            return
        if rel_path in self._tabs:
            self.setCurrentWidget(self._tabs[rel_path])
            return
        if self._project is None:
            return
        try:
            content = self._project.read_file(rel_path)
        except Exception as exc:
            logger.warning("Cannot open %s: %s", rel_path, exc)
            QMessageBox.warning(self, "Open file", f"Cannot open {rel_path}:\n{exc}")
            return

        self._remove_placeholder()
        tab = _EditorTab(rel_path, self)
        tab.editor.setPlainText(content)
        tab.editor.document().setModified(False)
        tab.editor.agentActionRequested.connect(self.agentActionRequested)
        tab.editor.document().modificationChanged.connect(
            lambda changed, p=rel_path: self._on_modified(p, changed)
        )
        self._tabs[rel_path] = tab
        index = self.addTab(tab, self._tab_title(rel_path, dirty=False))
        self.setTabToolTip(index, rel_path)
        self.setCurrentIndex(index)
        tab.editor.setFocus()

    def current_path(self) -> Optional[str]:
        widget = self.currentWidget()
        if isinstance(widget, _EditorTab):
            return widget.rel_path
        return None

    def save_current(self) -> bool:
        widget = self.currentWidget()
        if isinstance(widget, _EditorTab):
            return self._save_tab(widget)
        return False

    def save_all(self) -> None:
        for tab in list(self._tabs.values()):
            if tab.dirty:
                self._save_tab(tab)

    def is_dirty(self, rel_path: str) -> bool:
        tab = self._tabs.get(rel_path)
        return bool(tab and tab.dirty)

    def has_unsaved(self) -> bool:
        return any(tab.dirty for tab in self._tabs.values())

    def refresh_file(self, rel_path: str) -> None:
        """Reload a file's contents from disk if it is open and clean."""
        tab = self._tabs.get(rel_path)
        if tab is None or tab.dirty or self._project is None:
            return
        try:
            content = self._project.read_file(rel_path)
        except Exception:
            return
        tab.editor.setPlainText(content)
        tab.editor.document().setModified(False)

    def close_file(self, rel_path: str) -> None:
        tab = self._tabs.get(rel_path)
        if tab is not None:
            self._on_tab_close_requested(self.indexOf(tab))

    # -- internals ---------------------------------------------------------
    def _save_tab(self, tab: _EditorTab) -> bool:
        if self._project is None:
            return False
        try:
            self._project.write_file(tab.rel_path, tab.editor.toPlainText())
        except Exception as exc:
            logger.warning("Cannot save %s: %s", tab.rel_path, exc)
            QMessageBox.warning(self, "Save file", f"Cannot save {tab.rel_path}:\n{exc}")
            return False
        tab.editor.document().setModified(False)  # triggers _on_modified(False)
        self.saveRequested.emit(tab.rel_path)
        return True

    def _on_modified(self, rel_path: str, changed: bool) -> None:
        tab = self._tabs.get(rel_path)
        if tab is None:
            return
        if tab.dirty == changed:
            return
        tab.dirty = changed
        index = self.indexOf(tab)
        if index != -1:
            self.setTabText(index, self._tab_title(rel_path, dirty=changed))
        self.dirtyChanged.emit(rel_path, changed)

    def _on_tab_close_requested(self, index: int) -> None:
        widget = self.widget(index)
        if not isinstance(widget, _EditorTab):
            self.removeTab(index)
            return
        if widget.dirty:
            resp = QMessageBox.question(
                self,
                "Unsaved changes",
                f"Save changes to {widget.rel_path} before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if resp == QMessageBox.StandardButton.Cancel:
                return
            if resp == QMessageBox.StandardButton.Save:
                if not self._save_tab(widget):
                    return
        self._tabs.pop(widget.rel_path, None)
        self.removeTab(index)
        widget.deleteLater()
        if self.count() == 0:
            self._build_placeholder()

    @staticmethod
    def _tab_title(rel_path: str, *, dirty: bool) -> str:
        name = rel_path.rsplit("/", 1)[-1] or rel_path
        return f"● {name}" if dirty else name


__all__ = ["EditorPanel", "CodeEditor", "PygmentsHighlighter"]
