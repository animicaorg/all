"""Editor widgets for the IDE."""

from __future__ import annotations

import logging
from dataclasses import dataclass
import importlib.util
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRegularExpression, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QPainter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QSyntaxHighlighter,
)
from PySide6.QtWidgets import QPlainTextEdit, QWidget
from PySide6.QtWidgets import QVBoxLayout

logger = logging.getLogger(__name__)

if importlib.util.find_spec("PySide6.Qsci") is not None:
    from PySide6.Qsci import QsciLexerPython, QsciScintilla

    QSCINTILLA_AVAILABLE = True
else:  # pragma: no cover - optional dependency
    QsciLexerPython = None
    QsciScintilla = None
    QSCINTILLA_AVAILABLE = False


@dataclass
class SearchOptions:
    """Search options for find/replace operations."""

    pattern: str
    regex: bool = False
    case_sensitive: bool = False


class PythonHighlighter(QSyntaxHighlighter):
    """Simple Python syntax highlighter for QTextDocument."""

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        self.rules: list[tuple[QRegularExpression, QTextCharFormat]] = []

        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#c792ea"))
        keyword_format.setFontWeight(QFont.Bold)
        keywords = [
            "and",
            "as",
            "assert",
            "break",
            "class",
            "continue",
            "def",
            "del",
            "elif",
            "else",
            "except",
            "False",
            "finally",
            "for",
            "from",
            "global",
            "if",
            "import",
            "in",
            "is",
            "lambda",
            "None",
            "nonlocal",
            "not",
            "or",
            "pass",
            "raise",
            "return",
            "True",
            "try",
            "while",
            "with",
            "yield",
        ]
        for keyword in keywords:
            pattern = QRegularExpression(fr"\b{keyword}\b")
            self.rules.append((pattern, keyword_format))

        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#ecc48d"))
        self.rules.append((QRegularExpression(r"'[^'\\]*(\\.[^'\\]*)*'"), string_format))
        self.rules.append((QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"'), string_format))

        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("#7f848e"))
        self.rules.append((QRegularExpression(r"#.*"), comment_format))

        number_format = QTextCharFormat()
        number_format.setForeground(QColor("#f78c6c"))
        self.rules.append((QRegularExpression(r"\b[0-9]+(\.[0-9]+)?\b"), number_format))

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self.rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)


