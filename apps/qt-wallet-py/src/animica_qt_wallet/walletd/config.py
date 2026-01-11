from __future__ import annotations

import os
import secrets
from pathlib import Path

from animica_qt_wallet.core.paths import get_app_data_dir

DEFAULT_PORT = 17834
TOKEN_FILE_NAME = "walletd.token"
LOG_FILE_NAME = "walletd.log"
WALLET_FILE_NAME = "walletd.wallet"
TX_HISTORY_FILE_NAME = "tx_history.json"
NODE_DIR_NAME = "node"
NODE_LOG_FILE_NAME = "node.log"


def resolve_port(override: int | None = None) -> int:
    if override:
        return override
    env_port = os.getenv("ANIMICA_WALLETD_PORT")
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            return DEFAULT_PORT
    return DEFAULT_PORT


def resolve_data_dir(override: Path | None = None) -> Path:
    return override or get_app_data_dir()


def resolve_token_path(data_dir: Path) -> Path:
    return data_dir / TOKEN_FILE_NAME


def resolve_log_path(data_dir: Path) -> Path:
    return data_dir / LOG_FILE_NAME


def resolve_wallet_path(data_dir: Path) -> Path:
    return data_dir / WALLET_FILE_NAME


def resolve_tx_history_path(data_dir: Path) -> Path:
    return data_dir / TX_HISTORY_FILE_NAME


def resolve_approval_queue_path(data_dir: Path) -> Path:
    return data_dir / "approval-queue.json"


def resolve_app_allowlist_path(data_dir: Path) -> Path:
    return data_dir / "app-allowlist.json"


def resolve_node_data_dir(data_dir: Path, network: str) -> Path:
    return data_dir / NODE_DIR_NAME / network


def resolve_node_log_path(data_dir: Path, network: str) -> Path:
    return resolve_node_data_dir(data_dir, network) / NODE_LOG_FILE_NAME


def load_or_create_token(data_dir: Path) -> str:
    data_dir.mkdir(parents=True, exist_ok=True)
    token_path = resolve_token_path(data_dir)
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        _ensure_strict_permissions(token_path)
        return token
    token = secrets.token_urlsafe(32)
    token_path.write_text(token, encoding="utf-8")
    _ensure_strict_permissions(token_path)
    return token


def _ensure_strict_permissions(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except PermissionError:
        pass
