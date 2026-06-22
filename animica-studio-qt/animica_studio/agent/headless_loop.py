"""Headless (Qt-free) agent loop for the Animica Studio sidecar.

This re-hosts the orchestration shape of :class:`animica_studio.agent.engine.AgentEngine`
WITHOUT any Qt dependency, so it can run inside the slim ``anm-studio-ide``
container. It is driven by the FastAPI sidecar (:mod:`animica_studio.agent_http.main`)
which streams the emitted events to the browser as Server-Sent Events.

Design
------
- The whole turn runs on a *worker thread* (started by the sidecar). Progress is
  surfaced through an :class:`EventSink` callback — typically a thread-safe queue
  the SSE response drains.
- Soft-write tools (``write_file`` / ``patch_file`` / ``create_file`` / ...) are
  gated exactly like the Qt engine: the existing :class:`ToolExecutor` computes a
  :class:`FileDiff` preview and calls our ``request_diff_approval`` callback,
  which emits a ``diff`` event with a fresh id and BLOCKS the worker thread on a
  :class:`threading.Event` keyed by that id. The sidecar resumes it via
  :meth:`HeadlessAgentLoop.approve` (wired to ``POST /ena/approve``). On accept
  the write applies (the executor proceeds to call the tool); on reject the write
  is skipped (the executor returns a failure and the model adapts).

The agent core (tool registry, executor, patching, prompts, context builder,
animica_client) is imported from the existing headless modules — Qt is never
imported on this path.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from ..config import Settings, redact
from ..models import AutonomyLevel, FileDiff, Message, MessageRole, new_id
from ..services.animica_client import AnimicaClient
from ..services.fs_project_service import FsProjectService
from . import prompts
from .context import ContextBuilder
from .executor import ToolExecutor
from .tools import ToolContext, ToolManager

logger = logging.getLogger("animica_studio.agent.headless")

# Hard cap on model/tool iterations per turn (mirrors engine.MAX_TURN_ITERATIONS).
MAX_TURN_ITERATIONS = 12
_MAX_TOOL_MSG_CHARS = 4_000
_MAX_TRANSCRIPT_CHARS = 48_000

# An event emitter: ``emit(event_type, data)`` where data is a JSON-serializable
# dict. The sidecar wires this to a thread-safe queue feeding the SSE response.
EventSink = Callable[[str, dict[str, Any]], None]


# --------------------------------------------------------------------------- #
# Project-service adapter
# --------------------------------------------------------------------------- #
class _ProjectServiceAdapter:
    """Adapt :class:`FsProjectService` to the duck-typed ``ProjectService`` API.

    The agent core (:class:`ToolManager` / :class:`ToolExecutor` / context
    builder) expects a project service whose ``read_file`` returns a *string*,
    plus helpers (``is_open``, ``create_file``, ``file_tree``, ``exists``,
    ``summarize_project``, ``detect_project_type``). :class:`FsProjectService`
    returns dicts and exposes a smaller surface, so this thin adapter bridges the
    two without re-implementing the sandbox.
    """

    def __init__(self, fs: FsProjectService) -> None:
        self._fs = fs

    @property
    def root(self):  # noqa: ANN201 - duck-typed Path
        return self._fs.root

    def is_open(self) -> bool:
        return self._fs.root.exists()

    # -- reads --------------------------------------------------------------- #
    def read_file(self, path: str, max_bytes: int = 1_000_000) -> str:
        result = self._fs.read_file(path, max_bytes=max_bytes)
        if result.get("truncated"):
            # Binary / oversized: surface a sentinel rather than silent empty text
            # so the model knows it can't edit this file blindly.
            raise ValueError(f"File is binary or too large to read: {path}")
        return result.get("content", "")

    def exists(self, path: str) -> bool:
        try:
            return self._fs.resolve(path).exists()
        except Exception:
            return False

    def file_tree(self, rel: str = "") -> list[dict[str, Any]]:
        """Return a node list compatible with ToolManager._render_tree / context."""
        root_node = self._fs.tree()
        nodes = root_node.get("children", [])
        if not rel:
            return _to_legacy_tree(nodes)
        # Walk to the requested subdirectory.
        target = rel.strip("/")
        cur = nodes
        for part in target.split("/"):
            if not part:
                continue
            nxt = None
            for n in cur:
                if n.get("name") == part and n.get("type") == "dir":
                    nxt = n.get("children", [])
                    break
            if nxt is None:
                return []
            cur = nxt
        return _to_legacy_tree(cur)

    # -- writes -------------------------------------------------------------- #
    def write_file(self, path: str, content: str) -> None:
        self._fs.write_file(path, content)

    def create_file(self, path: str, content: str = "") -> None:
        if self.exists(path):
            raise ValueError(f"File already exists: {path}")
        self._fs.write_file(path, content)

    def delete_file(self, path: str) -> None:
        target = self._fs.resolve(path)
        if target.is_dir():
            raise IsADirectoryError(f"Is a directory: {path}")
        target.unlink(missing_ok=True)

    def rename_file(self, src: str, dst: str) -> None:
        s = self._fs.resolve(src)
        d = self._fs.resolve(dst)
        d.parent.mkdir(parents=True, exist_ok=True)
        s.replace(d)

    # -- introspection ------------------------------------------------------- #
    def detect_project_type(self) -> str:
        root = self._fs.root
        checks = (
            ("package.json", "node"),
            ("pyproject.toml", "python"),
            ("requirements.txt", "python"),
            ("Cargo.toml", "rust"),
            ("go.mod", "go"),
            ("pom.xml", "java"),
        )
        for marker, kind in checks:
            if (root / marker).exists():
                return kind
        return "unknown"

    def summarize_project(self) -> str:
        kind = self.detect_project_type()
        try:
            top = sorted(
                p.name + ("/" if p.is_dir() else "")
                for p in self._fs.root.iterdir()
                if not p.name.startswith(".")
            )[:40]
        except OSError:
            top = []
        return f"Project type: {kind}\nTop-level entries: {', '.join(top) or '(empty)'}"


def _to_legacy_tree(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert FsProjectService node shape (type:dir/file) into the legacy shape
    (is_dir) the agent's tree renderers expect."""
    out: list[dict[str, Any]] = []
    for n in nodes:
        is_dir = n.get("type") == "dir"
        node: dict[str, Any] = {"name": n.get("name", "?"), "is_dir": is_dir}
        if is_dir and n.get("children"):
            node["children"] = _to_legacy_tree(n["children"])
        out.append(node)
    return out


