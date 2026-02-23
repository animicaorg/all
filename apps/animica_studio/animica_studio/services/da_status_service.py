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
        status_methods = ("da.status", "da.getStatus", "da_status", "da_getStatus")
        try:
            last_error: str | None = None
            payload: dict[str, Any] | None = None
            for method in status_methods:
                try:
                    payload = client.call(method, [{}])
                    break
                except RpcResponseError as exc:
                    if exc.rpc_error.code in (-32601, -32602):
                        last_error = str(exc)
                        continue
                    raise
            if payload is None:
                return {
                    "ok": False,
                    "enabled": False,
                    "configured_dir": "",
                    "effective_mode": "",
                    "effective_limit": 0,
                    "server_version": self.get_server_version(rpc_url),
                    "rpc_url": self._rpc_url(rpc_url),
                    "last_error": last_error or "DA status method unavailable",
                }
            enabled = bool(payload.get("enabled", False))
            return {
                "ok": True,
                "enabled": enabled,
                "configured_dir": str(payload.get("dir") or ""),
                "effective_mode": str(payload.get("on_full") or payload.get("eviction_policy") or ""),
                "effective_limit": int(payload.get("max_bytes") or 0),
                "server_version": self.get_server_version(rpc_url),
                "rpc_url": self._rpc_url(rpc_url),
                "raw": payload,
                "last_error": str(payload.get("last_error") or ""),
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
        payload = {
            "enabled": True,
            "dir": dir_path,
            "max_bytes": int(limit_bytes),
            "on_full": "evict" if mode == "quota" else "reject",
        }
        log.info("DA enable payload rpc_url=%s payload=%s", self._rpc_url(rpc_url), payload)
        try:
            response = client.call("da.configure", [payload])
            log.info("DA enable response: %s", response)
            check = self.get_status(rpc_url)
            return {"ok": bool(check.get("enabled")), "response": response, "status": check}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "status": self.get_status(rpc_url)}
        finally:
            client.close()
