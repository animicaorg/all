"""
animica.ena.tools — dynamic tool registry (propose → review → approve → run).

Lets a worker (or the model itself) PROPOSE a new agent tool and grow the
registered set over time — the last pillar of the self-improving pool. Because a
dynamic tool is model/worker-authored code, this is an arbitrary-code-execution
surface, so it is built governance-first:

  * Proposals are NEVER auto-activated. A human must call ``approve`` — which
    requires ``ANIMICA_ENA_ADMIN_TOKEN`` to be configured AND supplied. With no
    admin token set, approval is impossible (default-deny).
  * A static AST scan flags dangerous constructs (os/subprocess/socket imports,
    eval/exec/open, dunder access, …) for the reviewer; risky proposals are
    accepted into the queue but clearly marked.
  * Execution is OFF unless ``ANIMICA_ENA_DYNAMIC_TOOLS=1``, only runs APPROVED
    tools, and happens in an isolated child process with CPU/mem/file/time
    rlimits, a scrubbed env, and a throwaway temp cwd.

The subprocess is defense-in-depth, NOT a guarantee against a determined
attacker (it does not, by itself, block network egress). **Human approval is the
real gate.** Do not enable execution on a host holding secrets without OS-level
isolation (container / firejail / seccomp / network namespace).
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

from .models import now_ts, sha3_hex

_NAME_RE = re.compile(r"[a-z][a-z0-9_]{1,40}")
# Names a dynamic tool may not shadow (builtin agent tools + control words).
_RESERVED = {
    "read_file", "list_files", "glob", "tree", "grep", "file_stat", "diff_files",
    "write_file", "edit_file", "search_and_replace", "append_file", "mkdir",
    "delete_file", "move_file", "apply_patch", "bash", "python_eval", "fetch_url",
    "animica_rpc", "balance", "chain_head", "done",
}

# AST patterns the static scan flags as dangerous (advisory for the reviewer).
_DANGEROUS_MODULES = {
    "os", "sys", "subprocess", "socket", "shutil", "ctypes", "importlib",
    "multiprocessing", "resource", "signal", "threading", "mmap", "fcntl",
    "pty", "builtins", "gc", "inspect", "pickle", "marshal", "http", "urllib",
    "requests", "ftplib", "smtplib", "asyncio",
}
_DANGEROUS_CALLS = {
    "eval", "exec", "compile", "__import__", "open", "input", "globals",
    "locals", "vars", "getattr", "setattr", "delattr", "memoryview",
}

EXEC_ENV_FLAG = "ANIMICA_ENA_DYNAMIC_TOOLS"
ADMIN_TOKEN_ENV = "ANIMICA_ENA_ADMIN_TOKEN"


def scan_code(code: str) -> dict[str, Any]:
    """Static AST risk scan. Returns ``{flagged: bool, reasons: [...]}``.
    ``flagged`` does not block proposal — it informs the human reviewer."""
    reasons: list[str] = []
    try:
        tree = ast.parse(code or "")
    except SyntaxError as exc:
        return {"flagged": True, "reasons": [f"does not parse: {exc}"],
                "parses": False}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if root in _DANGEROUS_MODULES:
                    reasons.append(f"imports {a.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _DANGEROUS_MODULES:
                reasons.append(f"imports from {node.module}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _DANGEROUS_CALLS:
                reasons.append(f"calls {node.func.id}()")
        elif isinstance(node, ast.Attribute):
            if "__" in (node.attr or ""):
                reasons.append(f"accesses dunder attribute {node.attr!r}")
        elif isinstance(node, ast.Name):
            if "__" in node.id and node.id not in ("__name__",):
                reasons.append(f"references {node.id!r}")
    return {"flagged": bool(reasons), "reasons": sorted(set(reasons)),
            "parses": True}


# Subprocess harness: define the handler, read JSON args from stdin, call
# run(args)/handler(args), print a JSON result. The child IS the isolation.
_HARNESS = r'''
import sys, json
try:
    _args = json.loads(sys.stdin.read() or "{}")
except Exception:
    _args = {}
_ns = {"__name__": "_dyntool"}
try:
    exec(compile(_CODE, "<dyntool>", "exec"), _ns)
    _fn = _ns.get("run") or _ns.get("handler")
    if not callable(_fn):
        print(json.dumps({"ok": False, "error": "define run(args) or handler(args)"}))
        sys.exit(0)
    _res = _fn(_args if isinstance(_args, dict) else {})
    print(json.dumps({"ok": True, "result": str(_res)[:10000]}))
except Exception as _e:
    print(json.dumps({"ok": False, "error": str(_e)[:2000]}))
'''


def _rlimits() -> None:  # pragma: no cover - POSIX child setup
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    os.setsid()


class DynamicToolRegistry:
    """File-backed registry of proposed/approved dynamic tools."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._lock = threading.RLock()
        self.path = Path(cfg.home_path()) / "tools" / "registry.json"

    # -- persistence ------------------------------------------------------
    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"tools": {}}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    # -- lifecycle --------------------------------------------------------
    def propose(self, name: str, description: str, parameters: dict,
                handler_code: str, proposer: Optional[str] = None) -> dict[str, Any]:
        name = str(name or "").strip()
        if not _NAME_RE.fullmatch(name):
            raise ValueError("tool name must match [a-z][a-z0-9_]{1,40}")
        if name in _RESERVED:
            raise ValueError(f"'{name}' shadows a builtin tool")
        if not isinstance(handler_code, str) or not handler_code.strip():
            raise ValueError("handler_code is required")
        risk = scan_code(handler_code)
        rec = {
            "name": name, "description": str(description or ""),
            "parameters": parameters if isinstance(parameters, dict) else {},
            "handler_code": handler_code, "proposer": proposer,
            "status": "proposed", "risk": risk,
            "code_sha256": sha3_hex(handler_code),
            "created_at": now_ts(), "reviewed_at": None,
            "approver": None, "reject_reason": None,
        }
        with self._lock:
            data = self._load()
            existing = (data.get("tools") or {}).get(name)
            if existing and existing.get("status") == "approved":
                raise ValueError(f"'{name}' is already approved; reject it first")
            data.setdefault("tools", {})[name] = rec
            self._save(data)
        return rec

    def list(self, status: Optional[str] = None) -> list[dict]:
        tools = list((self._load().get("tools") or {}).values())
        if status:
            tools = [t for t in tools if t.get("status") == status]
        return sorted(tools, key=lambda t: t.get("created_at", 0))

    def get(self, name: str) -> Optional[dict]:
        return (self._load().get("tools") or {}).get(name)

    def approve(self, name: str, *, approver: str, admin_token: str) -> dict:
        """Approve a proposal — the ONLY path to making a tool active. Requires a
        configured + matching admin token; impossible if no token is set."""
        configured = os.environ.get(ADMIN_TOKEN_ENV, "")
        if not configured:
            raise PermissionError(
                f"approval disabled: set {ADMIN_TOKEN_ENV} on the coordinator")
        if not admin_token or admin_token != configured:
            raise PermissionError("invalid admin token")
        with self._lock:
            data = self._load()
            rec = (data.get("tools") or {}).get(name)
            if not rec:
                raise ValueError(f"no such proposal: {name}")
            rec["status"] = "approved"
            rec["approver"] = approver
            rec["reviewed_at"] = now_ts()
            self._save(data)
            return rec

    def reject(self, name: str, *, reason: str, admin_token: str) -> dict:
        configured = os.environ.get(ADMIN_TOKEN_ENV, "")
        if not configured or admin_token != configured:
            raise PermissionError("invalid or unconfigured admin token")
        with self._lock:
            data = self._load()
            rec = (data.get("tools") or {}).get(name)
            if not rec:
                raise ValueError(f"no such proposal: {name}")
            rec["status"] = "rejected"
            rec["reject_reason"] = str(reason or "")
            rec["reviewed_at"] = now_ts()
            self._save(data)
            return rec

    def approved_tools(self) -> list[dict]:
        """Approved tools, shaped for the curriculum/agent (name + params)."""
        return [{"name": t["name"], "description": t.get("description", ""),
                 "parameters": t.get("parameters", {})}
                for t in self.list(status="approved")]

    # -- execution (off by default, approved + sandboxed) -----------------
    def execute(self, name: str, args: dict, *, timeout: float = 10.0
                ) -> dict[str, Any]:
        if os.environ.get(EXEC_ENV_FLAG, "").lower() not in ("1", "true", "yes", "on"):
            return {"ok": False,
                    "error": f"dynamic tool execution disabled (set {EXEC_ENV_FLAG}=1)"}
        rec = self.get(name)
        if not rec or rec.get("status") != "approved":
            return {"ok": False, "error": f"tool '{name}' is not approved"}
        script = f"_CODE = {rec['handler_code']!r}\n" + _HARNESS
        with tempfile.TemporaryDirectory(prefix="dyntool-") as tmp:
            env = {"PATH": "/usr/bin:/bin", "HOME": tmp, "LC_ALL": "C",
                   "TMPDIR": tmp}
            try:
                proc = subprocess.run(
                    [sys.executable, "-I", "-c", script],
                    input=json.dumps(args or {}), text=True,
                    capture_output=True, timeout=timeout, env=env, cwd=tmp,
                    preexec_fn=_rlimits if os.name == "posix" else None)
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": "tool timed out"}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"sandbox error: {exc}"}
            line = (proc.stdout or "").strip().splitlines()[-1:] or [""]
            try:
                return json.loads(line[0])
            except Exception:  # noqa: BLE001
                return {"ok": False,
                        "error": (proc.stderr or "no output")[-500:]}