# --------------------------------------------------------------------------- #
# Settings from environment
# --------------------------------------------------------------------------- #
def settings_from_env(env: dict[str, str]) -> Settings:
    """Build :class:`Settings` from the ``ANIMICA_ENA_*`` environment."""
    base = (env.get("ANIMICA_ENA_BASE") or "https://pool.animica.org/v1").rstrip("/")
    key = env.get("ANIMICA_ENA_KEY") or ""
    model = env.get("ANIMICA_ENA_MODEL") or "anm-code-7b"
    return Settings(
        endpoint=base,
        api_key=key,
        model=model,
        streaming=True,
        # The sidecar acts on behalf of the connected user; apply edits after the
        # browser approves each diff (which we surface ourselves), so the executor
        # itself runs in SUGGEST mode (diff-then-apply gating).
        autonomy_level=AutonomyLevel.SUGGEST,
    )


# --------------------------------------------------------------------------- #
# The headless loop
# --------------------------------------------------------------------------- #
class HeadlessAgentLoop:
    """One ENA agent turn, headless, emitting SSE-shaped events via a sink."""

    def __init__(
        self,
        *,
        fs: FsProjectService,
        settings: Settings,
        emit: EventSink,
        client: Optional[AnimicaClient] = None,
    ) -> None:
        self._fs = fs
        self._ps = _ProjectServiceAdapter(fs)
        self._settings = settings
        self._emit = emit
        self._client = client or AnimicaClient(settings)

        # Approval synchronization: each pending diff blocks on its own Event,
        # keyed by the diff id, until approve(id, accept) sets the result.
        self._pending: dict[str, threading.Event] = {}
        self._results: dict[str, bool] = {}
        self._lock = threading.Lock()
        self._cancelled = False
        self._summary: Optional[str] = None
        self._done_emitted = False

    # -- public: approval entry (called by the sidecar /ena/approve) -------- #
    def approve(self, diff_id: str, accept: bool) -> bool:
        """Resolve a blocked diff approval. Returns False if the id is unknown."""
        with self._lock:
            event = self._pending.get(diff_id)
            if event is None:
                return False
            self._results[diff_id] = bool(accept)
            event.set()
            return True

    def cancel(self) -> None:
        self._cancelled = True
        # Unblock any pending approvals as denials.
        with self._lock:
            for diff_id, event in self._pending.items():
                self._results.setdefault(diff_id, False)
                event.set()

    # -- the turn ----------------------------------------------------------- #
    def run(self, message: str, history: Optional[list[dict[str, Any]]] = None) -> None:
        """Run one user turn to completion, emitting events through the sink."""
        try:
            self._run_turn(message, history or [])
        except Exception as exc:  # defensive: never crash the worker thread
            logger.exception("ENA headless turn failed")
            self._emit("error", {"message": self._redact(f"agent error: {exc}")})
        finally:
            self._emit_done()

    def _emit_done(self) -> None:
        if self._done_emitted:
            return
        self._done_emitted = True
        payload: dict[str, Any] = {}
        if self._summary:
            payload["summary"] = self._summary
        self._emit("done", payload)

    # ---------------------------------------------------------------------- #
    def _run_turn(self, message: str, history: list[dict[str, Any]]) -> None:
        # Guard: no API key configured -> emit a single clean error and stop.
        if not (self._settings.api_key or "").strip():
            self._emit(
                "error",
                {"message": "ENA inference is not configured — an API key is required"},
            )
            return

        self._emit("status", {"phase": "context", "message": "Gathering context…"})

        tool_manager = ToolManager(
            ToolContext(project_service=self._ps, git_service=None)
        )
        tool_schemas = tool_manager.list_tools()
        system_prompt = prompts.build_system_prompt(
            tools=tool_schemas, base_prompt=self._settings.system_prompt,
            include_tool_protocol=True,
        )

        context_builder = ContextBuilder(
            project_service=self._ps, secrets=(self._settings.api_key,)
        )
        hist_msgs = _history_to_messages(history)
        context = context_builder.build(user_request=message, history=hist_msgs)

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        if context.strip():
            messages.append(
                {"role": "system", "content": prompts.build_context_message(context)}
            )
        for m in history:
            role = str(m.get("role", "")).strip()
            content = str(m.get("content", "") or "")
            if role in ("user", "assistant", "system") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})

        executor = ToolExecutor(
            tool_manager,
            autonomy=AutonomyLevel.SUGGEST,
            request_diff_approval=self._blocking_diff_approval,
            on_tool_started=self._on_tool_started,
            on_tool_finished=self._on_tool_finished,
            cancel=lambda: self._cancelled,
        )

        final_text = ""
        for iteration in range(MAX_TURN_ITERATIONS):
            if self._cancelled:
                self._emit("status", {"phase": "cancelled"})
                return
            self._emit(
                "status",
                {
                    "phase": "thinking",
                    "message": "Thinking…" if iteration == 0
                    else f"Continuing (step {iteration + 1})…",
                },
            )
            result = self._stream_model(messages, tool_schemas)
            if self._cancelled:
                return

            assistant_text = result.text
            native_calls = result.tool_calls
            clean_text = prompts.strip_tool_call_fences(assistant_text)
            if clean_text:
                final_text = clean_text

            has_calls = bool(native_calls) or ToolExecutor.has_tool_calls(assistant_text)
            if not has_calls:
                break

            messages.append(self._assistant_message(assistant_text, native_calls))
            exec_result = executor.execute(assistant_text, native_tool_calls=native_calls)

            if exec_result.cancelled or self._cancelled:
                break
            if not exec_result.any_executed:
                break

            for tmsg in exec_result.tool_messages:
                messages.append(self._cap_tool_message(tmsg))
            messages = self._enforce_budget(messages)
        else:
            self._emit("status", {"phase": "limit", "message": "Reached iteration limit."})

        final_text = prompts.strip_tool_call_fences(final_text)
        self._summary = final_text or None

    # -- model streaming ---------------------------------------------------- #
    def _stream_model(self, messages: list[dict[str, Any]], tools):
        raw: list[str] = []
        emitted = 0

        def on_chunk(delta: str) -> None:
            nonlocal emitted
            if not delta:
                return
            raw.append(delta)
            visible = prompts.strip_tool_call_fences("".join(raw))
            # Hold back a small tail so a partially-streamed fence never leaks.
            safe = max(0, len(visible) - 12)
            if safe > emitted:
                self._emit("token", {"delta": visible[emitted:safe]})
                emitted = safe

        try:
            return self._client.chat_full(
                messages,
                stream=True,
                on_chunk=on_chunk,
                tools=tools,
                temperature=self._settings.temperature,
                max_tokens=self._settings.max_tokens,
                model=self._settings.model,
                cancel=lambda: self._cancelled,
            )
        except Exception as exc:
            from ..services.animica_client import ChatResult

            self._emit("error", {"message": self._redact(f"Model request failed: {exc}")})
            return ChatResult(text="")

    # -- soft-write approval (blocks the worker) --------------------------- #
    def _blocking_diff_approval(self, diffs: list[FileDiff]) -> bool:
        if self._cancelled:
            return False
        diff_id = new_id()
        event = threading.Event()
        with self._lock:
            self._pending[diff_id] = event
        payload = {
            "id": diff_id,
            "files": [
                {
                    "path": d.new_path or d.path,
                    "old_text": d.old_text or "",
                    "new_text": d.new_text or "",
                }
                for d in diffs
            ],
        }
        self._emit("diff", payload)
        self._emit("status", {"phase": "awaiting_approval", "message": "Review the proposed change."})
        # Block the worker thread until the browser approves/rejects (or cancel).
        event.wait()
        with self._lock:
            accepted = self._results.pop(diff_id, False)
            self._pending.pop(diff_id, None)
        return bool(accepted) and not self._cancelled

    # -- tool lifecycle relays --------------------------------------------- #
    def _on_tool_started(self, tc) -> None:
        self._emit("tool", {"name": tc.name, "status": "start"})

    def _on_tool_finished(self, tc) -> None:
        status = getattr(tc.status, "value", str(tc.status))
        summary = tc.output if status == "ok" else (tc.error or tc.output or "")
        summary = (summary or "")[:200]
        self._emit("tool", {"name": tc.name, "status": "done", "summary": summary})

    # -- conversation shaping ---------------------------------------------- #
    def _assistant_message(self, text: str, native_calls: list[dict[str, Any]]) -> dict[str, Any]:
        if not native_calls:
            return {"role": "assistant", "content": text}
        import json as _json

        tool_calls: list[dict[str, Any]] = []
        for c in native_calls:
            args = c.get("arguments")
            if not isinstance(args, str):
                try:
                    args = _json.dumps(args if args is not None else {})
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

    def _cap_tool_message(self, tmsg: dict[str, Any]) -> dict[str, Any]:
        content = str(tmsg.get("content", ""))
        if len(content) <= _MAX_TOOL_MSG_CHARS:
            return tmsg
        out = dict(tmsg)
        head = content[: (_MAX_TOOL_MSG_CHARS * 2) // 3]
        tail = content[-(_MAX_TOOL_MSG_CHARS // 3):]
        out["content"] = f"{head}\n… [truncated] …\n{tail}"
        return out

    def _enforce_budget(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
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

    def _redact(self, text: str) -> str:
        return redact(text, self._settings.api_key or "")


def _history_to_messages(history: list[dict[str, Any]]) -> list[Message]:
    out: list[Message] = []
    for m in history or []:
        role = str(m.get("role", "user")).strip()
        content = str(m.get("content", "") or "")
        if not content:
            continue
        try:
            mr = MessageRole.from_value(role)
        except Exception:
            mr = MessageRole.USER
        out.append(Message(role=mr, content=content))
    return out


__all__ = [
    "HeadlessAgentLoop",
    "settings_from_env",
    "MAX_TURN_ITERATIONS",
    "EventSink",
]
