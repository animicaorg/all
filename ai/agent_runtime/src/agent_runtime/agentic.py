"""Agentic chat loop for ``animica chat --agentic``.

Turns the AICF-paid chat REPL into a tool-using agent (Claude Code style).
The remote LLM (Qwen running on a miner via AICF) emits tool calls inside
``[TOOL_CALL]`` blocks; this module parses them, executes the tool with
local permission gating, and feeds the result back as the next assistant
turn. Loops until the model emits a final answer or hits the iteration cap.

Why a custom block format instead of OpenAI/Anthropic function-calling JSON:
the AICF protocol returns plain text from `aicf.streamJob` / `aicf.jobStatus`,
not structured tool-call deltas, so we need an in-band convention. Most
instruct-tuned models follow the format reliably when we describe it in the
system prompt; we tolerate minor variations (whitespace, fenced ``` blocks).

Cost model: each iteration is one paid AICF job. Total cost is reported per
turn so users can see what an agentic task spent. ``--max-iterations`` caps
runaway loops; ``--max-cost`` (in ANIMICA) hard-stops once exceeded.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional


# --------------------------------------------------------------------------- #
# Tool registry                                                               #
# --------------------------------------------------------------------------- #


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict             # JSON-schema-ish, used in system prompt only
    is_safe: bool                # safe = no permission prompt by default
    handler: Callable[..., str]  # returns observable result (string)


def _tool_read_file(path: str, max_lines: int = 200, offset: int = 0) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"ERROR: file does not exist: {path}"
    if not p.is_file():
        return f"ERROR: not a file: {path}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"ERROR: could not read {path}: {exc}"
    lines = text.splitlines()
    end = offset + max_lines
    visible = lines[offset:end]
    body = "\n".join(f"{i + 1:>5}: {ln}" for i, ln in enumerate(visible, start=offset))
    head = f"# {path} ({len(lines)} lines total"
    if offset:
        head += f", showing {offset+1}-{min(end, len(lines))}"
    elif len(lines) > max_lines:
        head += f", showing 1-{max_lines}; ask again with offset={max_lines} for more"
    head += ")\n"
    return head + body


def _tool_list_files(path: str = ".", max_entries: int = 200) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"ERROR: path does not exist: {path}"
    if not p.is_dir():
        return f"ERROR: not a directory: {path}"
    # The old format was `f          15  a.py` — three unlabelled columns. A model
    # asked "how many files are here?" answered "15", because the byte size is the
    # only number on the line and nothing said what it was. Labelling the columns
    # and stating the count outright costs a few tokens and removes the ambiguity.
    entries = []
    files = dirs = 0
    truncated = False
    for child in sorted(p.iterdir()):
        try:
            is_dir = child.is_dir()
            if is_dir:
                dirs += 1
                entries.append(f"dir   {child.name}/")
            else:
                files += 1
                entries.append(f"file  {child.name}  ({child.stat().st_size} bytes)")
        except OSError:
            continue
        if len(entries) >= max_entries:
            truncated = True
            break
    header = (f"# {p} contains {files} file(s) and {dirs} director"
              f"{'y' if dirs == 1 else 'ies'}")
    body = "\n".join(entries) if entries else "(empty)"
    if truncated:
        body += f"\n... (listing truncated at {max_entries} entries)"
    return f"{header}\n{body}"


def _tool_grep(pattern: str, path: str = ".", max_results: int = 100) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"ERROR: path does not exist: {path}"
    try:
        proc = subprocess.run(
            ["grep", "-rn", "--include=*", pattern, str(p)],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return "ERROR: grep is not installed on this system"
    except subprocess.TimeoutExpired:
        return "ERROR: grep timed out after 30s"
    lines = (proc.stdout or "").splitlines()
    if len(lines) > max_results:
        lines = lines[:max_results] + [f"... (truncated at {max_results})"]
    err = (proc.stderr or "").strip()
    body = "\n".join(lines) if lines else "(no matches)"
    return f"# grep -rn {pattern!r} {p}\n{body}" + (f"\n[stderr: {err}]" if err else "")


def _tool_write_file(path: str, content: str) -> str:
    p = Path(path).expanduser().resolve()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except Exception as exc:
        return f"ERROR: could not write {path}: {exc}"
    return f"wrote {len(content)} bytes to {path}"


def _tool_edit_file(path: str, old: str, new: str) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"ERROR: file does not exist: {path}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"ERROR: could not read {path}: {exc}"
    if old not in text:
        return f"ERROR: old_string not found in {path}"
    if text.count(old) > 1:
        return (
            f"ERROR: old_string appears {text.count(old)} times in {path}; "
            "include more surrounding context to make it unique"
        )
    new_text = text.replace(old, new, 1)
    try:
        p.write_text(new_text, encoding="utf-8")
    except Exception as exc:
        return f"ERROR: could not write {path}: {exc}"
    return f"edited {path} ({len(old)} -> {len(new)} bytes)"


def _tool_bash(command: str, timeout: int = 60) -> str:
    try:
        proc = subprocess.run(
            ["bash", "-c", command],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout}s"
    except Exception as exc:
        return f"ERROR: bash failed to launch: {exc}"
    out = proc.stdout or ""
    err = proc.stderr or ""
    # Cap individual outputs so a chatty command doesn't blow the next turn's
    # prompt and a huge AICF payment.
    def _cap(s: str, n: int = 4000) -> str:
        if len(s) <= n:
            return s
        return s[:n] + f"\n... (truncated; {len(s)-n} more bytes)"
    parts = [f"$ {command}", f"exit_code: {proc.returncode}"]
    if out:
        parts.append("stdout:\n" + _cap(out))
    if err:
        parts.append("stderr:\n" + _cap(err))
    return "\n".join(parts)


def _tool_done(message: str = "") -> str:
    return f"DONE: {message}"


# --------------------------------------------------------------------------- #
# Additional filesystem tools                                                 #
# --------------------------------------------------------------------------- #


def _tool_glob(pattern: str, path: str = ".", max_results: int = 200) -> str:
    """Find files matching a glob (supports ** for recursive)."""
    base = Path(path).expanduser().resolve()
    if not base.exists():
        return f"ERROR: path does not exist: {path}"
    if not base.is_dir():
        return f"ERROR: not a directory: {path}"
    try:
        matches = sorted(str(p) for p in base.glob(pattern))
    except Exception as exc:
        return f"ERROR: glob failed: {exc}"
    truncated = ""
    if len(matches) > max_results:
        matches = matches[:max_results]
        truncated = f"\n... (truncated at {max_results})"
    body = "\n".join(matches) if matches else "(no matches)"
    return f"# glob {pattern!r} under {base}\n{body}{truncated}"


def _tool_tree(path: str = ".", max_depth: int = 3, max_entries: int = 200) -> str:
    """Render a directory tree. Skips heavy dirs by default."""
    base = Path(path).expanduser().resolve()
    if not base.exists():
        return f"ERROR: path does not exist: {path}"
    skip = {".git", "__pycache__", "node_modules", ".venv", "venv",
            "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    out: list[str] = [f"{base}"]
    count = 0

    def _walk(p: Path, depth: int, prefix: str) -> None:
        nonlocal count
        if depth > max_depth:
            return
        try:
            children = sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name))
        except OSError:
            return
        for i, c in enumerate(children):
            if c.name in skip:
                continue
            if count >= max_entries:
                out.append(prefix + "... (truncated)")
                return
            count += 1
            last = i == len(children) - 1
            branch = "└── " if last else "├── "
            out.append(prefix + branch + c.name + ("/" if c.is_dir() else ""))
            if c.is_dir():
                _walk(c, depth + 1, prefix + ("    " if last else "│   "))

    _walk(base, 1, "")
    return "\n".join(out)


def _tool_file_stat(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"ERROR: does not exist: {path}"
    try:
        st = p.stat()
    except OSError as exc:
        return f"ERROR: stat failed: {exc}"
    kind = "directory" if p.is_dir() else (
        "file" if p.is_file() else (
            "symlink" if p.is_symlink() else "other"))
    return json.dumps({
        "path": str(p.resolve()),
        "kind": kind,
        "size_bytes": st.st_size,
        "mtime": int(st.st_mtime),
        "mode_octal": oct(st.st_mode & 0o777),
    }, indent=2)


def _tool_append_file(path: str, content: str) -> str:
    p = Path(path).expanduser().resolve()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(content)
    except Exception as exc:
        return f"ERROR: could not append to {path}: {exc}"
    return f"appended {len(content)} bytes to {path}"


def _tool_mkdir(path: str) -> str:
    p = Path(path).expanduser().resolve()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return f"ERROR: mkdir failed: {exc}"
    return f"mkdir -p {p}"


def _tool_delete_file(path: str) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"ERROR: does not exist: {path}"
    try:
        if p.is_dir():
            return (
                f"ERROR: refusing to recursively delete a directory; "
                f"use `bash` with explicit `rm -rf {p}` if you really mean it"
            )
        p.unlink()
    except Exception as exc:
        return f"ERROR: delete failed: {exc}"
    return f"deleted {p}"


def _tool_move_file(src: str, dst: str) -> str:
    sp = Path(src).expanduser().resolve()
    dp = Path(dst).expanduser().resolve()
    if not sp.exists():
        return f"ERROR: source does not exist: {src}"
    try:
        dp.parent.mkdir(parents=True, exist_ok=True)
        sp.rename(dp)
    except Exception as exc:
        return f"ERROR: move failed: {exc}"
    return f"moved {sp} -> {dp}"


def _tool_search_and_replace(
    path: str, old: str, new: str, replace_all: bool = False,
) -> str:
    """Like edit_file but allows replace_all when ``old`` appears multiple times."""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"ERROR: file does not exist: {path}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"ERROR: could not read {path}: {exc}"
    n = text.count(old)
    if n == 0:
        return f"ERROR: old_string not found in {path}"
    if n > 1 and not replace_all:
        return (
            f"ERROR: old_string appears {n} times in {path}; "
            "set replace_all=true to replace every occurrence"
        )
    new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    try:
        p.write_text(new_text, encoding="utf-8")
    except Exception as exc:
        return f"ERROR: could not write {path}: {exc}"
    return f"replaced {n if replace_all else 1} occurrence(s) in {path}"


def _tool_diff_files(a: str, b: str, context: int = 3) -> str:
    """Unified diff between two files."""
    import difflib
    pa = Path(a).expanduser().resolve()
    pb = Path(b).expanduser().resolve()
    for p, lbl in ((pa, a), (pb, b)):
        if not p.exists():
            return f"ERROR: does not exist: {lbl}"
    try:
        la = pa.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        lb = pb.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except Exception as exc:
        return f"ERROR: could not read inputs: {exc}"
    out = "".join(difflib.unified_diff(la, lb, fromfile=str(pa), tofile=str(pb),
                                        n=int(context)))
    return out or "(files are identical)"


def _tool_apply_patch(patch: str) -> str:
    """Apply a unified-diff patch via the system `patch` or `git apply` tool.

    Tries `patch -p0` first; falls back to `git apply -p0`. The patch text
    is fed via stdin so the model can construct it inline without writing
    a temp file first.
    """
    for cmd in (["patch", "-p0", "--no-backup-if-mismatch"], ["git", "apply", "-p0"]):
        try:
            proc = subprocess.run(
                cmd, input=patch, capture_output=True, text=True, timeout=30,
            )
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return f"ERROR: {cmd[0]} timed out"
        if proc.returncode == 0:
            return f"applied patch via {cmd[0]}: {proc.stdout.strip()}"
        # If first tool failed with non-empty stderr, surface it before
        # falling through.
        last_err = (proc.stderr or proc.stdout or "").strip()
        if "command not found" not in last_err.lower():
            return f"ERROR: {cmd[0]} failed (rc={proc.returncode}): {last_err}"
    return "ERROR: neither `patch` nor `git apply` is available"


def _tool_python_eval(code: str, timeout: int = 30) -> str:
    """Run a Python snippet in a subprocess. Output is whatever it prints.

    Safer than `bash` because there's no shell interpolation, but still
    capable of arbitrary IO — gated as non-safe.
    """
    try:
        proc = subprocess.run(
            ["python3", "-c", code],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        return "ERROR: python3 not on PATH"
    except subprocess.TimeoutExpired:
        return f"ERROR: python_eval timed out after {timeout}s"
    parts = [f"exit_code: {proc.returncode}"]
    out = (proc.stdout or "").rstrip()
    err = (proc.stderr or "").rstrip()
    if out:
        parts.append("stdout:\n" + (out[:4000] + (
            f"\n... (+{len(out)-4000} more)" if len(out) > 4000 else "")))
    if err:
        parts.append("stderr:\n" + (err[:4000] + (
            f"\n... (+{len(err)-4000} more)" if len(err) > 4000 else "")))
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Network / web                                                               #
# --------------------------------------------------------------------------- #


def _tool_fetch_url(url: str, max_bytes: int = 32768, timeout: int = 15) -> str:
    """HTTP GET. Returns headers + body (capped). No JS, no auth."""
    try:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(url, headers={
            "User-Agent": "animica-agentic/0.1",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", resp.getcode())
            headers = dict(resp.headers.items())
            raw = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        return f"ERROR: HTTP {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return f"ERROR: URL error: {exc.reason}"
    except Exception as exc:
        return f"ERROR: fetch failed: {exc}"
    truncated = ""
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
        truncated = f"\n... (truncated at {max_bytes} bytes)"
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError:
        body = raw.decode("utf-8", errors="replace")
    interesting_headers = {k: v for k, v in headers.items() if k.lower() in {
        "content-type", "content-length", "etag", "last-modified", "location"
    }}
    head = json.dumps({"status": status, "headers": interesting_headers}, indent=2)
    return f"{head}\n---\n{body}{truncated}"


# --------------------------------------------------------------------------- #
# Animica / blockchain                                                        #
# --------------------------------------------------------------------------- #


def _tool_animica_rpc(method: str, params: Any = None, timeout: int = 30) -> str:
    """JSON-RPC call against the local node (ANIMICA_RPC_URL or default)."""
    try:
        import urllib.request
        rpc_url = (
            os.environ.get("ANIMICA_AGENTIC_RPC_URL")
            or os.environ.get("ANIMICA_RPC_URL")
            or "http://127.0.0.1:8545/rpc"
        )
        body = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": method,
            "params": params if params is not None else {},
        }).encode("utf-8")
        req = urllib.request.Request(
            rpc_url, data=body,
            headers={"content-type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return f"ERROR: rpc call failed: {exc}"
    if "error" in data and data["error"] is not None:
        return json.dumps(data["error"], indent=2)
    return json.dumps(data.get("result"), indent=2, default=str)


def _tool_balance(address: str) -> str:
    raw = _tool_animica_rpc("state.getBalance", [address])
    if raw.startswith("ERROR"):
        return raw
    try:
        nanm = int(json.loads(raw), 16) if raw.startswith('"0x') else int(json.loads(raw))
    except Exception:
        return f"unparseable balance: {raw}"
    return f"{nanm / 1e9:.9f} ANM ({nanm} nanm)"


def _tool_chain_head() -> str:
    return _tool_animica_rpc("chain.getHead", {})


# --------------------------------------------------------------------------- #
# Cognitive scaffolding                                                       #
# --------------------------------------------------------------------------- #


def _tool_think(thought: str) -> str:
    """A no-op tool the model can call to think out loud without side effects.

    Some smaller models reason more reliably when given an explicit "scratchpad"
    step before committing to an action.
    """
    return f"(noted: {thought[:200]}{'...' if len(thought) > 200 else ''})"


# Process-local todo board so the model can build a plan over multiple turns.
_TODO_LIST: list[dict] = []


def _tool_todo(action: str = "show", task: str = "", index: int = -1) -> str:
    """A simple in-session todo list. Actions: show | add | done | clear."""
    action = (action or "show").strip().lower()
    if action == "show":
        if not _TODO_LIST:
            return "(todo list empty)"
        return "\n".join(
            f"[{i}] {'✓' if e['done'] else '·'} {e['task']}"
            for i, e in enumerate(_TODO_LIST)
        )
    if action == "add":
        if not task:
            return "ERROR: 'task' is required for add"
        _TODO_LIST.append({"task": task, "done": False})
        return f"added: [{len(_TODO_LIST)-1}] {task}"
    if action == "done":
        if not (0 <= index < len(_TODO_LIST)):
            return f"ERROR: index {index} out of range"
        _TODO_LIST[index]["done"] = True
        return f"completed: [{index}] {_TODO_LIST[index]['task']}"
    if action == "clear":
        _TODO_LIST.clear()
        return "cleared"
    return f"ERROR: unknown action {action!r}"


_TOOL_SPECS: list[ToolSpec] = [
    # ---- File reads (safe) ----
    ToolSpec(
        name="read_file",
        description="Read up to max_lines lines from a text file. Returns "
                    "numbered lines for easier downstream editing.",
        parameters={"path": "string", "max_lines": "int (default 200)",
                    "offset": "int (default 0)"},
        is_safe=True,
        handler=_tool_read_file,
    ),
    ToolSpec(
        name="list_files",
        description="List entries in a directory (non-recursive).",
        parameters={"path": "string (default '.')"},
        is_safe=True,
        handler=_tool_list_files,
    ),
    ToolSpec(
        name="glob",
        description="Find files matching a glob pattern (supports ** for "
                    "recursive). Returns absolute paths.",
        parameters={"pattern": "string e.g. '**/*.py'",
                    "path": "string (default '.')"},
        is_safe=True,
        handler=_tool_glob,
    ),
    ToolSpec(
        name="tree",
        description="Render a directory tree up to max_depth. Skips heavy "
                    "dirs (.git, node_modules, __pycache__, .venv, dist, ...).",
        parameters={"path": "string (default '.')",
                    "max_depth": "int (default 3)"},
        is_safe=True,
        handler=_tool_tree,
    ),
    ToolSpec(
        name="grep",
        description="Recursive regex search via the system grep -rn.",
        parameters={"pattern": "string", "path": "string (default '.')"},
        is_safe=True,
        handler=_tool_grep,
    ),
    ToolSpec(
        name="file_stat",
        description="Return file size, mtime, mode, and kind (file/dir/...).",
        parameters={"path": "string"},
        is_safe=True,
        handler=_tool_file_stat,
    ),
    ToolSpec(
        name="diff_files",
        description="Unified diff between two existing files. Use to compare "
                    "before / after of an edit, or two related files.",
        parameters={"a": "string", "b": "string",
                    "context": "int lines (default 3)"},
        is_safe=True,
        handler=_tool_diff_files,
    ),
    # ---- File writes (needs permission) ----
    ToolSpec(
        name="write_file",
        description="Create or overwrite a file. REQUIRES PERMISSION. Prefer "
                    "edit_file when modifying an existing file.",
        parameters={"path": "string", "content": "string"},
        is_safe=False,
        handler=_tool_write_file,
    ),
    ToolSpec(
        name="edit_file",
        description="Replace exactly one occurrence of old with new in path. "
                    "REQUIRES PERMISSION. Errors if old is non-unique.",
        parameters={"path": "string", "old": "string", "new": "string"},
        is_safe=False,
        handler=_tool_edit_file,
    ),
    ToolSpec(
        name="search_and_replace",
        description="Like edit_file but allows replace_all=true when the old "
                    "string appears more than once. REQUIRES PERMISSION.",
        parameters={"path": "string", "old": "string", "new": "string",
                    "replace_all": "bool (default false)"},
        is_safe=False,
        handler=_tool_search_and_replace,
    ),
    ToolSpec(
        name="append_file",
        description="Append content to a file (creates it if missing). "
                    "REQUIRES PERMISSION.",
        parameters={"path": "string", "content": "string"},
        is_safe=False,
        handler=_tool_append_file,
    ),
    ToolSpec(
        name="mkdir",
        description="Create a directory (mkdir -p). REQUIRES PERMISSION.",
        parameters={"path": "string"},
        is_safe=False,
        handler=_tool_mkdir,
    ),
    ToolSpec(
        name="delete_file",
        description="Delete a single file (NOT a directory). REQUIRES "
                    "PERMISSION. To delete a directory use `bash` explicitly.",
        parameters={"path": "string"},
        is_safe=False,
        handler=_tool_delete_file,
    ),
    ToolSpec(
        name="move_file",
        description="Rename or move a file. REQUIRES PERMISSION.",
        parameters={"src": "string", "dst": "string"},
        is_safe=False,
        handler=_tool_move_file,
    ),
    ToolSpec(
        name="apply_patch",
        description="Apply a unified-diff patch via system `patch` or "
                    "`git apply`. REQUIRES PERMISSION. The patch text is fed "
                    "on stdin so you can construct it inline.",
        parameters={"patch": "string (unified diff content)"},
        is_safe=False,
        handler=_tool_apply_patch,
    ),
    # ---- Execution (needs permission) ----
    ToolSpec(
        name="bash",
        description="Run a shell command via bash -c. REQUIRES PERMISSION.",
        parameters={"command": "string",
                    "timeout": "int seconds (default 60)"},
        is_safe=False,
        handler=_tool_bash,
    ),
    ToolSpec(
        name="python_eval",
        description="Run a Python snippet in a subprocess (python3 -c). "
                    "REQUIRES PERMISSION.",
        parameters={"code": "string",
                    "timeout": "int seconds (default 30)"},
        is_safe=False,
        handler=_tool_python_eval,
    ),
    # ---- Network ----
    ToolSpec(
        name="fetch_url",
        description="HTTP GET a URL and return headers + body (capped). "
                    "Safe — no auth, no JS, read-only.",
        parameters={"url": "string",
                    "max_bytes": "int (default 32768)",
                    "timeout": "int seconds (default 15)"},
        is_safe=True,
        handler=_tool_fetch_url,
    ),
    # ---- Animica blockchain ----
    ToolSpec(
        name="animica_rpc",
        description="JSON-RPC call against the local Animica node. Safe — "
                    "read-only RPCs only. Use submit_tx via `bash animica tx` "
                    "for state-changing operations.",
        parameters={"method": "string e.g. 'chain.getHead'",
                    "params": "object or array (default {})"},
        is_safe=True,
        handler=_tool_animica_rpc,
    ),
    ToolSpec(
        name="balance",
        description="Get the ANIMICA balance of a bech32 address (anim1...).",
        parameters={"address": "string"},
        is_safe=True,
        handler=_tool_balance,
    ),
    ToolSpec(
        name="chain_head",
        description="Get the current chain head (height, hash, theta).",
        parameters={},
        is_safe=True,
        handler=_tool_chain_head,
    ),
    # ---- Cognitive scaffolding ----
    ToolSpec(
        name="think",
        description="Record a chain-of-thought note without taking action. "
                    "Use to plan before making destructive changes.",
        parameters={"thought": "string"},
        is_safe=True,
        handler=_tool_think,
    ),
    ToolSpec(
        name="todo",
        description="Manage an in-session todo list. action: 'show' | 'add' "
                    "(with task) | 'done' (with index) | 'clear'.",
        parameters={"action": "string", "task": "string (for add)",
                    "index": "int (for done)"},
        is_safe=True,
        handler=_tool_todo,
    ),
    # ---- Completion ----
    ToolSpec(
        name="done",
        description="Signal task completion. Pass a one-line summary.",
        parameters={"message": "string"},
        is_safe=True,
        handler=_tool_done,
    ),
]
_TOOL_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in _TOOL_SPECS}


# --------------------------------------------------------------------------- #
# System prompt                                                               #
# --------------------------------------------------------------------------- #


_SYSTEM_PROMPT_TEMPLATE = """You are an autonomous coding assistant running on a remote
miner via the Animica AICF protocol. You can read and modify the user's
filesystem and run shell commands by emitting tool calls.

