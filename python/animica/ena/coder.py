"""
animica.ena.coder
=================

ENA-native coding agent: a tool-using repo loop that runs on the ENA *pool model*
— the community-trained checkpoint served over an OpenAI-compatible endpoint
(``ena pool serve``, pool.animica.org/v1, or the P2P serving layer). It is the
consumption side of the flywheel: usage here pays the pool that trains the model.

The loop is a compact ReAct: the model is asked to reply with a single JSON
action ``{"thought","tool","args"}``; the agent executes the tool inside a
sandboxed working directory and feeds the observation back, until the model calls
``done``. Tools are filesystem- and shell-scoped to ``workdir`` so a task can't
escape its sandbox.

The model is injected as any adapter exposing ``generate(prompt, *, system=...)``
— in production :func:`build_coder` wires ``providers.build_model_adapter`` at an
OpenAI-compatible ``base_url``; tests inject a scripted fake. This keeps the loop
provider-agnostic and unit-testable without a live model.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

from .errors import ENAError
from .models import new_uuid, now_ts

MAX_OBS_CHARS = 4000


class CoderError(ENAError):
    code = "ENA/CODER"


SYSTEM_PROMPT = """\
You are the Animica ENA coding agent. You complete a software task by issuing ONE
tool call at a time. Respond with ONLY a single JSON object, no prose, no markdown
fences:

  {"thought": "<brief reasoning>", "tool": "<name>", "args": {<...>}}

Tools:
  list_dir   {"path": "."}                       -> directory listing
  read_file  {"path": "x.py"}                    -> file contents
  write_file {"path": "x.py", "content": "..."}  -> create/overwrite a file
  run        {"cmd": "pytest -q"}                -> run a shell command (in the workdir)
  done       {"summary": "what you did"}         -> finish

Rules: paths are relative to the working directory and may not escape it. Make the
smallest change that satisfies the task, verify it (e.g. run tests), then call done.
"""


def _safe_path(workdir: Path, rel: str) -> Path:
    p = (workdir / (rel or ".")).resolve()
    if workdir not in p.parents and p != workdir:
        raise CoderError(f"path escapes workdir: {rel}")
    return p


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model reply (tolerates fences/prose)."""
    s = text.strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in reply")
    return json.loads(s[start:end + 1])


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

def _tool_list_dir(workdir: Path, args: dict, runner) -> str:
    d = _safe_path(workdir, args.get("path", "."))
    if not d.is_dir():
        return f"not a directory: {args.get('path')}"
    return "\n".join(sorted(
        p.name + ("/" if p.is_dir() else "") for p in d.iterdir())) or "(empty)"


def _tool_read_file(workdir: Path, args: dict, runner) -> str:
    p = _safe_path(workdir, args.get("path", ""))
    if not p.is_file():
        return f"not a file: {args.get('path')}"
    return p.read_text(encoding="utf-8", errors="replace")


def _tool_write_file(workdir: Path, args: dict, runner) -> str:
    p = _safe_path(workdir, args.get("path", ""))
    p.parent.mkdir(parents=True, exist_ok=True)
    content = args.get("content", "")
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {args.get('path')}"


def _tool_run(workdir: Path, args: dict, runner) -> str:
    cmd = args.get("cmd", "")
    if not cmd:
        return "no cmd"
    return runner(cmd, workdir)


def _default_runner(cmd: str, workdir: Path) -> str:
    try:
        proc = subprocess.run(cmd, shell=True, cwd=str(workdir),
                              capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "command timed out"
    out = (proc.stdout or "") + (("\n--- stderr ---\n" + proc.stderr) if proc.stderr else "")
    return f"exit={proc.returncode}\n{out}".strip()


_TOOLS: dict[str, Callable[[Path, dict, Any], str]] = {
    "list_dir": _tool_list_dir,
    "read_file": _tool_read_file,
    "write_file": _tool_write_file,
    "run": _tool_run,
}


# ---------------------------------------------------------------------------
# agent
# ---------------------------------------------------------------------------

class CoderAgent:
    """A sandboxed, tool-using coding loop on top of an injected model adapter."""

    def __init__(self, model, *, workdir: str | Path, max_steps: int = 24,
                 runner: Optional[Callable[[str, Path], str]] = None,
                 store=None) -> None:
        self.model = model
        self.workdir = Path(workdir).resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.max_steps = int(max_steps)
        self.runner = runner or _default_runner
        self.store = store

    def _ask(self, task: str, history: list[dict[str, Any]]) -> str:
        lines = [f"TASK: {task}", "", "History:"]
        for h in history:
            lines.append(f"  action: {json.dumps({'tool': h['tool'], 'args': h['args']})}")
            lines.append(f"  observation: {h['observation'][:600]}")
        lines.append("\nRespond with the next single JSON action.")
        return self.model.generate("\n".join(lines), system=SYSTEM_PROMPT,
                                   max_tokens=1024, temperature=0.0)

    def run_task(self, task: str) -> dict[str, Any]:
        history: list[dict[str, Any]] = []
        status, summary = "max_steps", ""
        for _ in range(self.max_steps):
            reply = self._ask(task, history)
            try:
                action = _extract_json(reply)
            except ValueError:
                history.append({"tool": "<parse>", "args": {},
                                "observation": "invalid: reply with ONE JSON object only"})
                continue
            tool = str(action.get("tool", "")).strip()
            args = action.get("args") if isinstance(action.get("args"), dict) else {}
            if tool == "done":
                status, summary = "done", str(args.get("summary", ""))
                history.append({"tool": "done", "args": args, "observation": summary})
                break
            fn = _TOOLS.get(tool)
            if fn is None:
                obs = f"unknown tool: {tool!r}; valid: {', '.join([*_TOOLS, 'done'])}"
            else:
                try:
                    obs = fn(self.workdir, args, self.runner)
                except CoderError as exc:
                    obs = f"error: {exc.message}"
                except Exception as exc:  # noqa: BLE001
                    obs = f"error: {exc}"
            history.append({"tool": tool, "args": args, "observation": obs[:MAX_OBS_CHARS]})
        result = {"task_id": "code-" + new_uuid()[:16], "status": status,
                  "summary": summary, "steps": len(history),
                  "transcript": history, "created_at": now_ts()}
        if self.store is not None:
            try:
                self.store.add_trace({"trace_id": result["task_id"],
                                      "session_id": "coder", "created_at": now_ts(),
                                      **result})
            except Exception:  # noqa: BLE001
                pass
        return result


def build_coder(cfg, *, workdir: str | Path, base_url: Optional[str] = None,
                model: Optional[str] = None, api_key_env: Optional[str] = None,
                model_provider: Optional[str] = None, store=None,
                max_steps: int = 24) -> CoderAgent:
    """Build a CoderAgent on an OpenAI-compatible endpoint (the pool's served
    model by default), reusing the ENA provider stack."""
    from .models import ModelProviderConfig
    from .providers import build_model_adapter
    if base_url:
        mcfg = ModelProviderConfig(
            name="ena-pool", provider="openai_compatible", transport="http",
            model=model or "ena-pool", base_url=base_url,
            api_key_env_vars=[api_key_env] if api_key_env else [])
    else:
        mcfg = cfg.model_provider(model_provider)
        if model:
            mcfg.model = model
    adapter = build_model_adapter(mcfg)
    return CoderAgent(adapter, workdir=workdir, store=store, max_steps=max_steps)
