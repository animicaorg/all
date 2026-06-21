"""Tool-call execution for the Animica Studio Agent.

:class:`ToolExecutor` parses the model's JSON tool-call protocol (see
:mod:`animica_studio.agent.prompts`), enforces the autonomy level + approval
gating, executes each call via :class:`animica_studio.agent.tools.ToolManager`,
and produces the records (tool calls, file diffs) the engine emits to the UI.

Approval is asynchronous in nature (the UI shows a dialog), so the executor calls
an injected ``request_approval(action, category) -> bool`` callable that BLOCKS
the worker thread until the UI responds. The engine wires this to a
``QEventLoop``-free threading primitive so the GUI thread is never blocked.

This module has no Qt dependency and imports headless.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

from ..models import (
    AutonomyLevel,
    FileDiff,
    ToolCall,
    ToolResult,
    ToolStatus,
    now_ts,
)
from . import prompts
from .prompts import TOOL_CALL_FENCE
from .tools import ALWAYS_APPROVE_TOOLS, SOFT_WRITE_TOOLS, ToolManager

if TYPE_CHECKING:  # pragma: no cover
    from ..db import Database


# Callback the executor uses to ask the UI for approval. Returns True if allowed.
ApprovalFn = Callable[[str, str], bool]
# Callback for soft-write diff approval. Returns True if the diff is accepted.
DiffApprovalFn = Callable[[list[FileDiff]], bool]
# Callback to surface a tool call lifecycle event (started/finished).
EventFn = Callable[[ToolCall], None]


@dataclass
class ParsedToolCall:
    """A tool call parsed from model output (fenced text or native tool_calls)."""

    name: str
    arguments: dict[str, Any]
    id: str = ""  # native tool_call id (empty for the fenced-text protocol)


@dataclass
class ExecutionResult:
    """Aggregate outcome of executing the tool calls in one model turn."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    diffs: list[FileDiff] = field(default_factory=list)
    any_executed: bool = False
    cancelled: bool = False

    @property
    def tool_messages(self) -> list[dict[str, Any]]:
        """Render results as OpenAI ``tool`` messages to feed back to the model."""
        msgs: list[dict[str, Any]] = []
        for tc in self.tool_calls:
            payload = tc.output if tc.status is ToolStatus.OK else (tc.error or tc.output)
            msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": payload or "(no output)",
                }
            )
        return msgs


