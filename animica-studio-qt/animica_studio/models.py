"""Core domain models for Animica Studio Qt.

These dataclasses and enums are the shared vocabulary used by the database layer,
the services layer, the agent layer, and the UI. They are intentionally free of
any Qt or sqlite imports so they can be used everywhere (including headless).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class AutonomyLevel(str, Enum):
    """How much freedom the agent has to act without the human in the loop."""

    CHAT_ONLY = "chat_only"
    SUGGEST = "suggest"
    APPLY_WITH_APPROVAL = "apply_with_approval"
    FULL_AGENT = "full_agent"
    AUTONOMOUS_LOOP = "autonomous_loop"

    @classmethod
    def from_value(cls, value: "AutonomyLevel | str | None") -> "AutonomyLevel":
        """Coerce a string/enum into an AutonomyLevel, defaulting to SUGGEST."""
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.SUGGEST
        try:
            return cls(str(value))
        except ValueError:
            # Accept enum NAME too (e.g. "FULL_AGENT").
            try:
                return cls[str(value).upper()]
            except KeyError:
                return cls.SUGGEST

    @property
    def label(self) -> str:
        return {
            AutonomyLevel.CHAT_ONLY: "Chat Only",
            AutonomyLevel.SUGGEST: "Suggest Changes",
            AutonomyLevel.APPLY_WITH_APPROVAL: "Apply With Approval",
            AutonomyLevel.FULL_AGENT: "Full Project Agent",
            AutonomyLevel.AUTONOMOUS_LOOP: "Autonomous Loop",
        }[self]

    @property
    def rank(self) -> int:
        """Numeric ordering (higher == more autonomous)."""
        order = [
            AutonomyLevel.CHAT_ONLY,
            AutonomyLevel.SUGGEST,
            AutonomyLevel.APPLY_WITH_APPROVAL,
            AutonomyLevel.FULL_AGENT,
            AutonomyLevel.AUTONOMOUS_LOOP,
        ]
        return order.index(self)

    @property
    def allows_tools(self) -> bool:
        """Whether tools may run at all (CHAT_ONLY forbids tool execution)."""
        return self is not AutonomyLevel.CHAT_ONLY

    @property
    def auto_apply(self) -> bool:
        """Whether non-risky edits apply without per-change confirmation."""
        return self.rank >= AutonomyLevel.FULL_AGENT.rank


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

    @classmethod
    def from_value(cls, value: "MessageRole | str") -> "MessageRole":
        if isinstance(value, cls):
            return value
        return cls(str(value))


class ToolStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"
    DENIED = "denied"
    AWAITING_APPROVAL = "awaiting_approval"


class ApprovalDecision(str, Enum):
    """Outcome of a command/tool approval prompt."""

    APPROVE_ONCE = "approve_once"
    DENY = "deny"
    ALWAYS_ALLOW = "always_allow"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def new_id() -> str:
    """Generate a short unique identifier."""
    return uuid.uuid4().hex


def now_ts() -> float:
    """Current wall-clock time as a float epoch seconds."""
    return time.time()


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #
@dataclass
class Session:
    """A chat/agent conversation session."""

    id: str = field(default_factory=new_id)
    title: str = "New Chat"
    project_id: Optional[str] = None
    model: Optional[str] = None
    autonomy_level: AutonomyLevel = AutonomyLevel.SUGGEST
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "project_id": self.project_id,
            "model": self.model,
            "autonomy_level": self.autonomy_level.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Message:
    """A single message within a session."""

    id: str = field(default_factory=new_id)
    session_id: str = ""
    role: MessageRole = MessageRole.USER
    content: str = ""
    name: Optional[str] = None  # tool name when role == TOOL
    tool_call_id: Optional[str] = None
    created_at: float = field(default_factory=now_ts)
    token_count: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_openai(self) -> dict[str, Any]:
        """Render as an OpenAI chat-completions message dict."""
        msg: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.role is MessageRole.TOOL:
            if self.tool_call_id:
                msg["tool_call_id"] = self.tool_call_id
            if self.name:
                msg["name"] = self.name
        return msg


@dataclass
class Project:
    """An opened project workspace."""

    id: str = field(default_factory=new_id)
    name: str = ""
    root_path: str = ""
    project_type: str = "unknown"
    description: str = ""
    last_opened_at: float = field(default_factory=now_ts)
    created_at: float = field(default_factory=now_ts)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    """A record of a tool invocation by the agent."""

    id: str = field(default_factory=new_id)
    session_id: str = ""
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    status: ToolStatus = ToolStatus.PENDING
    output: str = ""
    error: Optional[str] = None
    risky: bool = False
    created_at: float = field(default_factory=now_ts)
    finished_at: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class CommandApproval:
    """A persisted approval/denial decision for a command or tool."""

    id: str = field(default_factory=new_id)
    project_id: Optional[str] = None
    command: str = ""
    category: str = "general"
    decision: ApprovalDecision = ApprovalDecision.APPROVE_ONCE
    always: bool = False
    created_at: float = field(default_factory=now_ts)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["decision"] = self.decision.value
        return d


@dataclass
class FileIndexEntry:
    """An indexed file within a project (for keyword search / context)."""

    id: str = field(default_factory=new_id)
    project_id: str = ""
    path: str = ""  # path relative to project root
    language: str = ""
    size: int = 0
    mtime: float = 0.0
    summary: str = ""
    symbols: list[str] = field(default_factory=list)
    content_excerpt: str = ""
    indexed_at: float = field(default_factory=now_ts)


@dataclass
class ToolResult:
    """Uniform return value for every agent tool."""

    ok: bool = True
    output: str = ""
    error: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, output: str = "", **data: Any) -> "ToolResult":
        return cls(ok=True, output=output, data=dict(data))

    @classmethod
    def failure(cls, error: str, output: str = "", **data: Any) -> "ToolResult":
        return cls(ok=False, output=output, error=error, data=dict(data))

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "output": self.output, "error": self.error, "data": self.data}


@dataclass
class PlanStep:
    """One step of an agent-produced plan."""

    index: int = 0
    title: str = ""
    detail: str = ""
    done: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FileDiff:
    """A proposed change to a single file, for the diff viewer."""

    path: str = ""           # relative path
    old_text: str = ""
    new_text: str = ""
    is_new: bool = False
    is_delete: bool = False
    is_rename: bool = False
    new_path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Markdown / code-block helpers
# --------------------------------------------------------------------------- #
@dataclass
class CodeBlock:
    """A fenced code block extracted from markdown text."""

    language: str
    code: str
    start: int
    end: int


def extract_code_blocks(text: str) -> list[CodeBlock]:
    """Extract triple-backtick fenced code blocks from ``text``.

    Returns a list of :class:`CodeBlock` with the optional language tag.
    """
    blocks: list[CodeBlock] = []
    fence = "```"
    i = 0
    n = len(text)
    while True:
        start = text.find(fence, i)
        if start == -1:
            break
        # language tag runs to end of line
        nl = text.find("\n", start + len(fence))
        if nl == -1:
            break
        language = text[start + len(fence):nl].strip()
        end = text.find(fence, nl + 1)
        if end == -1:
            break
        code = text[nl + 1:end]
        blocks.append(CodeBlock(language=language, code=code, start=start, end=end + len(fence)))
        i = end + len(fence)
    return blocks


def strip_code_fences(text: str) -> str:
    """Return ``text`` with the first fenced code block's body if present, else text."""
    blocks = extract_code_blocks(text)
    if blocks:
        return blocks[0].code
    return text


__all__ = [
    "AutonomyLevel",
    "MessageRole",
    "ToolStatus",
    "ApprovalDecision",
    "Session",
    "Message",
    "Project",
    "ToolCall",
    "CommandApproval",
    "FileIndexEntry",
    "ToolResult",
    "PlanStep",
    "FileDiff",
    "CodeBlock",
    "extract_code_blocks",
    "strip_code_fences",
    "new_id",
    "now_ts",
]
