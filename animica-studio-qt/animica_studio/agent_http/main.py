"""Animica Studio agent sidecar — FastAPI app (CONTRACT 1, Increment 1).

A headless HTTP API that runs inside the per-user ``anm-studio-ide`` container
and owns a single cloned repository at ``REPO_DIR``
(default ``/home/studio/workspace/repo``). The broker (studio-host) proxies its
gated ``/api/ide/*`` routes here.

Endpoints:
    GET  /healthz        -> {"ok": true, "hasRepo": bool}
    POST /git/clone      -> {"ok": true, "branch": "..."}
    GET  /git/status     -> {"branch","ahead","behind","files":[...]}
    GET  /fs/tree        -> {"tree": <node>}
    GET  /fs/read?path=  -> {"path","content","truncated"}
    PUT  /fs/write       -> {"ok": true}

The services imported here are deliberately the *headless* modules
(``fs_project_service``, ``git_service``) so no Qt / DB code is pulled in.
"""
from __future__ import annotations

import json
import os
import queue
import signal
import socket
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

# Import the leaf service modules directly (NOT the package __init__) to avoid
# transitively importing any Qt / database code into the slim image.
from animica_studio.services.fs_project_service import (
    DEFAULT_MAX_READ_BYTES,
    FsProjectService,
    SandboxError,
)
from animica_studio.services import git_service as git_ops
from animica_studio.services.git_service import GitService

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
REPO_DIR = Path(os.environ.get("REPO_DIR", "/home/studio/workspace/repo"))
DEV_PORT = int(os.environ.get("DEV_PORT", "8099"))


def _ensure_repo_dir() -> None:
    REPO_DIR.mkdir(parents=True, exist_ok=True)


def _has_repo() -> bool:
    return (REPO_DIR / ".git").exists()


def _fs() -> FsProjectService:
    """Construct a sandboxed FS service bound to REPO_DIR (created if missing)."""
    return FsProjectService(REPO_DIR)


def _git() -> GitService:
    return GitService(REPO_DIR)


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class CloneRequest(BaseModel):
    url: str
    token: Optional[str] = None
    branch: Optional[str] = None


class WriteRequest(BaseModel):
    path: str
    content: str


class EnaChatRequest(BaseModel):
    message: str
    history: Optional[list[dict[str, Any]]] = None
    # Per-user inference auth (the broker injects the calling user's own pool key
    # so usage bills to them). Falls back to the ANIMICA_ENA_* env when absent.
    ena_key: Optional[str] = None
    ena_base: Optional[str] = None
    ena_model: Optional[str] = None
    # ANM budget metering (broker-driven). When ``budget_anm`` is set the loop
    # charges the ACTUAL cost of each model call (estimated tokens ×
    # ``anm_per_ktok``, ANM per 1k tokens) and stops cleanly once spend reaches
    # the cap. When ``budget_anm`` is None metering is disabled (legacy).
    budget_anm: Optional[float] = None
    anm_per_ktok: Optional[float] = None


class EnaApproveRequest(BaseModel):
    id: str
    accept: bool


class CommitRequest(BaseModel):
    message: str
    paths: Optional[list[str]] = None


class PushRequest(BaseModel):
    token: Optional[str] = None
    remote: Optional[str] = None
    branch: Optional[str] = None


class PreviewStartRequest(BaseModel):
    cmd: Optional[str] = None


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
app = FastAPI(title="Animica Studio Agent Sidecar", version="0.1.0")


def _err(message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


@app.on_event("startup")
def _on_startup() -> None:
    _ensure_repo_dir()


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "hasRepo": _has_repo()}


# --------------------------------------------------------------------------- #
# Git
# --------------------------------------------------------------------------- #
@app.post("/git/clone")
def git_clone(req: CloneRequest):
    url = (req.url or "").strip()
    if not url:
        return _err("url is required", 400)
    # Only allow http(s) clone URLs from the broker; reject local/ssh schemes.
    if not (url.startswith("http://") or url.startswith("https://")):
        return _err("only http(s) clone urls are supported", 400)
    try:
        svc = GitService.clone(
            url,
            REPO_DIR,
            token=req.token or None,
            branch=(req.branch or None),
        )
    except RuntimeError as exc:
        return _err(str(exc), 502)
    except ValueError as exc:
        return _err(str(exc), 400)
    branch = svc.current_branch()
    return {"ok": True, "branch": branch}


@app.get("/git/status")
def git_status():
    if not _has_repo():
        return _err("no repository", 404)
    return _git().status_dict()


# --------------------------------------------------------------------------- #
# Filesystem
# --------------------------------------------------------------------------- #
@app.get("/fs/tree")
def fs_tree():
    if not _has_repo():
        return _err("no repository", 404)
    try:
        tree = _fs().tree()
    except OSError as exc:
        return _err(str(exc), 500)
    return {"tree": tree}