To call a tool, emit EXACTLY this block (one tool per turn):

[TOOL_CALL]
{"name": "<tool_name>", "arguments": {<args>}}
[/TOOL_CALL]

After the tool runs, you will see its result on the next turn prefixed with
[TOOL_RESULT]. Use that to inform your next action.

When you are finished, call the `done` tool:

[TOOL_CALL]
{"name": "done", "arguments": {"message": "summary of what you did"}}
[/TOOL_CALL]

Available tools:
__TOOL_LIST__

Rules:
- Emit AT MOST ONE tool call per turn. Do not stack multiple [TOOL_CALL] blocks.
- Output ONLY the [TOOL_CALL] block and a brief one-line rationale BEFORE it.
  Do not paste large file contents or commentary into your reply.
- If you have all the information needed to answer without using tools, just
  answer the user directly in plain text (no [TOOL_CALL] block).
- When editing files, prefer `edit_file` (string replace) over `write_file`
  (full overwrite) unless you are creating a new file or rewriting the whole
  file. Be precise with the `old` value — it must be a unique substring.
- Treat ERROR: results as recoverable: investigate, then retry with adjusted
  arguments.

Working directory: __CWD__
"""


def _render_tool_list() -> str:
    lines = []
    for t in _TOOL_SPECS:
        params = ", ".join(f"{k}: {v}" for k, v in t.parameters.items())
        safety = "" if t.is_safe else "  [needs permission]"
        lines.append(f"  - {t.name}({params}) — {t.description}{safety}")
    return "\n".join(lines)


# Project instruction files, in the order they are looked for. AGENTS.md is the
# emerging cross-tool convention; the others are what people already have lying
# around from other assistants, and reading them costs nothing.
PROJECT_CONTEXT_FILES = (
    "AGENTS.md", "ANIMICA.md", "CLAUDE.md", ".animica/instructions.md",
    ".github/copilot-instructions.md",
)
MAX_PROJECT_CONTEXT_BYTES = 8000


def load_project_context(cwd: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """The repo's own instructions, if it has any: (filename, text) or (None, None).

    Walks up from `cwd` to the filesystem root, so running the agent in a
    subdirectory of a repo still picks up the file at its top. Stops at the first
    match rather than merging several, because two files disagreeing about house
    style is a worse outcome than reading only the nearest one.

    Truncated at 8 KiB. A long instruction file competes with the actual task for
    the model's attention, and silently spending the context window on it is worse
    than saying it was cut.
    """
    start = Path(cwd or os.getcwd()).expanduser().resolve()
    for directory in [start, *start.parents]:
        for name in PROJECT_CONTEXT_FILES:
            candidate = directory / name
            try:
                if not candidate.is_file():
                    continue
                text = candidate.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if not text:
                continue
            if len(text) > MAX_PROJECT_CONTEXT_BYTES:
                text = (text[:MAX_PROJECT_CONTEXT_BYTES]
                        + f"\n\n[truncated at {MAX_PROJECT_CONTEXT_BYTES} bytes]")
            try:
                shown = str(candidate.relative_to(start))
            except ValueError:
                shown = str(candidate)
            return shown, text
    return None, None


def build_system_prompt(cwd: Optional[str] = None) -> str:
    base = (
        _SYSTEM_PROMPT_TEMPLATE
        .replace("__TOOL_LIST__", _render_tool_list())
        .replace("__CWD__", cwd or os.getcwd())
    )
    name, text = load_project_context(cwd)
    if not text:
        return base
    # Appended after the tool contract, not before: the house rules of one repo
    # must not be able to talk the agent out of how tool calls are formatted.
    return (base
            + f"\n\n# Project instructions (from {name})\n"
            + "The repository you are working in ships these. Follow them unless the "
            + "user's request contradicts them, and say so if it does.\n\n"
            + text)


# Back-compat for any caller that reaches for SYSTEM_PROMPT directly.
SYSTEM_PROMPT = _SYSTEM_PROMPT_TEMPLATE


# --------------------------------------------------------------------------- #
# Tool-call parser                                                            #
# --------------------------------------------------------------------------- #


_TOOL_CALL_RE = re.compile(
    r"\[TOOL_CALL\]\s*(?:```(?:json)?\s*)?(\{.*?\})\s*(?:```\s*)?\[/TOOL_CALL\]",
    re.DOTALL,
)


@dataclass
class ParsedToolCall:
    name: str
    arguments: dict
    raw: str


def parse_tool_call(text: str) -> Optional[ParsedToolCall]:
    """Find the first [TOOL_CALL] block; return None if not present.

    Permissive about formatting variations:
    - optional ``` / ```json fencing inside the block
    - whitespace around the JSON
    - if the strict regex misses, fall back to brace-counting from the marker
    """
    m = _TOOL_CALL_RE.search(text)
    if m is None:
        # Fallback: find `[TOOL_CALL]` then brace-match the JSON.
        start = text.find("[TOOL_CALL]")
        if start < 0:
            return None
        brace = text.find("{", start)
        if brace < 0:
            return None
        depth = 0
        end = -1
        for i in range(brace, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end < 0:
            return None
        raw_json = text[brace:end]
    else:
        raw_json = m.group(1)
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name") or "").strip()
    args = payload.get("arguments") or {}
    if not isinstance(args, dict):
        return None
    if not name:
        return None
    return ParsedToolCall(name=name, arguments=args, raw=raw_json)


# --------------------------------------------------------------------------- #
# Permission gating                                                           #
# --------------------------------------------------------------------------- #


# What a tool actually does, which is what an approval decision turns on.
#
# `is_safe` is a single bit and cannot express the distinction people actually
# want: "edit my files without asking, but ask before you run a shell command."
# Categories can. A tool absent from this map falls back to its `is_safe` flag,
# so a tool added later is gated rather than silently permitted.
TOOL_CATEGORY: dict[str, str] = {
    # reads — no side effects
    "read_file": "read", "list_files": "read", "glob": "read", "tree": "read",
    "grep": "read", "file_stat": "read", "diff_files": "read",
    "think": "read", "todo": "read", "done": "read",
    "balance": "read", "chain_head": "read", "animica_rpc": "read",
    # reaches the network — safe locally, but it leaves the machine
    "fetch_url": "net",
    # changes files
    "write_file": "write", "edit_file": "write", "search_and_replace": "write",
    "append_file": "write", "mkdir": "write", "move_file": "write",
    "apply_patch": "write",
    # removes things
    "delete_file": "destroy",
    # runs arbitrary code
    "bash": "exec", "python_eval": "exec",
}

# Ordered least to most dangerous, so a mode can be described as a ceiling.
PERMISSION_MODES: dict[str, dict] = {
    # Read-only reconnaissance. Nothing leaves the machine, nothing changes.
    "plan": {"auto": {"read"}, "label": "plan", "blurb": "reads only; writes, shell and network refused"},
    # The default. Reads are free; anything with a consequence is asked about.
    "manual": {"auto": {"read"}, "label": "manual", "blurb": "asks before writing, deleting, running shell or fetching"},
    # Edits flow, execution does not — the mode most coding sessions want.
    "auto-edit": {"auto": {"read", "write", "net"}, "label": "auto-edit",
                  "blurb": "writes and edits apply automatically; shell and delete still ask"},
    # Everything. Named so it cannot be chosen by accident.
    "auto": {"auto": {"read", "write", "net", "destroy", "exec"}, "label": "auto",
             "blurb": "EVERYTHING auto-approved, including shell and delete"},
}

DEFAULT_PERMISSION_MODE = "manual"


def category_of(tool: ToolSpec) -> str:
    """A tool's category, defaulting so an unmapped tool is never auto-allowed
    just because nobody classified it."""
    cat = TOOL_CATEGORY.get(tool.name)
    if cat:
        return cat
    return "read" if tool.is_safe else "exec"


@dataclass
class PermissionPolicy:
    """Decide whether a tool call can run.

    Four modes, from `plan` (reads only) through `manual` (the default: ask
    before anything with a consequence) and `auto-edit` (edits apply, shell
    still asks) to `auto` (everything).

    The mode is meant to be *visible* wherever the agent runs. An agent that
    auto-approves shell commands without saying so is the failure this replaced:
    `--yolo-tools` was one boolean, off-screen, and indistinguishable from
    careful behaviour until something was already deleted.

    `yolo=` and `read_only=` are still accepted so existing callers and the old
    flags keep working; they map onto `auto` and `plan`.
    """

    mode: str = DEFAULT_PERMISSION_MODE
    # Per-tool overrides: name -> "allow" | "deny" | "ask"
    overrides: dict = field(default_factory=dict)
    # Persistent "yes to all of these in this session"
    session_allowed: set = field(default_factory=set)

    def __init__(self, mode: str = DEFAULT_PERMISSION_MODE, *, yolo: bool = False,
                 read_only: bool = False, overrides: dict | None = None,
                 session_allowed: set | None = None) -> None:
        if yolo and read_only:
            raise ValueError("yolo and read_only are contradictory; pick one mode")
        if yolo:
            mode = "auto"
        elif read_only:
            mode = "plan"
        self.mode = self.normalize_mode(mode)
        self.overrides = dict(overrides or {})
        self.session_allowed = set(session_allowed or ())

    @staticmethod
    def normalize_mode(mode: str) -> str:
        m = str(mode or "").strip().lower().replace("_", "-")
        aliases = {
            "yolo": "auto", "auto-all": "auto", "bypass": "auto",
            "read-only": "plan", "readonly": "plan", "ro": "plan",
            "ask": "manual", "default": "manual", "prompt": "manual",
            "acceptedits": "auto-edit", "accept-edits": "auto-edit", "edits": "auto-edit",
        }
        m = aliases.get(m, m)
        if m not in PERMISSION_MODES:
            raise ValueError(
                f"unknown permission mode {mode!r}; choose one of "
                + ", ".join(PERMISSION_MODES)
            )
        return m

    # -- introspection, so the UI can always state what is armed -------------
    @property
    def label(self) -> str:
        return PERMISSION_MODES[self.mode]["label"]

    @property
    def blurb(self) -> str:
        return PERMISSION_MODES[self.mode]["blurb"]

    @property
    def is_read_only(self) -> bool:
        return self.mode == "plan"

    @property
    def auto_approves_everything(self) -> bool:
        return self.mode == "auto"

    def auto_categories(self) -> set:
        return set(PERMISSION_MODES[self.mode]["auto"])

    def would_ask(self, tool: ToolSpec) -> bool:
        """Would this tool prompt right now? Used to preview a mode change."""
        if self.overrides.get(tool.name) in ("allow", "deny"):
            return False
        if tool.name in self.session_allowed:
            return False
        return category_of(tool) not in self.auto_categories()

    def evaluate(self, tool: ToolSpec, args: dict, *, prompter) -> tuple[bool, str]:
        cat = category_of(tool)
        override = self.overrides.get(tool.name)
        if override == "deny":
            return False, "denied by policy"
        if override == "allow":
            return True, "allowed by policy"

        auto = self.auto_categories()
        if cat in auto:
            return True, f"{cat} auto-allowed in {self.label} mode"

        # plan mode refuses rather than asking: its whole purpose is that the
        # session cannot change anything, and a prompt is a way to change that.
        if self.mode == "plan":
            return False, f"blocked: plan mode refuses {cat} tools ({tool.name})"

        if tool.name in self.session_allowed:
            return True, "allowed for this session earlier"

        decision = prompter(tool, args)
        if decision == "allow":
            return True, "allowed once"
        if decision == "allow_session":
            self.session_allowed.add(tool.name)
            return True, "allowed for this session"
        if decision == "allow_mode":
            # "always do this kind of thing" — widen the mode, not just one tool.
            self.mode = "auto-edit" if cat in ("write", "net") else "auto"
            return True, f"switched to {self.label} mode"
        return False, "denied"


# --------------------------------------------------------------------------- #
# Agent loop                                                                  #
# --------------------------------------------------------------------------- #


@dataclass
class AgentTurn:
    """One round-trip of the agent loop."""

    iteration: int
    prompt_sent: str        # the user message (for AICF), with system+history baked in
    assistant_text: str     # raw model output
    tool_call: Optional[ParsedToolCall]
    tool_result: Optional[str]
    cost_animica: float
    latency_ms: int


@dataclass
class AgentRunResult:
    turns: list[AgentTurn]
    final_text: str
    completed: bool
    total_cost: float
    stop_reason: str        # "done" | "max_iterations" | "max_cost" | "no_provider" | "user_abort"
    # Why the provider gave up, when stop_reason == "no_provider". Kept SEPARATE
    # from final_text on purpose: final_text is shown and remembered as the
    # assistant's words, and an error message is not the assistant's words.
    error: Optional[str] = None


def _format_assistant_for_history(text: str) -> str:
    # Keep the tool_call block intact so the model sees its own action when
    # we send the next turn — that gives better self-correction.
    return text.strip()


def _render_history_for_prompt(history: list[dict]) -> str:
    """Render conversation history as a flat string the AICF backend can
    feed straight into the model. AICF's submitInferenceJob accepts a single
    `prompt` field, so we serialize the conversation into ChatML-ish tags.
    """
    parts: list[str] = []
    for msg in history:
        role = msg.get("role", "user")
        content = str(msg.get("content", ""))
        parts.append(f"<|im_start|>{role}\n{content}\n<|im_end|>")
    parts.append("<|im_start|>assistant\n")
    return "\n".join(parts)


def run_agent_loop(
    *,
    user_task: str,
    submit_turn: Callable[[str], tuple[str, float, int]],
    policy: PermissionPolicy,
    permission_prompter: Callable[[ToolSpec, dict], str],
    on_iteration: Optional[Callable[[AgentTurn], None]] = None,
    cwd: Optional[str] = None,
    max_iterations: int = 10,
    max_cost: float = 0.5,
    initial_history: Optional[list[dict]] = None,
) -> AgentRunResult:
    """Run the agentic loop.

    ``submit_turn(prompt) -> (text, cost_animica, latency_ms)`` is the
    delegated AICF submission — the chat REPL plugs its ProviderCascade
    here, so this module stays free of agent_runtime imports.

    ``permission_prompter(tool, args) -> "allow" | "allow_session" | "deny"``
    is called for every non-safe tool when not in yolo / read_only mode.
    """
    history: list[dict[str, str]] = list(initial_history or [])
    history.append({"role": "system", "content": build_system_prompt(cwd)})
    history.append({"role": "user", "content": user_task})

    turns: list[AgentTurn] = []
    total_cost = 0.0
    final_text = ""
    completed = False
    stop_reason = "max_iterations"
    provider_error: Optional[str] = None

    for i in range(1, max_iterations + 1):
        prompt = _render_history_for_prompt(history)
        try:
            text, cost, latency_ms = submit_turn(prompt)
        except Exception as exc:
            # DO NOT put this in final_text. The caller renders final_text as the
            # assistant's reply and saves it to the session, so assigning an
            # exception string here made a network fault into both a fake answer
            # ("agent loop aborted: provider error: …" printed as if the model had
            # said it) and a corrupted transcript that the next turn then read
            # back as context. The reason belongs in stop_reason and error, where
            # a caller can report it as a failure.
            stop_reason = "no_provider"
            provider_error = str(exc)
            break
        total_cost += float(cost or 0.0)

        call = parse_tool_call(text)
        tool_result: Optional[str] = None

        turn = AgentTurn(
            iteration=i,
            prompt_sent=prompt,
            assistant_text=text,
            tool_call=call,
            tool_result=None,
            cost_animica=float(cost or 0.0),
            latency_ms=int(latency_ms or 0),
        )

        # No tool call -> treat as final answer.
        if call is None:
            final_text = text.strip()
            completed = True
            stop_reason = "no_tool_call"
            turns.append(turn)
            if on_iteration:
                on_iteration(turn)
            break

        # done() tool ends the loop cleanly.
        if call.name == "done":
            msg = str(call.arguments.get("message") or "(no summary)")
            final_text = msg
            completed = True
            stop_reason = "done"
            tool_result = f"DONE: {msg}"
            turn.tool_result = tool_result
            turns.append(turn)
            if on_iteration:
                on_iteration(turn)
            break

        # Look up and gate the tool.
        spec = _TOOL_BY_NAME.get(call.name)
        if spec is None:
            tool_result = f"ERROR: unknown tool: {call.name!r}"
        else:
            allowed, reason = policy.evaluate(
                spec, call.arguments, prompter=permission_prompter
            )
            if not allowed:
                if reason == "user denied":
                    stop_reason = "user_abort"
                    tool_result = f"ERROR: user denied tool call ({reason})"
                    turn.tool_result = tool_result
                    turns.append(turn)
                    if on_iteration:
                        on_iteration(turn)
                    final_text = "(aborted by user)"
                    break
                tool_result = f"ERROR: tool call refused: {reason}"
            else:
                t0 = time.monotonic()
                try:
                    tool_result = spec.handler(**call.arguments)
                except TypeError as exc:
                    tool_result = f"ERROR: bad arguments for {spec.name}: {exc}"
                except Exception as exc:
                    tool_result = f"ERROR: tool {spec.name} raised: {exc}"
                # Hard cap on returned size so we don't pay to echo logs back.
                if isinstance(tool_result, str) and len(tool_result) > 8000:
                    tool_result = tool_result[:8000] + (
                        f"\n... (truncated at 8000 of {len(tool_result)} bytes)"
                    )
                _ = time.monotonic() - t0
        turn.tool_result = tool_result

        # Feed back into history as an assistant turn followed by a tool
        # result (delivered as a user role so the model treats it as ground
        # truth, not its own claim).
        history.append({"role": "assistant",
                        "content": _format_assistant_for_history(text)})
        history.append({"role": "user",
                        "content": f"[TOOL_RESULT]\n{tool_result}\n[/TOOL_RESULT]"})

        turns.append(turn)
        if on_iteration:
            on_iteration(turn)

        if total_cost >= max_cost:
            stop_reason = "max_cost"
            final_text = (
                f"(stopped: total cost {total_cost:.6f} ANIMICA reached cap "
                f"{max_cost:.6f}; raise with --max-cost)"
            )
            break

    return AgentRunResult(
        turns=turns,
        final_text=final_text,
        completed=completed,
        total_cost=total_cost,
        stop_reason=stop_reason,
        error=provider_error,
    )


__all__ = [
    "AgentRunResult",
    "AgentTurn",
    "ParsedToolCall",
    "PermissionPolicy",
    "ToolSpec",
    "build_system_prompt",
    "parse_tool_call",
    "run_agent_loop",
]
