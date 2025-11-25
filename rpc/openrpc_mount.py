from __future__ import annotations

"""
Serve the repository's OpenRPC description verbatim at `/openrpc.json`.

- Looks for the file in this order:
  1) Function argument `openrpc_path`
  2) Env var ANIMICA_OPENRPC_PATH
  3) <repo_root>/spec/openrpc.json  (repo_root is inferred from this file's location)

- Returns the file as-is with:
  * Content-Type: application/json
  * Cache-Control: public, max-age=60
  * ETag: sha256 of file bytes
  * Last-Modified: mtime

- Supports conditional GET via If-None-Match → 304.

Usage
-----
from fastapi import FastAPI
from rpc.openrpc_mount import mount_openrpc

app = FastAPI()
mount_openrpc(app)  # now GET /openrpc.json returns the spec verbatim
"""

import email.utils
import hashlib
import os
from pathlib import Path
import typing as t

from fastapi import APIRouter, FastAPI, Request
from starlette.responses import Response, PlainTextResponse


def _default_repo_root() -> Path:
    # repo_root/rpc/openrpc_mount.py  → repo_root
    here = Path(__file__).resolve()
    return here.parents[1]


def _resolve_openrpc_path(openrpc_path: t.Optional[t.Union[str, Path]] = None) -> Path:
    if openrpc_path:
        p = Path(openrpc_path).expanduser().resolve()
        return p
    env = os.getenv("ANIMICA_OPENRPC_PATH")
    if env:
        return Path(env).expanduser().resolve()
    candidate = _default_repo_root() / "spec" / "openrpc.json"
    return candidate.resolve()


class _OpenRPCDoc:
    __slots__ = ("path", "_bytes", "_etag", "_mtime")

    def __init__(self, path: Path) -> None:
        self.path = path
        self._bytes: bytes = b""
        self._etag: str = ""
        self._mtime: float = 0.0
        self._load()

    def _load(self) -> None:
        data = self.path.read_bytes()
        self._bytes = data
        self._etag = hashlib.sha256(data).hexdigest()
        self._mtime = self.path.stat().st_mtime

    def _maybe_reload(self) -> None:
        try:
            m = self.path.stat().st_mtime
        except FileNotFoundError:
            return
        if m != self._mtime:
            self._load()

    def make_response(self, request: Request) -> Response:
        # Hot-reload on change (useful in dev)
        self._maybe_reload()

        # Conditional GET: If-None-Match (ETag)
        inm = request.headers.get("if-none-match")
        if inm and self._etag in inm:
            return Response(status_code=304)

        last_mod_http = email.utils.formatdate(self._mtime, usegmt=True)
        headers = {
            "ETag": self._etag,
            "Last-Modified": last_mod_http,
            "Cache-Control": "public, max-age=60",
        }
        return Response(
            content=self._bytes,
            media_type="application/json",
            headers=headers,
        )


def mount_openrpc(app: FastAPI, *, openrpc_path: t.Optional[t.Union[str, Path]] = None) -> None:
    """
    Mount GET /openrpc.json to serve the spec verbatim.

    If the file cannot be found at startup, a 503 text response is served instead,
    but the handler remains mounted so it starts working as soon as the file appears.
    """
    router = APIRouter()
    path = _resolve_openrpc_path(openrpc_path)

    if path.exists():
        doc = _OpenRPCDoc(path)

        @router.get("/openrpc.json", include_in_schema=False)
        async def get_openrpc(request: Request) -> Response:  # noqa: D401
            """Return spec/openrpc.json verbatim with caching headers."""
            return doc.make_response(request)

        @router.head("/openrpc.json", include_in_schema=False)
        async def head_openrpc(request: Request) -> Response:
            resp = doc.make_response(request)
            # Strip body for HEAD
            return Response(status_code=resp.status_code, headers=resp.headers, media_type=resp.media_type)

    else:
        # Graceful placeholder; helps during early boot or misconfig.
        missing_msg = (
            f"OpenRPC document not found at: {path}\n"
            "Set ANIMICA_OPENRPC_PATH or pass openrpc_path= to mount_openrpc()."
        )

        @router.get("/openrpc.json", include_in_schema=False)
        async def get_openrpc_missing() -> Response:
            return PlainTextResponse(missing_msg, status_code=503)

        @router.head("/openrpc.json", include_in_schema=False)
        async def head_openrpc_missing() -> Response:
            return Response(status_code=503)

    app.include_router(router)


__all__ = ["mount_openrpc"]
