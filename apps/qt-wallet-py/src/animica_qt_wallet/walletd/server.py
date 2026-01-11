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
    resolve_wallet_path,
    resolve_node_log_path,
    resolve_port,
)
from animica_qt_wallet.walletd.node_manager import NodeManager, NodeStatus
from animica_qt_wallet.walletd.wallet_store import WalletStore


@dataclass
class WalletdState:
    token: str
    rpc_url: str
    log_path: Path
    node_manager: NodeManager
    wallet_store: WalletStore
    node_network: str = "mainnet"
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
        node_status = state.node_manager.status()
        return {
            "node_running": node_status.running,
            "pid": os.getpid(),
            "rpc_url": state.rpc_url,
            "node_rpc_url": node_status.rpc_url,
            "node_network": node_status.network,
            "last_error": state.last_error,
            "wallet_locked": state.wallet_store.is_locked,
            "wallet_initialized": state.wallet_store.is_initialized,
        }
    if method == "walletd.getLogsTail":
        lines = int(params.get("lines", 200))
        return {"lines": _tail_log(state.log_path, max(1, min(lines, 1000)))}
    if method == "node.start":
        network = params.get("network", state.node_network)
        extra_args = params.get("extra_args", [])
        if network not in {"mainnet", "testnet"}:
            raise ValueError("Unsupported network (expected mainnet or testnet)")
        if not isinstance(extra_args, list) or not all(isinstance(arg, str) for arg in extra_args):
            raise ValueError("extra_args must be a list of strings")
        state.node_network = network
        status = await state.node_manager.start(network, extra_args=extra_args)
        return _node_status_payload(status)
    if method == "node.stop":
        await state.node_manager.stop()
        return {"stopped": True}
    if method == "node.status":
        return _node_status_payload(state.node_manager.status())
    if method == "node.logsTail":
        lines = int(params.get("lines", 200))
        log_path = resolve_node_log_path(state.node_manager.data_dir, state.node_network)
        return {"lines": _tail_log(log_path, max(1, min(lines, 2000)))}
    if method == "node.rpcInfo":
        status = state.node_manager.status()
        return {
            "rpc_url": status.rpc_url,
            "network": status.network,
        }
    if method == "wallet.lock":
        state.wallet_store.lock()
        return {"locked": True}
    if method == "wallet.unlock":
        password = params.get("password")
        if not isinstance(password, str):
            raise ValueError("password must be a string")
        result = state.wallet_store.unlock(password)
        return {
            "unlocked": True,
            "initialized": result.get("initialized", False),
            "accounts": len(state.wallet_store.list_accounts())
            if not state.wallet_store.is_locked
            else 0,
        }
    if method == "wallet.listAccounts":
        return {"accounts": state.wallet_store.list_accounts()}
    if method == "wallet.createAccount":
        label = params.get("label")
        if label is not None and not isinstance(label, str):
            raise ValueError("label must be a string")
        return state.wallet_store.create_account(label)
    if method == "wallet.importAccount":
        label = params.get("label")
        if label is not None and not isinstance(label, str):
            raise ValueError("label must be a string")
        secret = params.get("secret")
        if not isinstance(secret, str):
            raise ValueError("secret must be a string")
        return state.wallet_store.import_account(label, secret)
    if method == "wallet.exportAccount":
        address = params.get("address")
        if not isinstance(address, str):
            raise ValueError("address must be a string")
        return state.wallet_store.export_account(address)
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


def _node_status_payload(status: NodeStatus) -> dict[str, Any]:
    return {
        "running": status.running,
        "pid": status.pid,
        "network": status.network,
        "rpc_url": status.rpc_url,
        "restarting": status.restarting,
        "last_exit_code": status.last_exit_code,
        "last_error": status.last_error,
        "backoff_seconds": status.backoff_seconds,
        "started_at": status.started_at,
    }


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

    state = WalletdState(
        token=token,
        rpc_url=rpc_url,
        log_path=log_path,
        node_manager=NodeManager(data_dir),
        wallet_store=WalletStore(resolve_wallet_path(data_dir)),
    )
    app = create_app(state)
    web.run_app(app, host="127.0.0.1", port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
