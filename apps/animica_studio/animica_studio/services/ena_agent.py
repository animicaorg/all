"""ENA agent orchestration with tool-call loop and approvals."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Callable, Iterator

from animica_studio.services.ena_client import EnaClient
from animica_studio.services.ena_tools import ToolContext, ToolPolicy, execute_tool


@dataclass
class AgentSession:
    session_id: str
    workspace: Path
    messages: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


class EnaAgent:
    def __init__(self, client: EnaClient, *, rpc_caller=None, logs_provider=None) -> None:
        self._client = client
        self._rpc_caller = rpc_caller
        self._logs_provider = logs_provider

    def run(
        self,
        session: AgentSession,
        prompt: str,
        *,
        tool_policy: ToolPolicy = ToolPolicy.ASK,
        allow_mutations: bool = False,
        allow_exec: bool = False,
        approve_cb: Callable[[dict[str, Any]], bool] | None = None,
        include_context: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        session.messages.append({"role": "user", "content": prompt})
        ctx = ToolContext(workspace=session.workspace, policy=tool_policy, allow_mutations=allow_mutations, allow_exec=allow_exec)
        while True:
            stream = self._client.chat_stream(session.messages, model="default", tools=self._tool_schema(), context=include_context or {})
            consumed_any = False
            for event in stream:
                consumed_any = True
                typ = str(event.get("type", "token"))
                if typ == "tool_call":
                    approval = True
                    if approve_cb:
                        approval = approve_cb(event)
                    if not approval:
                        tool_result = {"ok": False, "error": "User denied tool call"}
                    else:
                        tool_result = execute_tool(
                            str(event.get("name", "")),
                            event.get("args", {}),
                            ctx,
                            rpc_caller=self._rpc_caller,
                            logs_provider=self._logs_provider,
                        )
                    msg = {"role": "tool", "name": event.get("name"), "call_id": event.get("call_id"), "content": json.dumps(tool_result, ensure_ascii=False)}
                    session.messages.append(msg)
                    yield {"type": "tool_result", "event": event, "result": tool_result}
                    break
                if typ == "token":
                    yield event
                elif typ == "done":
                    yield event
                    return
                elif typ == "error":
                    session.diagnostics.append(event)
                    yield event
                    return
                else:
                    yield event
            if not consumed_any:
                return

    def _tool_schema(self) -> list[dict[str, Any]]:
        return [
            {"name": "read_file", "params": {"path": "str", "max_bytes": "int"}},
            {"name": "list_dir", "params": {"path": "str", "depth": "int", "include_hidden": "bool"}},
            {"name": "search_text", "params": {"query": "str", "glob": "str", "max_results": "int"}},
            {"name": "get_git_diff", "params": {}},
            {"name": "rpc_call", "params": {"method": "str", "params": "list"}},
            {"name": "run_cli", "params": {"command": "str"}},
            {"name": "write_file", "params": {"path": "str", "content": "str"}},
            {"name": "apply_patch", "params": {"patch": "str"}},
        ]
