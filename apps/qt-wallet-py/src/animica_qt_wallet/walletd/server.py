from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from animica_qt_wallet.walletd.config import (
    DEFAULT_PORT,
    load_or_create_token,
    resolve_data_dir,
    resolve_log_path,
    resolve_port,
)


@dataclass
class WalletdState:
    token: str
    rpc_url: str
    log_path: Path
    last_error: str | None = None


def _setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(),
        ],
    )


async def handle_rpc(request: web.Request) -> web.Response:
    state: WalletdState = request.app["state"]
    token = _extract_token(request)
    if token != state.token:
        return web.json_response({"error": {"code": 401, "message": "Unauthorized"}}, status=401)

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": {"code": 400, "message": "Invalid JSON"}}, status=400)

    method = payload.get("method")
    params = payload.get("params") or {}
    request_id = payload.get("id")

    try:
        result = await dispatch(method, params, state)
        response = {"jsonrpc": "2.0", "result": result, "id": request_id}
    except Exception as exc:  # noqa: BLE001
        state.last_error = str(exc)
        response = {
            "jsonrpc": "2.0",
            "error": {"code": 500, "message": state.last_error},
            "id": request_id,
        }
    return web.json_response(response)


def _extract_token(request: web.Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.removeprefix("Bearer ").strip()
    return request.headers.get("X-Auth-Token", "")


async def dispatch(method: str | None, params: dict[str, Any], state: WalletdState) -> Any:
    if method == "walletd.health":
        return {"status": "ok"}
    if method == "walletd.version":
        return {"version": _resolve_version()}
    if method == "walletd.getStatus":
        return {
            "node_running": False,
            "pid": os.getpid(),
            "rpc_url": state.rpc_url,
            "last_error": state.last_error,
        }
    if method == "walletd.getLogsTail":
        lines = int(params.get("lines", 200))
        return {"lines": _tail_log(state.log_path, max(1, min(lines, 1000)))}
    raise ValueError(f"Unknown method: {method}")


def _tail_log(log_path: Path, lines: int) -> list[str]:
    if not log_path.exists():
        return []
    content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return content[-lines:]


def _resolve_version() -> str:
    try:
        from importlib.metadata import version

        return version("animica-qt-wallet")
    except Exception:  # noqa: BLE001
        return "0.0.0"


def create_app(state: WalletdState) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_post("/", handle_rpc)
    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Animica walletd service")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_dir = resolve_data_dir(args.data_dir)
    token = load_or_create_token(data_dir)
    port = resolve_port(args.port)
    rpc_url = f"http://127.0.0.1:{port}"
    log_path = resolve_log_path(data_dir)
    _setup_logging(log_path)
    logging.getLogger(__name__).info("Starting walletd on %s", rpc_url)

    state = WalletdState(token=token, rpc_url=rpc_url, log_path=log_path)
    app = create_app(state)
    web.run_app(app, host="127.0.0.1", port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
