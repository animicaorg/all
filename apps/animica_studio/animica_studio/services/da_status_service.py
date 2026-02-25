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
            default_dir_method = registry.resolve_any(["da.getDefaultDir", "da_getDefaultDir"])
            allowed_dirs_method = registry.resolve_any(["da.getAllowedBaseDirs", "da_getAllowedBaseDirs"])
            if not isinstance(configure_method, str) or not configure_method:
                configure_method = None
            configure_param_spec = client.get_param_spec(configure_method) if configure_method else []
            status_method = registry.resolve_any(["da_getStatus", "da.getStatus", "da_status", "da.status"])
            da_found_methods = registry.dump_methods("da")
            payload: dict[str, Any] | None = None
            if status_method:
                try:
                    payload = client.call_with_schema(status_method, {})
                except RpcResponseError as exc:
                    if exc.rpc_error.code not in (-32601, -32602):
                        raise
            enabled = bool((payload or {}).get("enabled", False))
            allow_remote_put = bool((payload or {}).get("allow_remote_put", True))
            writable = bool((payload or {}).get("writable", False))
            status_ok = bool((payload or {}).get("ok", enabled and writable))
            reason = str((payload or {}).get("reason") or "")
            policy_blocked_reason = str((payload or {}).get("policy_blocked_reason") or "")
            default_dir = ""
            allowed_base_dirs: list[str] = []
            if default_dir_method:
                try:
                    out = client.call_with_schema(default_dir_method, {})
                    if isinstance(out, str):
                        default_dir = out
                    elif isinstance(out, dict):
                        default_dir = str(out.get("dir") or out.get("path") or "")
                except Exception:
                    default_dir = ""
            if allowed_dirs_method:
                try:
                    out = client.call_with_schema(allowed_dirs_method, {})
                    if isinstance(out, list):
                        allowed_base_dirs = [str(v) for v in out if isinstance(v, (str, bytes))]
                    elif isinstance(out, dict):
                        vals = out.get("dirs") if isinstance(out.get("dirs"), list) else out.get("allowed")
                        if isinstance(vals, list):
                            allowed_base_dirs = [str(v) for v in vals if isinstance(v, (str, bytes))]
                except Exception:
                    allowed_base_dirs = []
            return {
                "ok": status_ok and bool(put_method),
                "enabled": enabled,
                "writable": writable,
                "reason": reason,
                "policy_blocked_reason": policy_blocked_reason,
                "configured_dir": str((payload or {}).get("dir") or ""),
                "default_dir": default_dir,
                "allowed_base_dirs": allowed_base_dirs,
                "effective_mode": str((payload or {}).get("on_full") or (payload or {}).get("eviction_policy") or ""),
                "effective_limit": int((payload or {}).get("max_bytes") or 0),
                "server_version": self.get_server_version(rpc_url),
                "rpc_url": self._rpc_url(rpc_url),
                "raw": payload,
                "allow_remote_put": allow_remote_put,
                "last_error": str((payload or {}).get("last_error") or ""),
                "da_found_methods": da_found_methods,
                "da_methods": {
                    "put_blob": put_method,
                    "get_blob": get_method,
                    "configure": configure_method,
                    "status": status_method,
                    "default_dir": default_dir_method,
                    "allowed_base_dirs": allowed_dirs_method,
                },
                "configure_param_spec": configure_param_spec,
                "configure_param_structure": ((getattr(registry, "get_method_meta", lambda *_a, **_k: {})(configure_method).get("param_structure")) if configure_method else "unknown"),
                "configure_method_raw": ((getattr(registry, "get_method_meta", lambda *_a, **_k: {})(configure_method).get("raw")) if configure_method and not configure_param_spec else None),
                "can_configure_allow_remote_put": any(p.get("name") == "allow_remote_put" for p in configure_param_spec if isinstance(p, dict)),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "enabled": False,
                "reason": "rpc_error",
                "configured_dir": "",
                "effective_mode": "",
                "effective_limit": 0,
                "server_version": self.get_server_version(rpc_url),
                "rpc_url": self._rpc_url(rpc_url),
                "last_error": str(exc),
            }
        finally:
            client.close()

    @staticmethod
    def _is_dir_allowed(dir_path: str, allowed_base_dirs: list[str]) -> bool:
        if not dir_path:
            return False
        if not allowed_base_dirs:
            return True
        norm = dir_path.rstrip("/")
        for base in allowed_base_dirs:
            b = str(base).rstrip("/")
            if not b:
                continue
            if norm == b or norm.startswith(f"{b}/"):
                return True
        return False

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
            if before.get("enabled") and (before.get("ok") or before.get("writable")):
                return {"ok": True, "response": {"noop": True}, "status": before, "method": before.get("da_methods", {}).get("configure")}

            registry = client.registry()
            configure_method = registry.resolve_any(["da_configure", "da.configure"])
            if not isinstance(configure_method, str) or not configure_method:
                return {"ok": False, "error": "DA configure method not exposed by node.", "status": before}

            spec = client.get_param_spec(configure_method)
            status_raw = before.get("raw") if isinstance(before.get("raw"), dict) else {}
            default_dir = str(before.get("default_dir") or "")
            allowed_base_dirs = before.get("allowed_base_dirs") if isinstance(before.get("allowed_base_dirs"), list) else []
            candidate_dir = str(dir_path or status_raw.get("dir") or default_dir or "/data/da")
            if not self._is_dir_allowed(candidate_dir, allowed_base_dirs):
                if default_dir and self._is_dir_allowed(default_dir, allowed_base_dirs):
                    candidate_dir = default_dir
                elif allowed_base_dirs:
                    candidate_dir = str(allowed_base_dirs[0])

            values: dict[str, Any] = {
                "enabled": True,
                "dir": candidate_dir,
                "max_bytes": int(limit_bytes),
                "on_full": "evict" if mode == "quota" else "reject",
                "mode": "evict" if mode == "quota" else "reject",
            }
            if isinstance(status_raw, dict) and "allow_remote_put" in status_raw:
                values["allow_remote_put"] = bool(status_raw.get("allow_remote_put"))

            attempts: list[tuple[str, Any]] = []
            if spec:
                ordered: list[Any] = []
                for p in spec:
                    name = p.get("name") if isinstance(p, dict) else None
                    if isinstance(name, str) and name in values:
                        ordered.append(values[name])
                attempts.append(("object", values))
                if ordered:
                    attempts.append(("positional", ordered))
            else:
                attempts.append(("object", values))

            response = None
            used_encoding = "object"
            last_error = ""
            for encoding, payload in attempts:
                try:
                    if encoding == "object":
                        response = client.call(configure_method, payload)
                    else:
                        response = client.call(configure_method, payload)
                    used_encoding = encoding
                    last_error = ""
                    break
                except RpcResponseError as exc:
                    last_error = str(exc)
                    msg = (exc.rpc_error.message or "").lower()
                    if exc.rpc_error.code == -32602 and ("missing" in msg or "unexpected keyword" in msg):
                        continue
                    raise

            check = self.get_status(rpc_url)
            if not check.get("enabled"):
                reason = check.get("reason") or check.get("policy_blocked_reason") or last_error or "unknown"
                return {
                    "ok": False,
                    "error": f"Node did not enable DA ({reason})",
                    "response": response,
                    "status": check,
                    "method": configure_method,
                    "param_encoding": used_encoding,
                }
            if check.get("ok") is not True and check.get("writable", True) is not True:
                reason = check.get("policy_blocked_reason") or check.get("reason") or last_error or "not_writable"
                return {
                    "ok": False,
                    "error": f"DA enabled but not writable ({reason})",
                    "response": response,
                    "status": check,
                    "method": configure_method,
                    "param_encoding": used_encoding,
                }
            return {
                "ok": True,
                "response": response,
                "status": check,
                "method": configure_method,
                "payload": values,
                "param_encoding": used_encoding,
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "status": self.get_status(rpc_url)}
        finally:
            client.close()
