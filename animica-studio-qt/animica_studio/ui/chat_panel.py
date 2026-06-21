"""Chat panel for Animica Studio Qt.

A scrollable conversation view that renders user / assistant messages with
Markdown (via the ``markdown`` library into ``QTextBrowser`` rich text), fenced
code blocks with monospace styling + a per-block *Copy* button + Pygments
syntax highlighting, an inline tool-call chip stream, and a multiline input box
(Ctrl+Enter / Cmd+Enter to send) with Send / Stop controls.

It binds to the :class:`~animica_studio.agent.engine.AgentEngine` signals
described in the contracts:

    assistantChunk(str)       -- streaming token delta (appended live)
    assistantMessage(str)     -- final assistant message text
    toolCallStarted(dict)     -- ToolCall.to_dict()
    toolCallFinished(dict)    -- ToolCall.to_dict() (with result)
    approvalRequested(str, callback)
    statusChanged(str)
    errorOccurred(str)
    busyChanged(bool)         -- (optional; bound defensively)

The panel is import-safe and constructable headless (QT_QPA_PLATFORM=offscreen):
heavy work happens lazily and every optional dependency is guarded.
"""
from __future__ import annotations

import html
import logging
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QFont, QKeyEvent, QGuiApplication, QTextOption
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..config import Settings
from ..models import (
    Message,
    MessageRole,
    ToolStatus,
    extract_code_blocks,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Optional dependencies (guarded so the panel imports headless even if missing)
# --------------------------------------------------------------------------- #
try:  # markdown -> rich HTML
    import markdown as _markdown  # type: ignore

    _HAVE_MARKDOWN = True
except Exception:  # pragma: no cover - defensive
    _markdown = None  # type: ignore
    _HAVE_MARKDOWN = False

try:  # pygments -> syntax-highlighted HTML for code blocks
    from pygments import highlight as _pyg_highlight  # type: ignore
    from pygments.formatters import HtmlFormatter  # type: ignore
    from pygments.lexers import get_lexer_by_name, guess_lexer  # type: ignore
    from pygments.util import ClassNotFound  # type: ignore

    _HAVE_PYGMENTS = True
except Exception:  # pragma: no cover - defensive
    _pyg_highlight = None  # type: ignore
    _HAVE_PYGMENTS = False


# --------------------------------------------------------------------------- #
# Markdown / highlight rendering helpers
# --------------------------------------------------------------------------- #
_CODE_BG = "#04060f"
_CODE_FG = "#d6e2ff"


def _pygments_css() -> str:
    """Return a Pygments CSS block scoped to ``.highlight`` (dark friendly)."""
    if not _HAVE_PYGMENTS:
        return ""
    try:
        return HtmlFormatter(style="monokai").get_style_defs(".highlight")
    except Exception:  # pragma: no cover
        return ""


def highlight_code(code: str, language: str = "") -> str:
    """Return an HTML fragment of ``code`` syntax-highlighted via Pygments.

    Falls back to an escaped ``<pre>`` block when Pygments is unavailable or the
    language cannot be resolved.
    """
    if _HAVE_PYGMENTS:
        try:
            lexer = None
            if language:
                try:
                    lexer = get_lexer_by_name(language, stripnl=False)
                except ClassNotFound:
                    lexer = None
            if lexer is None:
                try:
                    lexer = guess_lexer(code) if code.strip() else None
                except Exception:
                    lexer = None
            if lexer is not None:
                formatter = HtmlFormatter(nowrap=False, noclasses=False)
                return _pyg_highlight(code, lexer, formatter)
        except Exception:  # pragma: no cover - never let rendering crash the UI
            logger.debug("pygments highlight failed", exc_info=True)
    escaped = html.escape(code)
    return (
        f'<pre style="margin:0;color:{_CODE_FG};background:{_CODE_BG};'
        f'white-space:pre-wrap;">{escaped}</pre>'
    )


def render_markdown(text: str) -> str:
    """Render Markdown ``text`` (sans fenced code) to an HTML fragment.

    Code blocks are handled separately by :class:`CodeBlockWidget`; this renders
    the surrounding prose. Falls back to escaped, paragraph-wrapped plain text.
    """
    if not text:
        return ""
    if _HAVE_MARKDOWN:
        try:
            return _markdown.markdown(
                text,
                extensions=["fenced_code", "tables", "sane_lists"],
                output_format="html5",
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug("markdown render failed", exc_info=True)
    escaped = html.escape(text).replace("\n\n", "</p><p>").replace("\n", "<br/>")
    return f"<p>{escaped}</p>"


# Fenced-code languages that are the agent's machine protocol, never shown raw.
_PROTOCOL_LANGUAGES = {"tool_call", "plan"}


def _segment_message(text: str) -> list[tuple[str, str, str]]:
    """Split ``text`` into ordered ("md"|"code", payload, language) segments.

    ``tool_call`` / ``plan`` protocol blocks are dropped — tool activity is shown
    via the tool-call chip stream, never as raw JSON code blocks in the bubble.
    """
    blocks = extract_code_blocks(text)
    if not blocks:
        return [("md", text, "")] if text else []
    segments: list[tuple[str, str, str]] = []
    cursor = 0
    for blk in blocks:
        if blk.start > cursor:
            prose = text[cursor:blk.start]
            if prose.strip():
                segments.append(("md", prose, ""))
        if (blk.language or "").strip().lower() not in _PROTOCOL_LANGUAGES:
            segments.append(("code", blk.code, blk.language))
        cursor = blk.end
    if cursor < len(text):
        tail = text[cursor:]
        if tail.strip():
            segments.append(("md", tail, ""))
    return segments


# --------------------------------------------------------------------------- #
# Widgets
# --------------------------------------------------------------------------- #
class _MarkdownView(QTextBrowser):
    """A read-only rich-text view sized to its content (no inner scrollbars)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setFrameStyle(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setStyleSheet("background: transparent; border: none;")
        self.document().contentsChanged.connect(self._fit_height)

    def set_html_fragment(self, fragment: str) -> None:
        self.setHtml(fragment)
        self._fit_height()

    def _fit_height(self) -> None:
        doc = self.document()
        doc.setTextWidth(max(1, self.viewport().width()))
        height = int(doc.size().height()) + 4
        self.setFixedHeight(max(height, 1))

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._fit_height()


class CodeBlockWidget(QFrame):
    """A fenced code block: language label, Copy button, highlighted body."""

    def __init__(
        self, code: str, language: str = "", parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._code = code
        self._language = language or "text"
        self.setObjectName("CodeArea")
        self.setProperty("class", "code")
        self.setFrameStyle(QFrame.Shape.StyledPanel)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame(self)
        header.setProperty("panel", "true")
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(8, 3, 6, 3)
        hbox.setSpacing(6)
        lang_label = QLabel(self._language, header)
        lang_label.setObjectName("SectionHeader")
        hbox.addWidget(lang_label)
        hbox.addStretch(1)
        self._copy_btn = QPushButton("Copy", header)
        self._copy_btn.setProperty("class", "ghost")
        self._copy_btn.setProperty("variant", "ghost")
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.clicked.connect(self._copy)
        hbox.addWidget(self._copy_btn)
        outer.addWidget(header)

        self._body = QTextBrowser(self)
        self._body.setObjectName("CodeArea")
        self._body.setProperty("class", "code")
        self._body.setFrameStyle(QFrame.Shape.NoFrame)
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setFamilies(["Menlo", "Consolas", "DejaVu Sans Mono", "monospace"])
        mono.setPointSize(10)
        self._body.setFont(mono)
        self._body.setHtml(self._build_html())
        self._body.document().contentsChanged.connect(self._fit_height)
        outer.addWidget(self._body)
        self._fit_height()

    def _build_html(self) -> str:
        css = _pygments_css()
        fragment = highlight_code(self._code, self._language)
        return (
            "<html><head><style>"
            f"body{{margin:0;background:{_CODE_BG};color:{_CODE_FG};}}"
            f"pre{{margin:0;white-space:pre;}}"
            f"{css}"
            "</style></head><body>"
            f"{fragment}"
            "</body></html>"
        )

    def _fit_height(self) -> None:
        doc = self._body.document()
        height = int(doc.size().height()) + 12
        # Cap very tall blocks so a single message can't fill the screen.
        self._body.setFixedHeight(min(max(height, 28), 600))

    def _copy(self) -> None:
        clip = QGuiApplication.clipboard()
        if clip is not None:
            clip.setText(self._code)
        self._copy_btn.setText("Copied")
        self._copy_btn.setEnabled(False)
        try:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(1200, self._reset_copy)
        except Exception:  # pragma: no cover
            self._reset_copy()

    def _reset_copy(self) -> None:
        self._copy_btn.setText("Copy")
        self._copy_btn.setEnabled(True)


class ToolChip(QFrame):
    """An inline chip representing an agent tool call and its live status."""

    _STATUS_TEXT = {
        ToolStatus.PENDING: "queued",
        ToolStatus.RUNNING: "running…",
        ToolStatus.OK: "done",
        ToolStatus.ERROR: "error",
        ToolStatus.DENIED: "denied",
        ToolStatus.AWAITING_APPROVAL: "awaiting approval",
    }

    def __init__(self, tool_dict: dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.tool_id: str = str(tool_dict.get("id", ""))
        self.setObjectName("Panel")
        self.setProperty("panel", "true")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(8)
        self._icon = QLabel("⚙", self)
        self._icon.setObjectName("RoleBadgeTool")
        lay.addWidget(self._icon)
        self._name = QLabel(self)
        self._name.setObjectName("RoleBadgeTool")
        lay.addWidget(self._name)
        self._status = QLabel(self)
        self._status.setObjectName("StatusBusy")
        lay.addWidget(self._status)
        self.update_from(tool_dict)

    def _status_enum(self, value: Any) -> ToolStatus:
        if isinstance(value, ToolStatus):
            return value
        try:
            return ToolStatus(str(value))
        except Exception:
            return ToolStatus.PENDING

    def update_from(self, tool_dict: dict[str, Any]) -> None:
        name = str(tool_dict.get("name", "tool"))
        self._name.setText(name)
        status = self._status_enum(tool_dict.get("status", ToolStatus.PENDING))
        risky = bool(tool_dict.get("risky", False))
        prefix = "⚠ " if risky else ""
        self._status.setText(prefix + self._STATUS_TEXT.get(status, str(status)))
        obj = "StatusBusy"
        if status is ToolStatus.OK:
            obj = "StatusOk"
        elif status in (ToolStatus.ERROR, ToolStatus.DENIED):
            obj = "StatusError"
        self._status.setObjectName(obj)
        self._repolish(self._status)
        error = tool_dict.get("error")
        out = tool_dict.get("output") or ""
        tip = error or (out[:400] if out else "")
        if tip:
            self.setToolTip(str(tip))

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)


class MessageBubble(QFrame):
    """A single chat bubble (user / assistant / tool) with mixed content."""

    _OBJ = {
        MessageRole.USER: ("ChatBubbleUser", "RoleBadgeUser", "You"),
        MessageRole.ASSISTANT: ("ChatBubbleAssistant", "RoleBadgeAssistant", "Animica"),
        MessageRole.TOOL: ("ChatBubbleTool", "RoleBadgeTool", "Tool"),
        MessageRole.SYSTEM: ("ChatBubbleTool", "RoleBadgeTool", "System"),
    }

    def __init__(
        self, role: MessageRole, text: str = "", parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._role = role
        self._raw_text = text
        bubble_obj, badge_obj, badge_text = self._OBJ.get(
            role, self._OBJ[MessageRole.ASSISTANT]
        )
        self.setObjectName(bubble_obj)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(12, 8, 12, 8)
        self._outer.setSpacing(6)

        self._badge = QLabel(badge_text, self)
        self._badge.setObjectName(badge_obj)
        self._outer.addWidget(self._badge)

        # Container for dynamically rebuilt content segments.
        self._content = QWidget(self)
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(0, 0, 0, 0)
        self._content_lay.setSpacing(6)
        self._outer.addWidget(self._content)

        self.set_text(text)

    @property
    def role(self) -> MessageRole:
        return self._role

    def set_text(self, text: str) -> None:
        """Replace the bubble content, re-segmenting markdown/code."""
        self._raw_text = text or ""
        self._rebuild()

    def append_chunk(self, delta: str) -> None:
        """Append a streaming delta and re-render (used for live assistant text)."""
        self._raw_text += delta
        self._rebuild()

    def text(self) -> str:
        return self._raw_text

    def _clear_content(self) -> None:
        while self._content_lay.count():
            item = self._content_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _rebuild(self) -> None:
        self._clear_content()
        segments = _segment_message(self._raw_text)
        if not segments:
            placeholder = _MarkdownView(self._content)
            placeholder.set_html_fragment("<p style='color:#5b6b86;'>…</p>")
            self._content_lay.addWidget(placeholder)
            return
        for kind, payload, language in segments:
            if kind == "code":
                self._content_lay.addWidget(
                    CodeBlockWidget(payload, language, self._content)
                )
            else:
                view = _MarkdownView(self._content)
                view.set_html_fragment(render_markdown(payload))
                self._content_lay.addWidget(view)


class _ChatInput(QPlainTextEdit):
    """Multiline input that emits :attr:`submitted` on Ctrl/Cmd+Enter."""

    submitted = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ChatInput")
        self.setPlaceholderText(
            "Message Animica Studio Agent…  (Ctrl+Enter to send)"
        )
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setMaximumHeight(160)
        self.setMinimumHeight(56)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt naming)
        key = event.key()
        mods = event.modifiers()
        is_enter = key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ctrl_like = bool(
            mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        )
        if is_enter and ctrl_like:
            event.accept()
            self.submitted.emit()
            return
        super().keyPressEvent(event)


# --------------------------------------------------------------------------- #
# ChatPanel
# --------------------------------------------------------------------------- #
class ChatPanel(QWidget):
    """The conversation view + input, bound to an :class:`AgentEngine`.

    Parameters
    ----------
    agent_engine:
        The engine whose signals drive the live conversation. May be ``None`` to
        construct a dormant panel (still renders history from the DB).
    db:
        Database for loading session history and the conversation switcher.
    """

    def __init__(
        self,
        agent_engine: Optional[Any] = None,
        db: Optional[Any] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ChatPanel")
        self._engine = agent_engine
        self._db = db
        self._session_id: Optional[str] = None
        self._streaming_bubble: Optional[MessageBubble] = None
        self._tool_chips: dict[str, ToolChip] = {}
        self._busy = False

        self._build_ui()
        self._connect_engine()
        self._refresh_sessions()

    # ------------------------------------------------------------------ UI ---
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- header: session switcher + new chat ---------------------------
        header = QFrame(self)
        header.setObjectName("Panel")
        header.setProperty("panel", "true")
        hb = QHBoxLayout(header)
        hb.setContentsMargins(10, 6, 10, 6)
        hb.setSpacing(8)
        title = QLabel("Conversation", header)
        title.setObjectName("SectionHeader")
        hb.addWidget(title)
        self._session_combo = QComboBox(header)
        self._session_combo.setMinimumWidth(180)
        self._session_combo.currentIndexChanged.connect(self._on_session_selected)
        hb.addWidget(self._session_combo, 1)
        self._new_btn = QPushButton("New Chat", header)
        self._new_btn.setProperty("class", "ghost")
        self._new_btn.setProperty("variant", "ghost")
        self._new_btn.clicked.connect(self._on_new_chat)
        hb.addWidget(self._new_btn)
        root.addWidget(header)

        # -- scrollable conversation ---------------------------------------
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("ChatScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setFrameStyle(QFrame.Shape.NoFrame)
        self._messages_host = QWidget()
        self._messages_host.setObjectName("ChatMessages")
        self._messages_lay = QVBoxLayout(self._messages_host)
        self._messages_lay.setContentsMargins(14, 14, 14, 14)
        self._messages_lay.setSpacing(12)
        self._messages_lay.addStretch(1)  # keeps bubbles top-aligned
        self._scroll.setWidget(self._messages_host)
        root.addWidget(self._scroll, 1)

        # -- status line ---------------------------------------------------
        self._status_label = QLabel("", self)
        self._status_label.setObjectName("StatusOk")
        self._status_label.setContentsMargins(14, 0, 14, 0)
        root.addWidget(self._status_label)

        # -- input row -----------------------------------------------------
        input_row = QFrame(self)
        input_row.setObjectName("Panel")
        input_row.setProperty("panel", "true")
        ir = QHBoxLayout(input_row)
        ir.setContentsMargins(10, 8, 10, 10)
        ir.setSpacing(8)
        self._input = _ChatInput(input_row)
        self._input.submitted.connect(self._on_send)
        ir.addWidget(self._input, 1)

        btns = QVBoxLayout()
        btns.setSpacing(6)
        self._send_btn = QPushButton("Send", input_row)
        self._send_btn.setProperty("class", "primary")
        self._send_btn.setProperty("variant", "primary")
        self._send_btn.clicked.connect(self._on_send)
        btns.addWidget(self._send_btn)
        self._stop_btn = QPushButton("Stop", input_row)
        self._stop_btn.setProperty("class", "danger")
        self._stop_btn.setProperty("variant", "danger")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        btns.addWidget(self._stop_btn)
        ir.addLayout(btns)
        root.addWidget(input_row)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(560, 640)

    # -------------------------------------------------------------- engine ---
    def _connect_engine(self) -> None:
        engine = self._engine
        if engine is None:
            return
        # Bind defensively: tolerate engines missing optional signals.
        for sig_name, slot in (
            ("assistantChunk", self._on_assistant_chunk),
            ("assistantMessage", self._on_assistant_message),
            ("toolCallStarted", self._on_tool_started),
            ("toolCallFinished", self._on_tool_finished),
            ("approvalRequested", self._on_approval_requested),
            ("statusChanged", self._on_status_changed),
            ("errorOccurred", self._on_error),
            ("busyChanged", self._on_busy_changed),
        ):
            sig = getattr(engine, sig_name, None)
            if sig is not None and hasattr(sig, "connect"):
                try:
                    sig.connect(slot)
                except Exception:  # pragma: no cover - defensive binding
                    logger.debug("could not bind signal %s", sig_name, exc_info=True)

    def set_engine(self, engine: Any) -> None:
        """(Re)bind the panel to an agent engine."""
        self._engine = engine
        self._connect_engine()

    # ------------------------------------------------------------ sessions ---
    def _refresh_sessions(self) -> None:
        if self._db is None:
            return
        try:
            sessions = self._db.list_sessions()
        except Exception:  # pragma: no cover - db optional/headless
            logger.debug("list_sessions failed", exc_info=True)
            return
        self._session_combo.blockSignals(True)
        self._session_combo.clear()
        for sess in sessions:
            self._session_combo.addItem(sess.title or "Untitled", sess.id)
        self._session_combo.blockSignals(False)
        if self._session_id is not None:
            idx = self._session_combo.findData(self._session_id)
            if idx >= 0:
                self._session_combo.setCurrentIndex(idx)

    def set_session(self, session_id: str) -> None:
        """Switch the panel to ``session_id``: load history and notify the engine."""
        self._session_id = session_id
        self._clear_messages()
        self._load_history(session_id)
        idx = self._session_combo.findData(session_id)
        if idx >= 0:
            self._session_combo.blockSignals(True)
            self._session_combo.setCurrentIndex(idx)
            self._session_combo.blockSignals(False)
        engine = self._engine
        if engine is not None and hasattr(engine, "set_session"):
            try:
                engine.set_session(session_id)
            except Exception:  # pragma: no cover
                logger.debug("engine.set_session failed", exc_info=True)

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    def _on_session_selected(self, index: int) -> None:
        if index < 0:
            return
        sid = self._session_combo.itemData(index)
        if sid and sid != self._session_id:
            self.set_session(str(sid))

    def _on_new_chat(self) -> None:
        if self._db is None:
            self._clear_messages()
            self._session_id = None
            return
        try:
            sess = self._db.create_session()
        except Exception:  # pragma: no cover
            logger.debug("create_session failed", exc_info=True)
            return
        self._refresh_sessions()
        self.set_session(sess.id)

    def _load_history(self, session_id: str) -> None:
        if self._db is None:
            return
        try:
            messages = self._db.list_messages(session_id)
        except Exception:  # pragma: no cover
            logger.debug("list_messages failed", exc_info=True)
            messages = []
        for msg in messages:
            if msg.role is MessageRole.SYSTEM:
                continue
            if msg.role is MessageRole.TOOL:
                self._add_history_tool(msg)
            else:
                self._add_bubble(msg.role, msg.content)
        # Inline any logged tool calls after their textual context.
        try:
            tool_calls = self._db.list_tool_calls(session_id)
        except Exception:  # pragma: no cover
            tool_calls = []
        for tc in tool_calls:
            d = tc.to_dict() if hasattr(tc, "to_dict") else {}
            if d and d.get("id") not in self._tool_chips:
                self._add_tool_chip(d)
        self._scroll_to_bottom()

    def _add_history_tool(self, msg: Message) -> None:
        chip = ToolChip(
            {
                "id": msg.tool_call_id or msg.id,
                "name": msg.name or "tool",
                "status": ToolStatus.OK.value,
                "output": msg.content,
            },
            self._messages_host,
        )
        self._insert_widget(chip)

    # ------------------------------------------------------------- bubbles ---
    def _insert_widget(self, widget: QWidget) -> None:
        # Insert before the trailing stretch (last layout item).
        count = self._messages_lay.count()
        self._messages_lay.insertWidget(max(0, count - 1), widget)

    def _add_bubble(self, role: MessageRole, text: str) -> MessageBubble:
        bubble = MessageBubble(role, text, self._messages_host)
        self._insert_widget(bubble)
        self._scroll_to_bottom()
        return bubble

    def _add_tool_chip(self, tool_dict: dict[str, Any]) -> ToolChip:
        chip = ToolChip(tool_dict, self._messages_host)
        tid = chip.tool_id
        if tid:
            self._tool_chips[tid] = chip
        self._insert_widget(chip)
        self._scroll_to_bottom()
        return chip

    def _clear_messages(self) -> None:
        self._streaming_bubble = None
        self._tool_chips.clear()
        # Remove everything except the trailing stretch.
        while self._messages_lay.count() > 1:
            item = self._messages_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _scroll_to_bottom(self) -> None:
        try:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self._do_scroll)
        except Exception:  # pragma: no cover
            self._do_scroll()

    def _do_scroll(self) -> None:
        bar = self._scroll.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    # ---------------------------------------------------------- send/stop ---
    def _on_send(self) -> None:
        text = self._input.toPlainText().strip()
        if not text:
            return
        if self._engine is None:
            self._add_bubble(MessageRole.USER, text)
            self._input.clear()
            self._on_error("No agent engine is connected.")
            return
        self._input.clear()
        self._add_bubble(MessageRole.USER, text)
        # Prepare a fresh assistant bubble for streaming deltas.
        self._streaming_bubble = self._add_bubble(MessageRole.ASSISTANT, "")
        self._set_busy(True)
        try:
            self._engine.send_user_message(text)
        except Exception as exc:  # pragma: no cover - engine errors surfaced
            logger.debug("send_user_message failed", exc_info=True)
            self._on_error(f"Failed to send message: {exc}")
            self._set_busy(False)

    def _on_stop(self) -> None:
        engine = self._engine
        if engine is not None and hasattr(engine, "stop"):
            try:
                engine.stop()
            except Exception:  # pragma: no cover
                logger.debug("engine.stop failed", exc_info=True)
        self._set_busy(False)
        self._on_status_changed("Stopped.")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._send_btn.setEnabled(not busy)
        self._stop_btn.setEnabled(busy)
        self._input.setReadOnly(busy)

    # ------------------------------------------------------- engine slots ---
    def _on_assistant_chunk(self, delta: str) -> None:
        if not delta:
            return
        if self._streaming_bubble is None:
            self._streaming_bubble = self._add_bubble(MessageRole.ASSISTANT, "")
        self._streaming_bubble.append_chunk(delta)
        self._scroll_to_bottom()

    def _on_assistant_message(self, text: str) -> None:
        if self._streaming_bubble is not None:
            # Finalize: render the authoritative full text — but don't blank out a
            # bubble that already streamed prose if the final text is empty (e.g. a
            # turn that ended on tool calls with no closing message).
            if text or not self._streaming_bubble.text().strip():
                self._streaming_bubble.set_text(text)
            self._streaming_bubble = None
        elif text:
            self._add_bubble(MessageRole.ASSISTANT, text)
        self._set_busy(False)
        self._scroll_to_bottom()
        self._refresh_session_titles()

    def _on_tool_started(self, tool_dict: dict[str, Any]) -> None:
        tid = str(tool_dict.get("id", ""))
        # New tool call begins below the current streaming text; detach so the
        # next assistant delta starts a fresh bubble after the tool result.
        self._streaming_bubble = None
        if tid and tid in self._tool_chips:
            self._tool_chips[tid].update_from(tool_dict)
        else:
            self._add_tool_chip(tool_dict)

    def _on_tool_finished(self, tool_dict: dict[str, Any]) -> None:
        tid = str(tool_dict.get("id", ""))
        chip = self._tool_chips.get(tid)
        if chip is not None:
            chip.update_from(tool_dict)
        else:
            self._add_tool_chip(tool_dict)

    def _on_approval_requested(
        self, action: str, callback: Callable[[bool], None]
    ) -> None:
        box = QMessageBox(self)
        box.setObjectName("ApprovalDialog")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Approval required")
        box.setText("The agent wants to run a potentially risky action:")
        box.setInformativeText(action)
        approve = box.addButton("Approve once", QMessageBox.ButtonRole.AcceptRole)
        deny = box.addButton("Deny", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(deny)
        box.exec()
        allowed = box.clickedButton() is approve
        try:
            callback(bool(allowed))
        except Exception:  # pragma: no cover - never crash on caller's callback
            logger.debug("approval callback failed", exc_info=True)

    def _on_status_changed(self, text: str) -> None:
        self._status_label.setText(text or "")
        self._status_label.setObjectName("StatusBusy" if self._busy else "StatusOk")
        self._repolish(self._status_label)

    def _on_error(self, text: str) -> None:
        self._status_label.setText(text or "Error")
        self._status_label.setObjectName("StatusError")
        self._repolish(self._status_label)
        self._set_busy(False)

    def _on_busy_changed(self, busy: bool) -> None:
        self._set_busy(bool(busy))

    # --------------------------------------------------------------- misc ---
    def _refresh_session_titles(self) -> None:
        """Update the switcher label if the engine retitled the session."""
        if self._db is None or self._session_id is None:
            return
        try:
            sess = self._db.get_session(self._session_id)
        except Exception:  # pragma: no cover
            return
        if sess is None:
            return
        idx = self._session_combo.findData(self._session_id)
        if idx >= 0 and self._session_combo.itemText(idx) != sess.title:
            self._session_combo.setItemText(idx, sess.title or "Untitled")

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)
            widget.update()


__all__ = [
    "ChatPanel",
    "MessageBubble",
    "CodeBlockWidget",
    "ToolChip",
    "render_markdown",
    "highlight_code",
]
