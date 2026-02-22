"""Reference CPU-only ENA daemon server used by Studio for local mode."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from typing import Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import uvicorn

app = FastAPI(title="Animica ENA Daemon", version="0.1.0")

_DA_BLOBS: dict[str, dict[str, str]] = {}


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": "0.1.0",
        "capabilities": {"chat": True, "tools": True, "embed": False, "da": True},
    }


@app.get("/version")
def version() -> dict[str, str]:
    return {"version": "0.1.0"}


@app.get("/tools")
def tools() -> dict[str, Any]:
    return {
        "tools": [
            {"name": "read_file", "description": "Read file contents"},
            {"name": "list_dir", "description": "List directory tree"},
            {"name": "search_text", "description": "Search text in workspace"},
        ]
    }


@app.post("/chat")
def chat(payload: dict[str, Any]) -> StreamingResponse:
    msgs = payload.get("messages") or []
    prompt = ""
    if msgs and isinstance(msgs[-1], dict):
        prompt = str(msgs[-1].get("content", ""))

    def _gen():
        text = f"ENA(local): {prompt}".strip()
        for tok in text.split(" "):
            yield f"data: {json.dumps({'type': 'token', 'text': tok + ' '})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


@app.post("/da/put")
def da_put_blob(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    namespace = str(payload.get("namespace") or "default")
    if not isinstance(data, str) or not data.strip():
        return {"ok": False, "error": "data is required and must be base64 text"}
    try:
        decoded = base64.b64decode(data, validate=True)
    except Exception:
        return {"ok": False, "error": "invalid base64 payload"}
    commitment = hashlib.sha256(decoded).hexdigest()
    _DA_BLOBS[commitment] = {"data": data, "namespace": namespace}
    return {"ok": True, "commitment": commitment, "namespace": namespace}


@app.get("/da/get/{commitment}")
def da_get_blob(commitment: str) -> dict[str, Any]:
    entry = _DA_BLOBS.get(commitment)
    if not entry:
        return {"ok": False, "error": "commitment not found", "commitment": commitment}
    return {"ok": True, "commitment": commitment, **entry}


@app.get("/da/proof/{commitment}")
def da_get_proof(commitment: str) -> dict[str, Any]:
    exists = commitment in _DA_BLOBS
    if not exists:
        return {"ok": False, "error": "commitment not found", "commitment": commitment}
    return {
        "ok": True,
        "commitment": commitment,
        "proof": {"type": "sha256", "verified": True, "note": "Local daemon proof stub"},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
