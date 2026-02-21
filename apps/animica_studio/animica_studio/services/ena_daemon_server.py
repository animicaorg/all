"""Reference CPU-only ENA daemon server used by Studio for local mode."""

from __future__ import annotations

import argparse
import json
from typing import Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import uvicorn

app = FastAPI(title="Animica ENA Daemon", version="0.1.0")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": "0.1.0", "capabilities": {"chat": True, "tools": True, "embed": False}}


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
