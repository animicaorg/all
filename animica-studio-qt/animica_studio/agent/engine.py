"""The Animica Studio Agent orchestrator: :class:`AgentEngine`.

``AgentEngine`` is the single object the UI talks to. It owns the conversation
loop: it assembles context, optionally plans, calls the model on a worker thread
(streaming token deltas), parses + executes tool calls (with approval/diff
gating), drives the autonomous error-fix loop, and persists everything to the DB.

Threading model
---------------
``send_user_message`` returns immediately; the heavy work runs on a
:class:`QThread` worker. Signals are emitted from the worker but Qt delivers them
to the GUI thread queued, so the UI never blocks. Approval is handled by emitting
``approvalRequested(action, callback)`` and blocking the *worker* (not the GUI) on
a :class:`threading.Event` until the UI invokes the callback.

This file must import headless (``QT_QPA_PLATFORM=offscreen``). All UI-free.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Any, Optional

from PySide6.QtCore import QObject, QThread, Signal

from ..config import Settings, redact
from ..models import (
    AutonomyLevel,
    FileDiff,
    Message,
    MessageRole,
    ToolCall,
    new_id,
)
from . import prompts
from .context import ContextBuilder
from .executor import ToolExecutor
from .planner import Planner
from .tools import SOFT_WRITE_TOOLS, ToolContext, ToolManager

if TYPE_CHECKING:  # pragma: no cover
    from ..db import Database
    from ..services.animica_client import AnimicaClient
    from ..services.command_runner import CommandRunner
    from ..services.git_service import GitService
    from ..services.indexer import Indexer
    from ..services.project_service import ProjectService

logger = logging.getLogger("animica_studio.agent")

# Hard cap on tool/model iterations per user turn (prevents runaway loops).
MAX_TURN_ITERATIONS = 12
# Max automatic re-run attempts in the autonomous build/verify/fix loop.
MAX_FIX_ATTEMPTS = 3
# Per tool-result message cap (chars) when feeding results back to the model.
_MAX_TOOL_MSG_CHARS = 4_000
# Cumulative transcript budget (chars) for the in-turn message list; oldest
# tool/assistant messages are pruned beyond this so small-model context windows
# don't overflow mid-build.
_MAX_TRANSCRIPT_CHARS = 48_000
# Captured verify-command output fed back on failure.
_MAX_VERIFY_OUTPUT_CHARS = 4_000

# Tool names that mutate the project (used to decide whether to auto-verify).
_EDIT_TOOLS = SOFT_WRITE_TOOLS | {"delete_file"}


# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #
class _AgentWorker(QObject):
    """Runs one user turn off the GUI thread, emitting progress via the engine."""

    finished = Signal()

    def __init__(self, engine: "AgentEngine", user_text: str) -> None:
        super().__init__()
        self._engine = engine
        self._user_text = user_text

    def run(self) -> None:
        try:
            self._engine._run_turn(self._user_text)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Agent turn failed")
            self._engine.errorOccurred.emit(redact(str(exc), self._engine._api_key()))
        finally:
            self._engine._set_busy(False)
            self.finished.emit()


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
class AgentEngine(QObject):
    """Orchestrates model calls, planning, and tool execution for the UI."""

    # --- Signals (exact contract names) ---
    assistantChunk = Signal(str)
    assistantMessage = Signal(str)
    toolCallStarted = Signal(dict)
    toolCallFinished = Signal(dict)
    approvalRequested = Signal(str, object)  # (action, callback: Callable[[bool], None])
    planReady = Signal(list)
    statusChanged = Signal(str)
    errorOccurred = Signal(str)
    diffProposed = Signal(list)
    busyChanged = Signal(bool)

    def __init__(
        self,
        settings: Settings,
        db: Optional["Database"] = None,
        *,
        project_service: Optional["ProjectService"] = None,
        tool_manager: Optional[ToolManager] = None,
        client: Optional["AnimicaClient"] = None,
        git_service: Optional["GitService"] = None,
        command_runner: Optional["CommandRunner"] = None,
        indexer: Optional["Indexer"] = None,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._db = db
        self._project_service = project_service
        self._git_service = git_service
        self._command_runner = command_runner
        self._indexer = indexer
        self._client = client

        self._session_id: Optional[str] = None
        self._project_id: Optional[str] = None
        self._busy = False
        self._cancel_flag = False

        self._thread: Optional[QThread] = None
        self._worker: Optional[_AgentWorker] = None

        # Approval synchronization (worker blocks; GUI thread answers).
        self._approval_event = threading.Event()
        self._approval_result = False
        self._approval_lock = threading.Lock()

        self._tool_manager = tool_manager or self._build_tool_manager()
        self._planner = Planner(self._ensure_client())
        self._context_builder = ContextBuilder(
            project_service=self._project_service,
            indexer=self._indexer,
            db=self._db,
            secrets=(self._api_key(),),
        )

    # ------------------------------------------------------------------ #
    # Public API (UI calls these)
    # ------------------------------------------------------------------ #
    @property
    def is_busy(self) -> bool:
        return self._busy

    def send_user_message(self, text: str) -> None:
        """Kick off a user turn on a worker thread (non-blocking)."""
        text = (text or "").strip()
        if not text:
            return
        if self._busy:
            self.errorOccurred.emit("Agent is busy; stop the current run first.")
            return
        self._cancel_flag = False
        self._set_busy(True)

        thread = QThread()
        worker = _AgentWorker(self, text)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    def set_project(self, path: str) -> None:
        """Open/attach a project and rebuild the tool context."""
        if self._project_service is not None:
            try:
                project = self._project_service.open_project(path)
                self._project_id = getattr(project, "id", None)
            except Exception as exc:
                self.errorOccurred.emit(f"Could not open project: {exc}")
                return
        elif self._db is not None:
            proj = self._db.get_project_by_path(path)
            self._project_id = getattr(proj, "id", None) if proj else None
        self._tool_manager = self._build_tool_manager()
        self._context_builder.project_service = self._project_service
        self._context_builder.indexer = self._indexer
        if self._command_runner is not None and self._project_service is not None:
            root = getattr(self._project_service, "root", None)
            if root is not None:
                try:
                    self._command_runner.set_cwd(str(root))
                except Exception:
                    pass
        self.statusChanged.emit(f"Project set: {path}")

    def set_session(self, session_id: str) -> None:
        self._session_id = session_id

    def set_autonomy(self, level: AutonomyLevel) -> None:
        self._settings.autonomy_level = AutonomyLevel.from_value(level)
        self.statusChanged.emit(f"Autonomy: {self._settings.autonomy_level.label}")

    def set_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None  # force rebuild against new endpoint/key
        self._planner = Planner(self._ensure_client())
        self._context_builder.secrets = (self._api_key(),)

    def stop(self) -> None:
        """Request cancellation of the current run."""
        self._cancel_flag = True
        # Unblock any pending approval wait as a denial.
        self._approval_result = False
        self._approval_event.set()
        self.statusChanged.emit("Stopping…")

    # ------------------------------------------------------------------ #
    # Turn execution (runs on the worker thread)
    # ------------------------------------------------------------------ #
    def _run_turn(self, user_text: str) -> None:
        if not self._session_id and self._db is not None:
            # Auto-create a session so messages/tool calls persist.
            try:
                session = self._db.create_session(project_id=self._project_id)
                self._session_id = session.id
            except Exception:
                self._session_id = None

        self._persist_message(MessageRole.USER, user_text)

        client = self._ensure_client()
        if client is None:
            self.errorOccurred.emit(
                "No inference client is configured. Check the endpoint in Settings."
            )
            return

        autonomy = self._settings.autonomy_level
        tools_enabled = autonomy.allows_tools

        self.statusChanged.emit("Gathering context…")
        history = self._load_history()
        context = self._context_builder.build(
            user_request=user_text,
            history=history,
            project_id=self._project_id,
        )

        # Optional planning step for large requests — and feed the plan BACK into
        # the conversation so it actually guides the implementation (not just UI).
        plan_message: Optional[str] = None
        if tools_enabled and self._planner.needs_plan(user_text) and not self._cancelled():
            self.statusChanged.emit("Planning…")
            plan = self._planner.make_plan(
                system_prompt=self._settings.system_prompt,
                context=context,
                user_request=user_text,
                model=self._settings.model,
                cancel=self._cancelled,
            )
            if plan.steps:
                self.planReady.emit(plan.steps_as_dicts())
                plan_message = prompts.build_plan_message(
                    plan.summary, plan.steps, plan.files_to_change
                )

        if self._cancelled():
            self.statusChanged.emit("Cancelled.")
            return

        tool_schemas = self._tool_manager.list_tools() if tools_enabled else None
        system_prompt = prompts.build_system_prompt(
            tools=tool_schemas, base_prompt=self._settings.system_prompt,
            include_tool_protocol=tools_enabled,
        )

        # Build the model message list.
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        if context.strip():
            messages.append(
                {"role": "system", "content": prompts.build_context_message(context)}
            )
        if plan_message:
            messages.append({"role": "system", "content": plan_message})
        for m in history:
            messages.append(m.to_openai())

        executor = self._build_executor(autonomy)

        # Iterative model → tool → verify → fix loop.
        final_text = ""
        fix_attempts = 0
        made_edits = False
        verified_ok = False
        for iteration in range(MAX_TURN_ITERATIONS):
            if self._cancelled():
                self.statusChanged.emit("Cancelled.")
                break
            self.statusChanged.emit(
                "Thinking…" if iteration == 0 else f"Continuing (step {iteration + 1})…"
            )
            result = self._stream_model(client, messages, tool_schemas if tools_enabled else None)
            if self._cancelled():
                break

            assistant_text = result.text
            native_calls = result.tool_calls
            clean_text = prompts.strip_tool_call_fences(assistant_text)
            if clean_text:
                # Persist the user-facing prose (without protocol JSON).
                self._persist_message(MessageRole.ASSISTANT, clean_text)
                final_text = clean_text

            has_calls = bool(native_calls) or (
                tools_enabled and ToolExecutor.has_tool_calls(assistant_text)
            )

            if not tools_enabled or not has_calls:
                # The model thinks it's done. Before accepting, run an autonomous
                # build/verify gate (high autonomy + edits made this turn).
                if self._should_verify(autonomy, made_edits, verified_ok, fix_attempts):
                    ok, fed_back = self._auto_verify(messages, fix_attempts)
                    if ok:
                        verified_ok = True
                        break
                    if fed_back:
                        fix_attempts += 1
                        messages = self._enforce_message_budget(messages)
                        continue  # let the model fix the build failure
                break

            # Append the assistant turn (with native tool_calls when present so the
            # tool results map back correctly) and execute the calls.
            messages.append(self._assistant_message(assistant_text, native_calls))
            exec_result = executor.execute(assistant_text, native_tool_calls=native_calls)

            if exec_result.diffs:
                self.diffProposed.emit([d.to_dict() for d in exec_result.diffs])
            if any(tc.name in _EDIT_TOOLS and tc.status.value == "ok" for tc in exec_result.tool_calls):
                made_edits = True
                verified_ok = False  # new edits invalidate a prior green build

            if exec_result.cancelled or self._cancelled():
                break
            if not exec_result.any_executed:
                break

            # Feed (capped) tool results back to the model and persist them.
            for tmsg in exec_result.tool_messages:
                capped = self._cap_tool_message(tmsg)
                messages.append(capped)
                self._persist_tool_message(capped)

            messages = self._enforce_message_budget(messages)
        else:
            self.statusChanged.emit("Reached iteration limit.")
            final_text = self._summarize_after_tools(client, messages) or final_text

        final_text = prompts.strip_tool_call_fences(final_text)
        self.assistantMessage.emit(final_text or "")
        self.statusChanged.emit("Ready" if not self._cancelled() else "Cancelled.")

    # ------------------------------------------------------------------ #
    def _should_verify(
        self,
        autonomy: AutonomyLevel,
        made_edits: bool,
        verified_ok: bool,
        fix_attempts: int,
    ) -> bool:
        """Whether the engine should auto-run a build/verify gate before finishing."""
        return (
            autonomy.auto_apply
            and made_edits
            and not verified_ok
            and fix_attempts < MAX_FIX_ATTEMPTS
            and self._command_runner is not None
            and self._project_service is not None
            and not self._cancelled()
        )

    def _auto_verify(
        self, messages: list[dict[str, Any]], fix_attempts: int
    ) -> tuple[bool, bool]:
        """Run the project's verify command; on failure feed the error back.

        Returns ``(passed, fed_back)``: ``passed`` is True when the build/test
        command exits 0; ``fed_back`` is True when a failure was appended to the
        conversation so the model can fix it and we should keep iterating.
        """
        ps = self._project_service
        runner = self._command_runner
        try:
            cmd = ps.suggest_verify_command() if ps is not None else None
        except Exception:
            cmd = None
        if not cmd or runner is None:
            return False, False
        try:
            if runner.is_risky(cmd):  # don't auto-run anything risky unattended
                return False, False
        except Exception:
            pass
        self.statusChanged.emit(f"Verifying build: {cmd}")
        try:
            code, output = runner.run_blocking(cmd, timeout=300.0)
        except Exception as exc:
            logger.debug("auto-verify failed to run: %s", exc)
            return False, False
        if code == 0:
            self.statusChanged.emit("Build verified ✓")
            return True, False
        # Feed the failure back so the next iteration fixes it.
        self.statusChanged.emit(f"Build failed (exit {code}); fixing…")
        capped = self._cap_text(output, _MAX_VERIFY_OUTPUT_CHARS)
        messages.append(
            {
                "role": "user",
                "content": prompts.build_error_fix_message(cmd, code, capped),
            }
        )
        return False, True

    def _summarize_after_tools(self, client, messages: list[dict[str, Any]]) -> str:
        """Ask the model for a closing summary after the tool loop ends."""
        if self._cancelled() or client is None:
            return ""
        try:
            messages = self._enforce_message_budget(messages)
            messages.append(
                {
                    "role": "user",
                    "content": "Summarize what you did and any next steps. Do not call tools.",
                }
            )
            return self._stream_model(client, messages, None).text
        except Exception:
            return ""

    def _stream_model(self, client, messages: list[dict[str, Any]], tools=None):
        """Call the model (streaming to the UI); returns a ChatResult.

        Streaming hides tool-call protocol JSON from the live chat: the visible
        delta stops growing once a ``tool_call`` fence begins, while the full raw
        text (used for parsing) is still returned.
        """
        streaming = bool(self._settings.streaming)
        raw_buf: list[str] = []
        emitted = 0

        def on_chunk(delta: str) -> None:
            nonlocal emitted
            if not delta:
                return
            raw_buf.append(delta)
            visible = prompts.strip_tool_call_fences("".join(raw_buf))
            # Hold back a small tail so a partially-streamed fence never leaks.
            safe_len = max(0, len(visible) - 12)
            if safe_len > emitted:
                self.assistantChunk.emit(visible[emitted:safe_len])
                emitted = safe_len

        try:
            return client.chat_full(
                messages,
                stream=streaming,
                on_chunk=on_chunk if streaming else None,
                tools=tools,
                temperature=self._settings.temperature,
                max_tokens=self._settings.max_tokens,
                model=self._settings.model,
                cancel=self._cancelled,
            )
        except Exception as exc:
            from ..services.animica_client import ChatResult

            msg = redact(f"Model request failed: {exc}", self._api_key())
            self.errorOccurred.emit(msg)
            return ChatResult(text="")

    # ------------------------------------------------------------------ #
    # Conversation-shaping helpers (native tool calls + context budget)
    # ------------------------------------------------------------------ #
    def _assistant_message(
        self, text: str, native_calls: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Build the assistant message to append to the model conversation."""
        if not native_calls:
            return {"role": "assistant", "content": text}
        tool_calls: list[dict[str, Any]] = []
        for c in native_calls:
            args = c.get("arguments")
            if not isinstance(args, str):
                try:
                    args = json.dumps(args if args is not None else {})
                except (TypeError, ValueError):
                    args = "{}"
            tool_calls.append(
                {
                    "id": c.get("id") or new_id(),
                    "type": "function",
                    "function": {"name": c.get("name", ""), "arguments": args},
                }
            )
        return {"role": "assistant", "content": text or None, "tool_calls": tool_calls}

    @staticmethod
    def _cap_text(text: str, limit: int) -> str:
        text = text or ""
        if len(text) <= limit:
            return text
        head = text[: (limit * 2) // 3]
        tail = text[-(limit // 3):]
        return f"{head}\n… [truncated {len(text) - limit} chars] …\n{tail}"

    def _cap_tool_message(self, tmsg: dict[str, Any]) -> dict[str, Any]:
        content = str(tmsg.get("content", ""))
        if len(content) <= _MAX_TOOL_MSG_CHARS:
            return tmsg
        out = dict(tmsg)
        out["content"] = self._cap_text(content, _MAX_TOOL_MSG_CHARS)
        return out

    def _enforce_message_budget(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Prune oldest non-system messages so the transcript stays in budget."""
        total = sum(len(str(m.get("content", "") or "")) for m in messages)
        if total <= _MAX_TRANSCRIPT_CHARS:
            return messages
        lead: list[dict[str, Any]] = []
        rest: list[dict[str, Any]] = []
        for m in messages:
            if not rest and m.get("role") == "system":
                lead.append(m)
            else:
                rest.append(m)
        budget = _MAX_TRANSCRIPT_CHARS - sum(
            len(str(m.get("content", "") or "")) for m in lead
        )
        kept: list[dict[str, Any]] = []
        running = 0
        for m in reversed(rest):
            c = len(str(m.get("content", "") or ""))
            if running + c > budget and kept:
                break
            kept.append(m)
            running += c
        kept.reverse()
        return lead + kept

    # ------------------------------------------------------------------ #
    # Executor / approval wiring
    # ------------------------------------------------------------------ #
    def _build_executor(self, autonomy: AutonomyLevel) -> ToolExecutor:
        return ToolExecutor(
            self._tool_manager,
            autonomy=autonomy,
            request_approval=self._blocking_approval,
            request_diff_approval=self._blocking_diff_approval,
            on_tool_started=self._on_tool_started,
            on_tool_finished=self._on_tool_finished,
            db=self._db,
            session_id=self._session_id,
            cancel=self._cancelled,
            project_id=self._project_id,
        )

    def _blocking_approval(self, action: str, category: str) -> bool:
        """Emit approvalRequested and block the worker until the UI answers."""
        if self._cancelled():
            return False
        # Persisted always-allow short-circuit.
        if self._db is not None:
            try:
                if self._db.find_always_allow(category, self._project_id):
                    return True
            except Exception:
                pass

        with self._approval_lock:
            self._approval_event.clear()
            self._approval_result = False

        def callback(allowed: bool, *, always: bool = False) -> None:
            # Persist an always-allow if requested.
            if always and allowed and self._db is not None:
                try:
                    from ..models import ApprovalDecision, CommandApproval

                    self._db.add_command_approval(
                        CommandApproval(
                            project_id=self._project_id,
                            command=action,
                            category=category,
                            decision=ApprovalDecision.ALWAYS_ALLOW,
                            always=True,
                        )
                    )
                except Exception:
                    pass
            self._approval_result = bool(allowed)
            self._approval_event.set()

        self.approvalRequested.emit(f"{action}  [{category}]", callback)
        # Block the worker thread (NOT the GUI) until the UI responds.
        self._approval_event.wait()
        return self._approval_result

    def _blocking_diff_approval(self, diffs: list[FileDiff]) -> bool:
        """Propose diffs to the UI and block until accept/reject.

        Under SUGGEST/APPLY_WITH_APPROVAL the engine emits the diffs and waits for
        an approval callback (reusing the approval channel). The UI's diff viewer
        invokes the same callback with True (accept) / False (reject).
        """
        if self._cancelled():
            return False
        self.diffProposed.emit([d.to_dict() for d in diffs])
        paths = ", ".join(d.path for d in diffs) or "changes"
        return self._blocking_approval(f"apply changes to {paths}", "edit")

    # ------------------------------------------------------------------ #
    # Tool call event relays
    # ------------------------------------------------------------------ #
    def _on_tool_started(self, tc: ToolCall) -> None:
        self.statusChanged.emit(f"Running tool: {tc.name}")
        self.toolCallStarted.emit(tc.to_dict())

    def _on_tool_finished(self, tc: ToolCall) -> None:
        self.toolCallFinished.emit(tc.to_dict())

    # ------------------------------------------------------------------ #
    # Persistence helpers
    # ------------------------------------------------------------------ #
    def _persist_message(self, role: MessageRole, content: str) -> None:
        if self._db is None or not self._session_id or not content:
            return
        try:
            self._db.add_message(
                Message(session_id=self._session_id, role=role, content=content)
            )
        except Exception:
            pass

    def _persist_tool_message(self, tmsg: dict[str, Any]) -> None:
        if self._db is None or not self._session_id:
            return
        try:
            self._db.add_message(
                Message(
                    session_id=self._session_id,
                    role=MessageRole.TOOL,
                    content=str(tmsg.get("content", "")),
                    name=tmsg.get("name"),
                    tool_call_id=tmsg.get("tool_call_id"),
                )
            )
        except Exception:
            pass

    def _load_history(self) -> list[Message]:
        if self._db is None or not self._session_id:
            return []
        try:
            msgs = self._db.list_messages(self._session_id, limit=200)
            # Drop the just-added trailing user message duplication risk: the
            # user message was persisted above and will be the last USER entry;
            # we DO want it included, so return as-is.
            return msgs
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # Building collaborators
    # ------------------------------------------------------------------ #
    def _build_tool_manager(self) -> ToolManager:
        ctx = ToolContext(
            project_service=self._project_service,
            git_service=self._git_service,
            command_runner=self._command_runner,
            indexer=self._indexer,
            templates_service=self._ensure_templates_service(),
            db=self._db,
            session_id=self._session_id,
            approve=self._blocking_approval,
        )
        return ToolManager(ctx)

    def _ensure_templates_service(self):
        """Lazily build a TemplatesService for the scaffold_project tool."""
        svc = getattr(self, "_templates_service", None)
        if svc is None:
            try:
                from ..services.templates_service import TemplatesService

                svc = TemplatesService()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("TemplatesService unavailable: %s", exc)
                svc = None
            self._templates_service = svc
        return svc

    def _ensure_client(self) -> Optional["AnimicaClient"]:
        if self._client is not None:
            return self._client
        try:
            from ..services.animica_client import AnimicaClient

            self._client = AnimicaClient(self._settings)
        except Exception as exc:  # services not built yet / import error
            logger.debug("AnimicaClient unavailable: %s", exc)
            self._client = None
        return self._client

    # ------------------------------------------------------------------ #
    # Small utilities
    # ------------------------------------------------------------------ #
    def _cancelled(self) -> bool:
        return self._cancel_flag

    def _set_busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.busyChanged.emit(value)

    def _api_key(self) -> str:
        return getattr(self._settings, "api_key", "") or ""

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None


__all__ = ["AgentEngine", "MAX_TURN_ITERATIONS", "MAX_FIX_ATTEMPTS"]
