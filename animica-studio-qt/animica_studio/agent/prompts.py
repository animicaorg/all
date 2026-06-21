"""Prompt templates and the JSON tool-call protocol for the Animica Studio Agent.

This module centralizes every piece of natural-language text that is sent to the
model: the system persona, the planning prompt, the tool-format instructions, and
the small helpers that render the tool catalog / protocol description.

The agent uses a **JSON tool-call protocol** (not OpenAI native function-calling)
so it works uniformly across Animica / OpenAI / Ollama-compatible endpoints. The
model is asked to either answer in prose, or to emit one or more tool calls inside
a fenced ```tool_call code block whose body is a JSON object of the form::

    {"tool": "read_file", "arguments": {"path": "src/main.py"}}

Multiple tool calls may be expressed as a JSON array, or as several consecutive
``tool_call`` fences. The executor parses these via :mod:`animica_studio.agent.executor`.
"""
from __future__ import annotations

import json
from typing import Any

from ..config import DEFAULT_SYSTEM_PROMPT

# The canonical agent persona. Sourced from config (single source of truth) so we
# never drift from the spec text.
SYSTEM_PROMPT: str = DEFAULT_SYSTEM_PROMPT

# Fence tag the model must use to emit a machine-readable tool call.
TOOL_CALL_FENCE = "tool_call"
PLAN_FENCE = "plan"

# Description of the JSON tool-call protocol that is appended to the system prompt
# whenever tools are available for the current autonomy level.
TOOL_PROTOCOL_PROMPT = """\
# Tool-use protocol

You can act on the project by calling tools. To call a tool, emit a fenced code \
block tagged `{fence}` whose body is a single JSON object:

```{fence}
{{"tool": "<tool_name>", "arguments": {{ ... }}}}
```

Rules:
- Emit ONLY valid JSON inside a `{fence}` block — no comments, no trailing commas.
- To run several tools in one turn, emit several `{fence}` blocks, OR one block \
whose body is a JSON array of call objects.
- After you call tools, you will receive their results as `tool` messages; then \
continue. Inspect the project (read_file / list_files / search_project) BEFORE \
editing. Make minimal, correct changes.
- For risky tools (write_file, patch_file, create_file, rename_file, delete_file, \
run_command, git_commit, install_dependency) approval may be required; if a call \
is denied, adapt and explain — never retry the same denied action in a loop.
- When the task is complete (or you only need to talk), respond in normal prose \
with NO `{fence}` block. Briefly explain what you changed and why.

Available tools:
{catalog}
"""

# Prompt used to ask the model to produce a structured plan for a large task.
PLAN_PROMPT = """\
You are about to work on a non-trivial request. Before acting, produce a short, \
concrete plan.

Respond with a single fenced code block tagged `{fence}` containing a JSON object:

```{fence}
{{
  "summary": "<one sentence describing the goal>",
  "steps": [
    {{"title": "<imperative step title>", "detail": "<what & which files>"}}
  ],
  "files_to_change": ["<relative/path/one>", "<relative/path/two>"]
}}
```

Keep it to at most 8 steps. Reference real files from the project context when \
possible. Do NOT write code yet — only the plan."""

# Wrapper prompt that frames the assembled project context for the model.
CONTEXT_PREAMBLE = """\
# Project context

The following information about the current project is provided to help you. \
Treat it as read-only background; use tools to read full file contents when you \
need more than the excerpts shown.

{context}
"""

# Instruction injected into the autonomous error-fix loop after a command fails.
ERROR_FIX_PROMPT = """\
The command `{command}` failed with exit code {code}. Output:

```
{output}
```

Diagnose the failure, propose a minimal fix using the file tools, then we will \
re-run the command. If the failure is environmental (missing dependency, network) \
and cannot be fixed by editing files, say so plainly instead of editing."""


def render_tool_catalog(tools: list[dict[str, Any]]) -> str:
    """Render OpenAI-style tool schemas into a compact human/LLM-readable catalog."""
    lines: list[str] = []
    for t in tools:
        fn = t.get("function", t) if isinstance(t, dict) else {}
        name = fn.get("name", "?")
        desc = (fn.get("description") or "").strip()
        params = fn.get("parameters", {}) or {}
        props = params.get("properties", {}) or {}
        required = set(params.get("required", []) or [])
        arg_parts: list[str] = []
        for pname, pschema in props.items():
            ptype = (pschema or {}).get("type", "any")
            flag = "" if pname in required else "?"
            arg_parts.append(f"{pname}{flag}:{ptype}")
        sig = ", ".join(arg_parts)
        lines.append(f"- {name}({sig}) — {desc}")
    return "\n".join(lines) if lines else "(no tools available)"


