from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .artifacts import deployments_root

DEPLOYMENT_INDEX_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def network_key(*, chain_id: Optional[int] = None, network: Optional[str] = None) -> str:
    if network and network.strip():
        safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in network.strip())
        if chain_id is not None:
            return f"{safe}-chain-{int(chain_id)}"
        return safe
    if chain_id is not None:
        return f"chain-{int(chain_id)}"
    return "unknown"


def _network_dir(key: str) -> Path:
    return (deployments_root() / key).resolve()


def _index_path(key: str) -> Path:
    return _network_dir(key) / "index.json"


def _load_index(key: str) -> dict[str, Any]:
    path = _index_path(key)
    if not path.exists():
        return {"version": DEPLOYMENT_INDEX_VERSION, "deployments": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": DEPLOYMENT_INDEX_VERSION, "deployments": []}
    if not isinstance(data, dict):
        return {"version": DEPLOYMENT_INDEX_VERSION, "deployments": []}
    if not isinstance(data.get("deployments"), list):
        data["deployments"] = []
    data.setdefault("version", DEPLOYMENT_INDEX_VERSION)
    return data


def _save_index(key: str, payload: dict[str, Any]) -> None:
    path = _index_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _normalize_record(record: dict[str, Any], *, key: str) -> dict[str, Any]:
    out = dict(record)
    out.setdefault("created_at", _utc_now_iso())
    out.setdefault("network", key)
    return out


def save_deployment_record(record: dict[str, Any], *, key: str) -> None:
    normalized = _normalize_record(record, key=key)
    index = _load_index(key)
    deployments = index.get("deployments")
    if not isinstance(deployments, list):
        deployments = []
        index["deployments"] = deployments

    name = str(normalized.get("name") or "").strip().lower()
    address = str(normalized.get("address") or "").strip().lower()

    replaced = False
    for idx, existing in enumerate(deployments):
        if not isinstance(existing, dict):
            continue
        existing_name = str(existing.get("name") or "").strip().lower()
        existing_address = str(existing.get("address") or "").strip().lower()
        if name and existing_name == name:
            deployments[idx] = normalized
            replaced = True
            break
        if address and existing_address and existing_address == address:
            deployments[idx] = normalized
            replaced = True
            break

    if not replaced:
        deployments.append(normalized)

    _save_index(key, index)


def _list_from_key(key: str) -> list[dict[str, Any]]:
    index = _load_index(key)
    deployments = index.get("deployments")
    if not isinstance(deployments, list):
        return []
    out: list[dict[str, Any]] = []
    for item in deployments:
        if isinstance(item, dict):
            normalized = dict(item)
            normalized.setdefault("network", key)
            out.append(normalized)
    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out


def list_deployments(*, key: Optional[str] = None) -> list[dict[str, Any]]:
    if key:
        return _list_from_key(key)
    root = deployments_root()
    out: list[dict[str, Any]] = []
    if not root.exists():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        out.extend(_list_from_key(child.name))
    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out


def resolve_deployment(identifier: str, *, key: Optional[str] = None) -> Optional[dict[str, Any]]:
    needle = identifier.strip().lower()
    if not needle:
        return None

    candidates = list_deployments(key=key)
    for item in candidates:
        name = str(item.get("name") or "").strip().lower()
        address = str(item.get("address") or "").strip().lower()
        if needle == name or needle == address:
            return item
    return None


def deployment_index_path(*, key: str) -> Path:
    return _index_path(key)