@app.get("/fs/read")
def fs_read(path: str = Query(..., description="path relative to REPO_DIR")):
    if not _has_repo():
        return _err("no repository", 404)
    try:
        result = _fs().read_file(path, max_bytes=DEFAULT_MAX_READ_BYTES)
    except SandboxError as exc:
        return _err(str(exc), 403)
    except FileNotFoundError:
        return _err("file not found", 404)
    except IsADirectoryError:
        return _err("path is a directory", 400)
    except OSError as exc:
        return _err(str(exc), 500)
    return result


@app.put("/fs/write")
def fs_write(req: WriteRequest):
    if not _has_repo():
        return _err("no repository", 404)
    path = (req.path or "").strip()
    if not path:
        return _err("path is required", 400)
    try:
        _fs().write_file(path, req.content if req.content is not None else "")
    except SandboxError as exc:
        return _err(str(exc), 403)
    except IsADirectoryError:
        return _err("path is a directory", 400)
    except OSError as exc:
        return _err(str(exc), 500)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# ENA agent loop (SSE) + diff approval
# --------------------------------------------------------------------------- #
# Registry mapping a diff id -> the live HeadlessAgentLoop awaiting approval, so
# POST /ena/approve can route the decision to the correct in-flight turn.
_LOOP_LOCK = threading.Lock()
_LOOPS_BY_DIFF: dict[str, Any] = {}


def _sse_event(event_type: str, data: dict) -> str:
    """Frame one SSE event: ``event: <type>\\ndata: <json>\\n\\n``."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/ena/chat")
def ena_chat(req: EnaChatRequest):
    """Run one ENA agent turn, streaming events as ``text/event-stream``."""
    from animica_studio.agent.headless_loop import HeadlessAgentLoop, settings_from_env

    message = (req.message or "").strip()
    settings = settings_from_env(dict(os.environ))
    # Per-request override (per-user key/base/model injected by the broker).
    import dataclasses as _dc
    _ov = {}
    if req.ena_key:
        _ov["api_key"] = req.ena_key
    if req.ena_base:
        _ov["base_url"] = req.ena_base.rstrip("/")
    if req.ena_model:
        _ov["model"] = req.ena_model
    if _ov:
        try:
            settings = _dc.replace(settings, **_ov)
        except Exception:
            for k, v in _ov.items():
                setattr(settings, k, v)

    # A thread-safe queue bridges the worker thread to the SSE generator. A
    # sentinel (None) signals completion.
    events: "queue.Queue[Optional[str]]" = queue.Queue()
    registered_ids: list[str] = []

    def emit(event_type: str, data: dict) -> None:
        if event_type == "diff":
            diff_id = data.get("id")
            if diff_id:
                with _LOOP_LOCK:
                    _LOOPS_BY_DIFF[diff_id] = loop
                registered_ids.append(diff_id)
        events.put(_sse_event(event_type, data))

    # Build the loop (FsProjectService rooted at REPO_DIR). If the repo isn't
    # cloned yet the loop still works (empty project context).
    fs = FsProjectService(REPO_DIR)
    loop = HeadlessAgentLoop(
        fs=fs,
        settings=settings,
        emit=emit,
        budget_anm=req.budget_anm,
        anm_per_ktok=req.anm_per_ktok,
    )

    def worker() -> None:
        try:
            loop.run(message, req.history or [])
        finally:
            # Clean up any diff ids this turn registered.
            with _LOOP_LOCK:
                for did in registered_ids:
                    _LOOPS_BY_DIFF.pop(did, None)
            events.put(None)  # sentinel: end of stream

    t = threading.Thread(target=worker, name="ena-turn", daemon=True)
    t.start()

    def generate():
        while True:
            try:
                item = events.get(timeout=300.0)
            except queue.Empty:
                # Heartbeat / give up after a long idle.
                break
            if item is None:
                break
            yield item

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        generate(), media_type="text/event-stream", headers=headers
    )


@app.post("/ena/approve")
def ena_approve(req: EnaApproveRequest):
    diff_id = (req.id or "").strip()
    with _LOOP_LOCK:
        loop = _LOOPS_BY_DIFF.get(diff_id)
    if loop is None:
        return _err("unknown approval id", 404)
    ok = loop.approve(diff_id, bool(req.accept))
    if not ok:
        return _err("unknown approval id", 404)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Git diff / commit / push (sidecar half of the SCM contract)
# --------------------------------------------------------------------------- #
@app.get("/git/diff")
def git_diff(path: Optional[str] = Query(default=None)):
    if not _has_repo():
        return _err("no repository", 404)
    try:
        out = git_ops.diff(REPO_DIR, path=(path or None))
    except Exception as exc:  # pragma: no cover - defensive
        return _err(str(exc), 500)
    return {"diff": out}


@app.post("/git/commit")
def git_commit(req: CommitRequest):
    if not _has_repo():
        return _err("no repository", 404)
    message = (req.message or "").strip()
    if not message:
        return _err("commit message is required", 400)
    result = git_ops.commit(REPO_DIR, message, paths=req.paths or None)
    if "error" in result:
        return JSONResponse(status_code=200, content=result)
    return result


@app.post("/git/push")
def git_push(req: PushRequest):
    if not _has_repo():
        return _err("no repository", 404)
    result = git_ops.push(
        REPO_DIR,
        token=req.token or None,
        remote=(req.remote or "origin"),
        branch=(req.branch or None),
    )
    if "error" in result:
        # 502 on failure per contract; token already scrubbed by push().
        return JSONResponse(status_code=502, content=result)
    return result


# --------------------------------------------------------------------------- #
# Run / preview — a single tracked dev server per container.
# --------------------------------------------------------------------------- #
# Only one dev server runs at a time. The child is started in its own session
# (start_new_session=True) so we can signal the whole process group on stop —
# `npm run dev` typically forks the real server as a grandchild.
_PREVIEW_LOCK = threading.Lock()
_PREVIEW: dict[str, Any] = {
    "proc": None,          # subprocess.Popen | None
    "cmd": None,           # str | None — the command line we ran
    "log": deque(maxlen=200),  # last ~200 log lines (stdout+stderr merged)
    "reader": None,        # threading.Thread draining the pipe
}


def _detect_run_cmd() -> Optional[str]:
    """Best-effort detection of the dev-server command in REPO_DIR.

    package.json scripts.dev -> 'npm run dev'; scripts.start -> 'npm start';
    else if a root index.html exists -> static 'python3 -m http.server'.
    Returns None when nothing is detectable.
    """
    pkg = REPO_DIR / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = data.get("scripts") or {}
        except (OSError, ValueError, AttributeError):
            scripts = {}
        if isinstance(scripts, dict):
            if scripts.get("dev"):
                return "npm run dev"
            if scripts.get("start"):
                return "npm start"
    if (REPO_DIR / "index.html").is_file():
        return f"python3 -m http.server {DEV_PORT} --bind 0.0.0.0"
    return None


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    """True if something is accepting TCP connections on host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _preview_alive_locked() -> bool:
    proc = _PREVIEW["proc"]
    return proc is not None and proc.poll() is None