def build_system_prompt(
    *,
    tools: list[dict[str, Any]] | None = None,
    base_prompt: str | None = None,
    include_tool_protocol: bool = True,
) -> str:
    """Compose the full system prompt: persona + (optionally) the tool protocol."""
    prompt = (base_prompt or SYSTEM_PROMPT).strip()
    if include_tool_protocol and tools:
        catalog = render_tool_catalog(tools)
        protocol = TOOL_PROTOCOL_PROMPT.format(fence=TOOL_CALL_FENCE, catalog=catalog)
        prompt = f"{prompt}\n\n{protocol}"
    return prompt


def build_plan_prompt() -> str:
    """The instruction asking the model to emit a structured plan."""
    return PLAN_PROMPT.format(fence=PLAN_FENCE)


def build_context_message(context: str) -> str:
    """Wrap assembled project context with the standard preamble."""
    return CONTEXT_PREAMBLE.format(context=context.strip())


def build_error_fix_message(command: str, code: int, output: str) -> str:
    """Frame a failed-command result for the autonomous fix loop."""
    return ERROR_FIX_PROMPT.format(command=command, code=code, output=output.strip())


def build_plan_message(summary: str, steps: list[Any], files: list[str] | None = None) -> str:
    """Render a parsed plan as a guidance message injected into the conversation.

    The plan was previously emitted only to the UI and discarded; feeding it back
    to the model keeps the implementation aligned with the agreed approach.
    """
    lines = ["# Approved plan", ""]
    if summary:
        lines.append(summary)
        lines.append("")
    for i, step in enumerate(steps or [], start=1):
        title = getattr(step, "title", None) or (step.get("title") if isinstance(step, dict) else "")
        detail = getattr(step, "detail", None) or (step.get("detail") if isinstance(step, dict) else "")
        if detail:
            lines.append(f"{i}. {title} — {detail}")
        elif title:
            lines.append(f"{i}. {title}")
    if files:
        lines.append("")
        lines.append("Files likely involved: " + ", ".join(files))
    lines.append("")
    lines.append(
        "Execute this plan step by step using tools. Inspect files before editing, "
        "then verify the result actually runs before you finish."
    )
    return "\n".join(lines)


def strip_tool_call_fences(text: str) -> str:
    """Remove ``tool_call`` / ``plan`` protocol fences from user-facing text.

    The model's machine-readable tool calls must never be shown to the user as
    raw JSON code blocks. Tool activity is surfaced separately via the tool-call
    chip stream, so we strip these fences from any text rendered in the chat.
    """
    if not text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    fences = (f"```{TOOL_CALL_FENCE}", f"```{PLAN_FENCE}")
    while i < n:
        nxt = -1
        for fence in fences:
            pos = text.find(fence, i)
            if pos != -1 and (nxt == -1 or pos < nxt):
                nxt = pos
        if nxt == -1:
            out.append(text[i:])
            break
        out.append(text[i:nxt])
        close = text.find("```", nxt + 3)
        if close == -1:
            # Unterminated fence (still streaming): drop the remainder.
            break
        i = close + 3
    cleaned = "".join(out)
    # Collapse the blank lines left behind by removed blocks.
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    return cleaned.strip()


def encode_tool_call(name: str, arguments: dict[str, Any]) -> str:
    """Helper to encode a tool call as the model would (for tests / few-shot)."""
    body = json.dumps({"tool": name, "arguments": arguments}, ensure_ascii=False)
    return f"```{TOOL_CALL_FENCE}\n{body}\n```"


__all__ = [
    "SYSTEM_PROMPT",
    "TOOL_CALL_FENCE",
    "PLAN_FENCE",
    "TOOL_PROTOCOL_PROMPT",
    "PLAN_PROMPT",
    "CONTEXT_PREAMBLE",
    "ERROR_FIX_PROMPT",
    "render_tool_catalog",
    "build_system_prompt",
    "build_plan_prompt",
    "build_context_message",
    "build_error_fix_message",
    "build_plan_message",
    "strip_tool_call_fences",
    "encode_tool_call",
]
