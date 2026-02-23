"""DA readiness/status service for Studio pages."""

from __future__ import annotations

import logging
from typing import Any

from animica_studio.services.profile_helpers import get_active_rpc_url
from animica_studio.services.rpc_client import RpcClient, RpcResponseError
from animica_studio.storage.config import Config

log = logging.getLogger(__name__)


class DaStatusService:
    """Query and configure node DA status using RPC-first strategy."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def _rpc_url(self, override: str | None = None) -> str:
        raw = override or get_active_rpc_url(self._config) or self._config.get_active_profile().node.rpc_local_url
        raw = raw.rstrip("/")
        if not raw.endswith("/rpc"):
            raw += "/rpc"
        return raw

    def _client(self, override: str | None = None) -> RpcClient:
        return RpcClient(self._rpc_url(override), connect_timeout=4.0, read_timeout=15.0, max_retries=2)

    def get_status(self, rpc_url: str | None = None) -> dict[str, Any]:
        client = self._client(rpc_url)
        try:
            registry = client.registry()
            put_method = registry.resolve_any(["da_putBlob", "da.putBlob"])
            get_method = registry.resolve_any(["da_getBlob", "da.getBlob"])
            configure_method = registry.resolve_any(["da_configure", "da.configure"])
            if not isinstance(configure_method, str) or not configure_method:
                configure_method = None
            status_method = registry.resolve_any(["da_getStatus", "da.getStatus", "da_status", "da.status"])
            payload: dict[str, Any] | None = None
            if status_method:
                try:
                    payload = client.call_with_schema(status_method, {})
                except RpcResponseError as exc:
                    if exc.rpc_error.code not in (-32601, -32602):
                        raise
            enabled = bool((payload or {}).get("enabled", False))
            allow_remote_put = bool((payload or {}).get("allow_remote_put", True))
            return {
                "ok": enabled and bool(put_method),
                "enabled": enabled,
                "configured_dir": str((payload or {}).get("dir") or ""),
                "effective_mode": str((payload or {}).get("on_full") or (payload or {}).get("eviction_policy") or ""),
                "effective_limit": int((payload or {}).get("max_bytes") or 0),
                "server_version": self.get_server_version(rpc_url),
                "rpc_url": self._rpc_url(rpc_url),
                "raw": payload,
                "allow_remote_put": allow_remote_put,
                "last_error": str((payload or {}).get("last_error") or ""),
                "da_methods": {
                    "put_blob": put_method,
                    "get_blob": get_method,
                    "configure": configure_method,
                    "status": status_method,
                },
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "enabled": False,
                "configured_dir": "",
                "effective_mode": "",
                "effective_limit": 0,
                "server_version": self.get_server_version(rpc_url),
                "rpc_url": self._rpc_url(rpc_url),
                "last_error": str(exc),
            }
        finally:
            client.close()

    def get_server_version(self, rpc_url: str | None = None) -> str:
        client = self._client(rpc_url)
        try:
            for method in ("web3_clientVersion", "node.version", "system.version"):
                try:
                    out = client.call(method)
                    return str(out)
                except RpcResponseError as exc:
                    if exc.rpc_error.code in (-32601, -32602):
                        continue
                    raise
            return "unknown"
        except Exception:
            return "unknown"
        finally:
            client.close()

    def enable_da(self, dir_path: str, limit_bytes: int, mode: str = "quota", rpc_url: str | None = None) -> dict[str, Any]:
        client = self._client(rpc_url)
        try:
            before = self.get_status(rpc_url)
            if before.get("enabled"):
                return {"ok": True, "response": {"noop": True}, "status": before, "method": before.get("da_methods", {}).get("configure")}

            registry = client.registry()
            configure_method = registry.resolve_any(["da_configure", "da.configure"])
            if not isinstance(configure_method, str) or not configure_method:
                return {"ok": False, "error": "DA configure method not exposed by node.", "status": before}

            spec = client.get_param_spec(configure_method)
            payload: dict[str, Any] = {}
            for p in spec:
                name = p.get("name")
                if name == "enabled":
                    payload["enabled"] = True
                elif name == "dir":
                    payload["dir"] = dir_path
                elif name in {"max_bytes", "limit_bytes"}:
                    payload[name] = int(limit_bytes)
                elif name in {"on_full", "mode"}:
                    payload[name] = "evict" if mode == "quota" else "reject"
                elif p.get("required"):
                    return {"ok": False, "error": f"Unsupported required DA configure param: {name}", "status": before}
            if not payload:
                payload = {"enabled": True}

            response = client.call_with_schema(configure_method, payload)
            check = self.get_status(rpc_url)
            if not check.get("enabled"):
                return {
                    "ok": False,
                    "error": "Node did not enable DA",
                    "response": response,
                    "status": check,
                    "method": configure_method,
                }
            return {"ok": True, "response": response, "status": check, "method": configure_method}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "status": self.get_status(rpc_url)}
        finally:
            client.close()