def _kill_preview_locked() -> None:
    """Terminate the current child + its process group (caller holds lock)."""
    proc = _PREVIEW["proc"]
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
    _PREVIEW["proc"] = None
    _PREVIEW["cmd"] = None
    _PREVIEW["reader"] = None


def _drain(proc: "subprocess.Popen[str]", log: "deque[str]") -> None:
    """Read merged stdout/stderr line-by-line into the bounded log buffer."""
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            log.append(line.rstrip("\n"))
    except (OSError, ValueError):
        pass


@app.post("/preview/start")
def preview_start(req: PreviewStartRequest):
    cmd = (req.cmd or "").strip() or _detect_run_cmd()
    if not cmd:
        return _err("no run command detected", 404)

    with _PREVIEW_LOCK:
        # Kill any prior dev server first (single server per container).
        _kill_preview_locked()
        log: "deque[str]" = deque(maxlen=200)

        env = dict(os.environ)
        env["PORT"] = str(DEV_PORT)
        env["HOST"] = "0.0.0.0"
        # Hint Vite/CRA/etc. to bind all interfaces on our fixed port.
        env.setdefault("BROWSER", "none")

        try:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                cwd=str(REPO_DIR),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            return _err(f"failed to start: {exc}", 500)

        reader = threading.Thread(
            target=_drain, args=(proc, log), name="preview-log", daemon=True
        )
        reader.start()

        _PREVIEW["proc"] = proc
        _PREVIEW["cmd"] = cmd
        _PREVIEW["log"] = log
        _PREVIEW["reader"] = reader

        # If the command died instantly, surface the failure with logs.
        if proc.poll() is not None:
            tail = list(log)
            _kill_preview_locked()
            return JSONResponse(
                status_code=500,
                content={"error": "run command exited immediately", "logTail": tail},
            )

        pid = proc.pid

    return {
        "ok": True,
        "url": f"http://0.0.0.0:{DEV_PORT}",
        "pid": pid,
        "cmd": cmd,
    }


@app.post("/preview/stop")
def preview_stop():
    with _PREVIEW_LOCK:
        _kill_preview_locked()
    return {"ok": True}


@app.get("/preview/status")
def preview_status():
    with _PREVIEW_LOCK:
        running = _preview_alive_locked()
        cmd = _PREVIEW["cmd"]
        tail = list(_PREVIEW["log"])
    return {
        "running": running,
        "cmd": cmd if running else None,
        "port": DEV_PORT,
        "listening": _port_open(DEV_PORT),
        "logTail": tail[-50:],
    }
