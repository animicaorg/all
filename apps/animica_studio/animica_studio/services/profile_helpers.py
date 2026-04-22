"""Helpers for active profile RPC resolution and local-node detection."""

from __future__ import annotations

from urllib.parse import urlparse

from animica_studio.models.profile_models import RpcProfile
from animica_studio.storage.config import Config

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0"}


def get_active_rpc_url(config: Config) -> str:
    active_id = config.active_profile_id
    for d in config.rpc_profiles:
        if isinstance(d, dict) and d.get("id") == active_id:
            try:
                profile = RpcProfile.from_dict(d)
                url = profile.effective_rpc_url()
            except Exception:  # noqa: BLE001
                url = str(d.get("rpc_url") or d.get("node_rpc_url") or "")
            if isinstance(url, str) and url:
                return url
    try:
        return config.get_active_profile().rpc_url
    except Exception:  # noqa: BLE001
        return ""


def is_local_rpc_url(rpc_url: str) -> bool:
    if not rpc_url:
        return False
    try:
        host = (urlparse(rpc_url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return False
    return host in _LOCAL_HOSTS
