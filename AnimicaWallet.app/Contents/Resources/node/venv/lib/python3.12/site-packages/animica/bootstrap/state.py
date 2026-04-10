from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(slots=True)
class BootstrapState:
    manifest: dict[str, Any]
    seeds: list[str]
    fetched_at: Optional[int]
    checkpoint_height: Optional[int]
    checkpoint_hash: Optional[str]


def _resolve_chain_dir(chain_id: int, data_dir: str | None) -> Path:
    base = Path(os.path.expanduser(data_dir)) if data_dir else None
    if base is None:
        base = Path(os.environ.get("ANIMICA_DATA_DIR") or "~/.animica").expanduser()
    if base.name == f"chain-{chain_id}" or (base / "bootstrap.json").exists():
        return base
    return base / f"chain-{chain_id}"


def _bootstrap_state_path(chain_id: int, data_dir: str | None) -> Path:
    return _resolve_chain_dir(chain_id, data_dir) / "bootstrap.json"


def _normalize_hash(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        if cleaned.startswith("0x"):
            return cleaned
        return "0x" + cleaned
    return None


def _extract_checkpoint(payload: dict[str, Any]) -> tuple[Optional[int], Optional[str]]:
    checkpoint = payload.get("checkpoint")
    if isinstance(checkpoint, dict):
        height = checkpoint.get("height")
        block_hash = checkpoint.get("hash") or checkpoint.get("blockHash")
        if height is not None and block_hash:
            return int(height), _normalize_hash(block_hash)
    head = payload.get("head")
    if isinstance(head, dict):
        height = head.get("height") or head.get("number") or head.get("blockNumber")
        block_hash = head.get("hash") or head.get("blockHash")
        if height is not None and block_hash:
            return int(height), _normalize_hash(block_hash)
    manifest = payload.get("manifest")
    if isinstance(manifest, dict):
        head = manifest.get("head")
        if isinstance(head, dict):
            height = head.get("height") or head.get("number") or head.get("blockNumber")
            block_hash = head.get("hash") or head.get("blockHash")
            if height is not None and block_hash:
                return int(height), _normalize_hash(block_hash)
    return None, None


def load_bootstrap_state(
    chain_id: int, data_dir: str | None
) -> Optional[BootstrapState]:
    state_path = _bootstrap_state_path(chain_id, data_dir)
    if not state_path.exists():
        return None
    try:
        payload = json.loads(state_path.read_text())
    except Exception:
        return None
    manifest = payload.get("manifest") if isinstance(payload, dict) else None
    seeds = payload.get("seeds") if isinstance(payload, dict) else None
    fetched_at = payload.get("fetched_at") if isinstance(payload, dict) else None
    if not isinstance(manifest, dict):
        manifest = {}
    if not isinstance(seeds, list):
        seeds = []
    checkpoint_height, checkpoint_hash = _extract_checkpoint(payload)
    return BootstrapState(
        manifest=manifest,
        seeds=[str(s) for s in seeds if s],
        fetched_at=int(fetched_at) if isinstance(fetched_at, (int, float)) else None,
        checkpoint_height=checkpoint_height,
        checkpoint_hash=checkpoint_hash,
    )


def save_bootstrap_state(
    chain_id: int,
    data_dir: str | None,
    *,
    manifest: dict[str, Any] | None = None,
    seeds: list[str] | None = None,
    checkpoint: tuple[int, str] | None = None,
    fetched_at: int | None = None,
) -> Path:
    state_path = _bootstrap_state_path(chain_id, data_dir)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {}
    existing = load_bootstrap_state(chain_id, data_dir)
    if existing:
        payload["manifest"] = existing.manifest
        payload["seeds"] = existing.seeds
        payload["fetched_at"] = existing.fetched_at
        if existing.checkpoint_height is not None and existing.checkpoint_hash:
            payload["checkpoint"] = {
                "height": existing.checkpoint_height,
                "hash": existing.checkpoint_hash,
            }
    if manifest is not None:
        payload["manifest"] = manifest
    if seeds is not None:
        payload["seeds"] = [str(s) for s in seeds if s]
    if fetched_at is None:
        fetched_at = int(time.time())
    payload["fetched_at"] = int(fetched_at)
    if checkpoint is not None:
        payload["checkpoint"] = {"height": int(checkpoint[0]), "hash": checkpoint[1]}
    else:
        height, block_hash = _extract_checkpoint(payload)
        if height is not None and block_hash:
            payload["checkpoint"] = {"height": height, "hash": block_hash}
    state_path.write_text(json.dumps(payload, indent=2))
    return state_path


def get_bootstrap_checkpoint(
    chain_id: int, data_dir: str | None
) -> Optional[tuple[int, str]]:
    state = load_bootstrap_state(chain_id, data_dir)
    if state is None:
        return None
    if state.checkpoint_height is None or not state.checkpoint_hash:
        return None
    return state.checkpoint_height, state.checkpoint_hash


__all__ = [
    "BootstrapState",
    "load_bootstrap_state",
    "save_bootstrap_state",
    "get_bootstrap_checkpoint",
]
