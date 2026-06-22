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

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Import the leaf service modules directly (NOT the package __init__) to avoid
# transitively importing any Qt / database code into the slim image.
from animica_studio.services.fs_project_service import (
    DEFAULT_MAX_READ_BYTES,
    FsProjectService,
    SandboxError,
)
from animica_studio.services.git_service import GitService

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
REPO_DIR = Path(os.environ.get("REPO_DIR", "/home/studio/workspace/repo"))


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