class ToolExecutor:
    """Parses, gates, and runs the model's tool calls."""

    def __init__(
        self,
        tool_manager: ToolManager,
        *,
        autonomy: AutonomyLevel = AutonomyLevel.SUGGEST,
        request_approval: Optional[ApprovalFn] = None,
        request_diff_approval: Optional[DiffApprovalFn] = None,
        on_tool_started: Optional[EventFn] = None,
        on_tool_finished: Optional[EventFn] = None,
        db: Optional["Database"] = None,
        session_id: Optional[str] = None,
        cancel: Optional[Callable[[], bool]] = None,
        project_id: Optional[str] = None,
    ) -> None:
        self.tools = tool_manager
        self.autonomy = autonomy
        self._request_approval = request_approval or (lambda action, cat: True)
        self._request_diff_approval = request_diff_approval or (lambda diffs: True)
        self._on_started = on_tool_started
        self._on_finished = on_tool_finished
        self.db = db
        self.session_id = session_id
        self._cancel = cancel or (lambda: False)
        self.project_id = project_id
        # Wire the ToolManager's approval gate to ours (for ALWAYS_APPROVE tools
        # the manager calls ctx.approve internally; we intercept here too).
        self.tools.ctx.approve = self._approve_with_persistence

    # ------------------------------------------------------------------ #
    @property
    def is_cancelled(self) -> bool:
        try:
            return bool(self._cancel())
        except Exception:
            return False

    # -- parsing -------------------------------------------------------- #
    @staticmethod
    def parse_tool_calls(text: str) -> list[ParsedToolCall]:
        """Extract every tool call from model output (fenced JSON protocol)."""
        calls: list[ParsedToolCall] = []
        fence = f"```{TOOL_CALL_FENCE}"
        i = 0
        n = len(text)
        while True:
            start = text.find(fence, i)
            if start == -1:
                break
            nl = text.find("\n", start)
            if nl == -1:
                break
            end = text.find("```", nl + 1)
            if end == -1:
                break
            body = text[nl + 1 : end].strip()
            i = end + 3
            calls.extend(_parse_call_body(body))
        return calls

    @staticmethod
    def has_tool_calls(text: str) -> bool:
        return f"```{TOOL_CALL_FENCE}" in (text or "")

    @staticmethod
    def parse_native_tool_calls(tool_calls: list[dict[str, Any]]) -> list[ParsedToolCall]:
        """Convert OpenAI native ``tool_calls`` into :class:`ParsedToolCall`."""
        out: list[ParsedToolCall] = []
        for tc in tool_calls or []:
            if not isinstance(tc, dict):
                continue
            name = tc.get("name")
            if not name:
                continue
            raw_args = tc.get("arguments")
            if isinstance(raw_args, dict):
                args = raw_args
            elif isinstance(raw_args, str) and raw_args.strip():
                args = _loads_lenient(raw_args)
                if not isinstance(args, dict):
                    args = {}
            else:
                args = {}
            out.append(ParsedToolCall(name=str(name), arguments=args, id=str(tc.get("id") or "")))
        return out

    # -- execution ------------------------------------------------------ #
    def execute(
        self,
        model_text: str,
        native_tool_calls: Optional[list[dict[str, Any]]] = None,
    ) -> ExecutionResult:
        """Parse and execute the tool calls in one model turn.

        Native ``tool_calls`` (when the server used real function-calling) take
        precedence; otherwise the fenced-text protocol in ``model_text`` is parsed.
        """
        result = ExecutionResult()
        if not self.autonomy.allows_tools:
            # CHAT_ONLY: never execute tools.
            return result
        parsed = self.parse_native_tool_calls(native_tool_calls or [])
        if not parsed:
            parsed = self.parse_tool_calls(model_text)
        for pc in parsed:
            if self.is_cancelled:
                result.cancelled = True
                break
            tc = self._execute_one(pc)
            result.tool_calls.append(tc)
            result.any_executed = True
            # Pull any FileDiff produced into the aggregate.
            d = self._extract_diff(tc)
            if d is not None:
                result.diffs.append(d)
        return result

    def _execute_one(self, pc: ParsedToolCall) -> ToolCall:
        name = pc.name
        arguments = pc.arguments
        risky = self.tools.is_risky(name, arguments)
        tc_kwargs: dict[str, Any] = dict(
            session_id=self.session_id or "",
            name=name,
            arguments=dict(arguments),
            risky=risky,
            status=ToolStatus.RUNNING,
        )
        if pc.id:
            # Preserve the native tool_call id so the tool-result message maps
            # back to the assistant's tool_calls entry (strict OpenAI servers).
            tc_kwargs["id"] = pc.id
        tc = ToolCall(**tc_kwargs)
        self._emit_started(tc)
        self._persist(tc)

        # Gate: soft-write tools are governed by autonomy + diff approval; the
        # ALWAYS_APPROVE tools have their own approve() gate inside ToolManager.
        try:
            if name in SOFT_WRITE_TOOLS:
                allowed, denial = self._gate_soft_write(name, arguments)
                if not allowed:
                    return self._finish(tc, ToolResult.failure(denial))
            result = self.tools.call(name, arguments)
        except Exception as exc:  # pragma: no cover - defensive
            result = ToolResult.failure(f"{type(exc).__name__}: {exc}")
        return self._finish(tc, result)

    # -- gating --------------------------------------------------------- #
    def _gate_soft_write(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        """Apply autonomy gating for write/patch/create/rename.

        - FULL_AGENT / AUTONOMOUS_LOOP: auto-apply (no prompt).
        - SUGGEST / APPLY_WITH_APPROVAL: build the would-be diff and ask the UI to
          accept it before applying.
        """
        if self.autonomy.auto_apply:
            return True, ""
        diff = self._preview_diff(name, arguments)
        if diff is None:
            # Can't preview (e.g. missing file) — fall back to a generic approval.
            ok = self._request_approval(f"{name} {arguments.get('path', '')}", "general")
            return (ok, "" if ok else "Change denied by user")
        accepted = self._request_diff_approval([diff])
        return (accepted, "" if accepted else "Change rejected in diff viewer")

    def _preview_diff(self, name: str, arguments: dict[str, Any]) -> Optional[FileDiff]:
        ps = self.tools.ctx.project_service
        if ps is None:
            return None
        path = str(arguments.get("path", "") or "")
        try:
            if name == "create_file":
                return FileDiff(
                    path=path, old_text="", new_text=str(arguments.get("content", "")), is_new=True
                )
            if name == "rename_file":
                return FileDiff(
                    path=str(arguments.get("src", "")),
                    is_rename=True,
                    new_path=str(arguments.get("dst", "")),
                )
            old = ""
            is_new = False
            try:
                old = ps.read_file(path)
            except Exception:
                is_new = True
            if name == "write_file":
                return FileDiff(
                    path=path, old_text=old, new_text=str(arguments.get("content", "")), is_new=is_new
                )
            if name == "patch_file":
                # Compute new text without writing, mirroring ToolManager._patch_file.
                from .patching import PatchError, apply_patch  # headless-safe

                diff_text = arguments.get("diff")
                search = arguments.get("search")
                replace = arguments.get("replace")
                try:
                    new_text = apply_patch(
                        old,
                        diff=str(diff_text) if diff_text else None,
                        search=None if search is None else str(search),
                        replace=None if replace is None else str(replace),
                    )
                except PatchError:
                    return None
                return FileDiff(path=path, old_text=old, new_text=new_text, is_new=is_new)
        except Exception:
            return None
        return None

    def _approve_with_persistence(self, action: str, category: str) -> bool:
        """Approval gate used by ToolManager for ALWAYS_APPROVE tools.

        Honors a persisted ``always_allow`` for the (category, project) and
        persists an ``always`` approval when the UI returns one. The UI returns a
        plain bool here; the engine is responsible for translating an
        "Always allow" decision into ``always=True`` before calling back (it does
        so by recording the approval itself), so we only check the persisted flag.
        """
        # Short-circuit on a recorded always-allow.
        if self.db is not None:
            try:
                if self.db.find_always_allow(category, self.project_id):
                    return True
            except Exception:
                pass
        try:
            return bool(self._request_approval(action, category))
        except Exception:
            return False

    # -- lifecycle bookkeeping ----------------------------------------- #
    def _finish(self, tc: ToolCall, result: ToolResult) -> ToolCall:
        tc.finished_at = now_ts()
        if result.ok:
            tc.status = ToolStatus.OK
            tc.output = result.output
        else:
            err = (result.error or "").lower()
            if "denied" in err or "rejected" in err:
                tc.status = ToolStatus.DENIED
            else:
                tc.status = ToolStatus.ERROR
            tc.error = result.error
            tc.output = result.output
        # Stash diff (if any) for the engine to surface.
        diff = result.data.get("diff")
        if diff is not None:
            tc.metadata_diff = diff  # type: ignore[attr-defined]
        self._persist(tc)
        self._emit_finished(tc)
        return tc

    def _extract_diff(self, tc: ToolCall) -> Optional[FileDiff]:
        raw = getattr(tc, "metadata_diff", None)
        if not isinstance(raw, dict):
            return None
        try:
            return FileDiff(
                path=raw.get("path", ""),
                old_text=raw.get("old_text", ""),
                new_text=raw.get("new_text", ""),
                is_new=raw.get("is_new", False),
                is_delete=raw.get("is_delete", False),
                is_rename=raw.get("is_rename", False),
                new_path=raw.get("new_path"),
            )
        except Exception:
            return None

    def _emit_started(self, tc: ToolCall) -> None:
        if self._on_started is not None:
            try:
                self._on_started(tc)
            except Exception:
                pass

    def _emit_finished(self, tc: ToolCall) -> None:
        if self._on_finished is not None:
            try:
                self._on_finished(tc)
            except Exception:
                pass

    def _persist(self, tc: ToolCall) -> None:
        if self.db is None or not self.session_id:
            return
        try:
            self.db.log_tool_call(tc)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def _parse_call_body(body: str) -> list[ParsedToolCall]:
    """Parse a tool_call fence body into one or more ParsedToolCall."""
    data = _loads_lenient(body)
    if data is None:
        return []
    out: list[ParsedToolCall] = []
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("tool") or item.get("name")
        if not name:
            continue
        args = item.get("arguments")
        if args is None:
            args = item.get("args", {})
        if not isinstance(args, dict):
            args = {}
        out.append(ParsedToolCall(name=str(name), arguments=args))
    return out


def _loads_lenient(s: str) -> Any:
    s = s.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        pass
    # Try to recover by trimming to the first balanced object/array.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = s.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for idx in range(start, len(s)):
            if s[idx] == open_ch:
                depth += 1
            elif s[idx] == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start : idx + 1])
                    except (json.JSONDecodeError, ValueError):
                        break
    return None


__all__ = [
    "ToolExecutor",
    "ExecutionResult",
    "ParsedToolCall",
    "ApprovalFn",
    "DiffApprovalFn",
]
