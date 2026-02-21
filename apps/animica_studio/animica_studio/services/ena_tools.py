"""Safe ENA tool implementations with explicit policy gates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import fnmatch
import json
from pathlib import Path
import shlex
import subprocess
from typing import Any


class ToolPolicy(str, Enum):
    ASK = "ask"
    ALLOW_READONLY = "allow_readonly"
    ALLOW_ALL = "allow_all"


@dataclass
class ToolContext:
    workspace: Path
    policy: ToolPolicy = ToolPolicy.ASK
    allow_mutations: bool = False
    allow_exec: bool = False


READONLY_TOOLS = {"read_file", "list_dir", "search_text", "get_git_diff", "get_recent_logs", "rpc_call"}
WRITE_TOOLS = {"write_file", "apply_patch"}
EXEC_TOOLS = {"run_cli"}
READONLY_COMMAND_PREFIXES = ["git status", "git diff", "pytest -q", "python -m pytest -q", "ruff check"]


def check_tool_allowed(name: str, ctx: ToolContext) -> tuple[bool, str]:
    if ctx.policy == ToolPolicy.ALLOW_ALL:
        return True, "allowed"
    if name in READONLY_TOOLS and ctx.policy in (ToolPolicy.ALLOW_READONLY, ToolPolicy.ASK):
        return True, "allowed"
    if name in WRITE_TOOLS and ctx.allow_mutations:
        return True, "allowed"
    if name in EXEC_TOOLS and ctx.allow_exec:
        return True, "allowed"
    return False, f"Tool {name} blocked by policy"


def execute_tool(name: str, args: dict[str, Any], ctx: ToolContext, *, rpc_caller=None, logs_provider=None) -> dict[str, Any]:
    allowed, reason = check_tool_allowed(name, ctx)
    if not allowed:
        return {"ok": False, "error": reason}
    try:
        if name == "read_file":
            return {"ok": True, "result": read_file(ctx.workspace, str(args.get("path", "")), int(args.get("max_bytes", 250_000)))}
        if name == "list_dir":
            return {"ok": True, "result": list_dir(ctx.workspace, str(args.get("path", ".")), int(args.get("depth", 2)), bool(args.get("include_hidden", False)))}
        if name == "search_text":
            return {"ok": True, "result": search_text(ctx.workspace, str(args.get("query", "")), str(args.get("glob", "*")), int(args.get("max_results", 50)))}
        if name == "get_git_diff":
            return {"ok": True, "result": run_git_diff(ctx.workspace)}
        if name == "get_recent_logs":
            return {"ok": True, "result": logs_provider(str(args.get("service", "app"))) if logs_provider else ""}
        if name == "rpc_call":
            if rpc_caller is None:
                return {"ok": False, "error": "rpc_call unavailable"}
            return {"ok": True, "result": rpc_caller(str(args.get("method", "")), args.get("params", []))}
        if name == "run_cli":
            return run_cli(str(args.get("command", "")), ctx)
        if name == "write_file":
            return write_file(ctx.workspace, str(args.get("path", "")), str(args.get("content", "")))
        if name == "apply_patch":
            return apply_patch(ctx.workspace, str(args.get("patch", "")))
        return {"ok": False, "error": f"Unknown tool {name}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": stringify_error(exc)}


def resolve_path(workspace: Path, rel: str) -> Path:
    p = (workspace / rel).resolve()
    if workspace.resolve() not in p.parents and p != workspace.resolve():
        raise ValueError("Path escapes workspace")
    return p


def read_file(workspace: Path, rel: str, max_bytes: int) -> str:
    p = resolve_path(workspace, rel)
    data = p.read_bytes()[:max_bytes]
    return data.decode("utf-8", errors="replace")


def list_dir(workspace: Path, rel: str, depth: int, include_hidden: bool) -> list[dict[str, Any]]:
    base = resolve_path(workspace, rel)
    out: list[dict[str, Any]] = []
    for path in sorted(base.rglob("*")):
        depth_now = len(path.relative_to(base).parts)
        if depth_now > depth:
            continue
        if not include_hidden and any(part.startswith(".") for part in path.parts):
            continue
        out.append({"path": str(path.relative_to(workspace)), "is_dir": path.is_dir()})
    return out


def search_text(workspace: Path, query: str, glob_pat: str, max_results: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if not query.strip():
        return results
    for path in workspace.rglob("*"):
        if not path.is_file() or not fnmatch.fnmatch(path.name, glob_pat):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), start=1):
            if query.lower() in line.lower():
                results.append({"path": str(path.relative_to(workspace)), "line": i, "preview": line[:300]})
                if len(results) >= max_results:
                    return results
    return results


def run_git_diff(workspace: Path) -> str:
    proc = subprocess.run(["git", "diff", "--", "."], cwd=workspace, capture_output=True, text=True, check=False)
    return proc.stdout + ("\n" + proc.stderr if proc.stderr else "")


def run_cli(command: str, ctx: ToolContext) -> dict[str, Any]:
    cmd = shlex.split(command)
    if not cmd:
        return {"ok": False, "error": "Empty command"}
    rendered = " ".join(cmd)
    if not any(rendered.startswith(prefix) for prefix in READONLY_COMMAND_PREFIXES):
        return {"ok": False, "error": f"Command not allowlisted: {rendered}"}
    proc = subprocess.run(cmd, cwd=ctx.workspace, capture_output=True, text=True, check=False)
    return {"ok": True, "result": {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}}


def write_file(workspace: Path, rel: str, content: str) -> dict[str, Any]:
    p = resolve_path(workspace, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"ok": True, "result": str(p)}


def apply_patch(workspace: Path, patch_text: str) -> dict[str, Any]:
    proc = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=workspace, input=patch_text, capture_output=True, text=True, check=False)
    return {"ok": proc.returncode == 0, "result": proc.stdout, "error": proc.stderr if proc.returncode else None}


def stringify_error(exc: Exception) -> str:
    payload = {"type": exc.__class__.__name__, "message": str(exc)}
    return json.dumps(payload, ensure_ascii=False)