class LineNumberArea(QWidget):
    """Widget for displaying line numbers."""

    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self.code_editor = editor

    def sizeHint(self) -> QSize:  # noqa: D401 - Qt override
        return QSize(self.code_editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: D401 - Qt override
        self.code_editor.line_number_area_paint_event(event)


class CodeEditor(QPlainTextEdit):
    """QPlainTextEdit with line numbers, bracket matching, and indent guides."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.line_number_area = LineNumberArea(self)
        self.document().blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self.update_extra_selections)

        fixed_font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        fixed_font.setPointSize(11)
        self.setFont(fixed_font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)

        self.update_line_number_area_width(0)
        self.update_extra_selections()

        self.highlighter = PythonHighlighter(self.document())

    def line_number_area_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        space = 12 + self.fontMetrics().horizontalAdvance("9") * digits
        return space

    def update_line_number_area_width(self, _block_count: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def resizeEvent(self, event) -> None:  # noqa: D401 - Qt override
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height())
        )

    def line_number_area_paint_event(self, event) -> None:
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#2b2b2b"))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(QColor("#7f848e"))
                painter.drawText(
                    0,
                    top,
                    self.line_number_area.width() - 4,
                    self.fontMetrics().height(),
                    Qt.AlignRight,
                    number,
                )
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1

    def paintEvent(self, event) -> None:  # noqa: D401 - Qt override
        super().paintEvent(event)
        self._paint_indent_guides()

    def _paint_indent_guides(self) -> None:
        painter = QPainter(self.viewport())
        painter.setPen(QColor("#3d3d3d"))
        block = self.firstVisibleBlock()
        offset = self.contentOffset()
        fm = self.fontMetrics()
        tab_width = self.tabStopDistance()

        while block.isValid():
            block_top = self.blockBoundingGeometry(block).translated(offset).top()
            if block_top > self.viewport().height():
                break
            text = block.text()
            indent_level = len(text) - len(text.lstrip(" "))
            for i in range(0, indent_level, 4):
                x = int(i / 4 * tab_width + fm.horizontalAdvance(" ") * 0.5)
                painter.drawLine(x, int(block_top), x, int(block_top + fm.height()))
            block = block.next()

    def update_extra_selections(self) -> None:
        selections = []
        if not self.isReadOnly():
            selection = QPlainTextEdit.ExtraSelection()
            selection.format.setBackground(QColor("#333333"))
            selection.format.setProperty(QTextCharFormat.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            selections.append(selection)

        bracket_selection = self._matching_bracket_selection()
        if bracket_selection:
            selections.extend(bracket_selection)
        self.setExtraSelections(selections)

    def _matching_bracket_selection(self) -> list[QPlainTextEdit.ExtraSelection]:
        cursor = self.textCursor()
        pos = cursor.position()
        document = self.document()
        if pos == 0:
            return []

        char = document.characterAt(pos - 1)
        pairs = {"(": ")", "[": "]", "{": "}", ")": "(", "]": "[", "}": "{"}
        if char not in pairs:
            return []

        direction = 1 if char in "([{" else -1
        match_char = pairs[char]
        depth = 0
        index = pos - 1

        while 0 <= index < document.characterCount():
            current = document.characterAt(index)
            if current == char:
                depth += 1
            elif current == match_char:
                depth -= 1
                if depth == 0:
                    return self._highlight_positions(pos - 1, index)
            index += direction
        return []

    def _highlight_positions(self, first: int, second: int) -> list[QPlainTextEdit.ExtraSelection]:
        selections = []
        for pos in (first, second):
            cursor = self.textCursor()
            cursor.setPosition(pos)
            cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, 1)
            selection = QPlainTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format.setBackground(QColor("#4a4a4a"))
            selections.append(selection)
        return selections


class EditorWidget(QWidget):
    """Unified editor widget wrapper for QScintilla or QTextEdit fallback."""

    modificationChanged = Signal(bool)
    cursorPositionChanged = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        if QSCINTILLA_AVAILABLE:
            self.editor = QsciScintilla(self)
            self._setup_qscintilla()
        else:
            self.editor = CodeEditor(self)
        self._file_path: Optional[Path] = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.editor)

        if QSCINTILLA_AVAILABLE:
            self.editor.modificationChanged.connect(self.modificationChanged)
            self.editor.cursorPositionChanged.connect(lambda *_: self.cursorPositionChanged.emit())
        else:
            self.editor.document().modificationChanged.connect(self.modificationChanged)
            self.editor.cursorPositionChanged.connect(self.cursorPositionChanged)

    def _setup_qscintilla(self) -> None:
        assert QsciScintilla is not None
        self.editor.setUtf8(True)
        self.editor.setMarginsForegroundColor(QColor("#7f848e"))
        self.editor.setMarginsBackgroundColor(QColor("#2b2b2b"))
        self.editor.setMarginType(0, QsciScintilla.NumberMargin)
        self.editor.setMarginWidth(0, "00000")
        self.editor.setBraceMatching(QsciScintilla.SloppyBraceMatch)
        self.editor.setIndentationGuides(True)
        self.editor.setAutoIndent(True)
        self.editor.setIndentationsUseTabs(False)
        self.editor.setTabWidth(4)
        self.editor.setCaretLineVisible(True)
        self.editor.setCaretLineBackgroundColor(QColor("#333333"))
        fixed_font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        fixed_font.setPointSize(11)
        self.editor.setFont(fixed_font)
        lexer = QsciLexerPython()
        lexer.setDefaultFont(fixed_font)
        self.editor.setLexer(lexer)

    def widget(self) -> QWidget:
        return self.editor

    def set_text(self, text: str) -> None:
        if QSCINTILLA_AVAILABLE:
            self.editor.setText(text)
        else:
            self.editor.setPlainText(text)

    def text(self) -> str:
        if QSCINTILLA_AVAILABLE:
            return self.editor.text()
        return self.editor.toPlainText()

    def set_file_path(self, path: Optional[Path]) -> None:
        self._file_path = path

    def file_path(self) -> Optional[Path]:
        return self._file_path

    def is_modified(self) -> bool:
        if QSCINTILLA_AVAILABLE:
            return self.editor.isModified()
        return self.editor.document().isModified()

    def set_modified(self, value: bool) -> None:
        if QSCINTILLA_AVAILABLE:
            self.editor.setModified(value)
        else:
            self.editor.document().setModified(value)

    def load_file(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        self.set_text(text)
        self.set_file_path(path)
        self.set_modified(False)

    def save_file(self, path: Optional[Path] = None) -> None:
        target = path or self._file_path
        if not target:
            raise ValueError("No file path set for editor")
        target.write_text(self.text(), encoding="utf-8")
        self.set_file_path(target)
        self.set_modified(False)

    def find_next(self, options: SearchOptions, forward: bool = True) -> bool:
        if QSCINTILLA_AVAILABLE:
            return self._qsci_find_next(options, forward)
        return self._text_find_next(options, forward)

    def _qsci_find_next(self, options: SearchOptions, forward: bool) -> bool:
        return self.editor.findFirst(
            options.pattern,
            options.regex,
            options.case_sensitive,
            False,
            True,
            forward,
            -1,
            -1,
            False,
            False,
        )

    def _text_find_next(self, options: SearchOptions, forward: bool) -> bool:
        doc = self.editor.document()
        cursor = self.editor.textCursor()
        flags = QTextDocument.FindFlags()
        if options.case_sensitive:
            flags |= QTextDocument.FindCaseSensitively
        if not forward:
            flags |= QTextDocument.FindBackward
        expression: QRegularExpression | str
        if options.regex:
            expression = QRegularExpression(options.pattern)
        else:
            expression = options.pattern
        found = doc.find(expression, cursor, flags)
        if found.isNull():
            return False
        self.editor.setTextCursor(found)
        return True

    def replace_current(self, replacement: str) -> None:
        if QSCINTILLA_AVAILABLE:
            self.editor.replaceSelectedText(replacement)
        else:
            cursor = self.editor.textCursor()
            if cursor.hasSelection():
                cursor.insertText(replacement)

    def replace_all(self, options: SearchOptions, replacement: str) -> int:
        count = 0
        if QSCINTILLA_AVAILABLE:
            self.editor.setCursorPosition(0, 0)
        else:
            self.editor.moveCursor(QTextCursor.Start)
        while self.find_next(options, forward=True):
            self.replace_current(replacement)
            count += 1
        return count

    def go_to_line(self, line_number: int) -> None:
        if line_number < 1:
            return
        if QSCINTILLA_AVAILABLE:
            self.editor.setCursorPosition(line_number - 1, 0)
            self.editor.ensureLineVisible(line_number - 1)
        else:
            block = self.editor.document().findBlockByNumber(line_number - 1)
            if block.isValid():
                cursor = QTextCursor(block)
                self.editor.setTextCursor(cursor)
                self.editor.centerCursor()

    def go_to_line_column(self, line_number: int, column: int) -> None:
        if line_number < 1:
            return
        column = max(0, column - 1)
        if QSCINTILLA_AVAILABLE:
            self.editor.setCursorPosition(line_number - 1, column)
            self.editor.ensureLineVisible(line_number - 1)
        else:
            block = self.editor.document().findBlockByNumber(line_number - 1)
            if block.isValid():
                cursor = QTextCursor(block)
                cursor.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, column)
                self.editor.setTextCursor(cursor)
                self.editor.centerCursor()
