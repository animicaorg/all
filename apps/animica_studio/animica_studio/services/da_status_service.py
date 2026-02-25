"""DA readiness/status service for Studio pages."""

from __future__ import annotations

import json
import logging
import shlex
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

    @staticmethod
    def _rpc_curl(rpc_url: str, method: str, params: Any) -> str:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, separators=(",", ":"))
        return f"curl -sS -X POST {shlex.quote(rpc_url)} -H 'Content-Type: application/json' --data-raw {shlex.quote(body)}"

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

    def _resolve_candidate_dir(self, before: dict[str, Any], requested_dir: str) -> str:
        status_raw = before.get("raw") if isinstance(before.get("raw"), dict) else {}
        default_dir = str(before.get("default_dir") or "")
        allowed_base_dirs = before.get("allowed_base_dirs") if isinstance(before.get("allowed_base_dirs"), list) else []
        effective_dir = str(status_raw.get("effective_dir") or status_raw.get("dir") or "")
        candidate_dir = str(requested_dir or effective_dir or default_dir or "/data/da")
        if self._is_dir_allowed(candidate_dir, allowed_base_dirs):
            return candidate_dir
        if effective_dir and self._is_dir_allowed(effective_dir, allowed_base_dirs):
            return effective_dir
        if default_dir and self._is_dir_allowed(default_dir, allowed_base_dirs):
            return default_dir
        if allowed_base_dirs:
            return str(allowed_base_dirs[0])
        return "/data/da"

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
            writable = bool((payload or {}).get("writable", False))
            status_ok = bool((payload or {}).get("ok", enabled and writable))
            allow_remote_put = bool((payload or {}).get("allow_remote_put", True))
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

            rpc_url_resolved = self._rpc_url(rpc_url)
            return {
                "ok": status_ok and bool(put_method),
                "enabled": enabled,
                "writable": writable,
                "reason": reason,
                "policy_blocked_reason": policy_blocked_reason,
                "configured_dir": str((payload or {}).get("dir") or ""),
                "effective_dir": str((payload or {}).get("effective_dir") or ""),
                "default_dir": default_dir,
                "allowed_base_dirs": allowed_base_dirs,
                "effective_mode": str((payload or {}).get("on_full") or (payload or {}).get("eviction_policy") or ""),
                "effective_limit": int((payload or {}).get("max_bytes") or 0),
                "server_version": self.get_server_version(rpc_url),
                "rpc_url": rpc_url_resolved,
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
                "curl_get_status": self._rpc_curl(rpc_url_resolved, status_method or "da.getStatus", {}),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "enabled": False,
                "writable": False,
                "reason": "unreachable",
                "policy_blocked_reason": "",
                "configured_dir": "",
                "effective_dir": "",
                "default_dir": "",
                "allowed_base_dirs": [],
                "effective_mode": "",
                "effective_limit": 0,
                "server_version": "unknown",
                "rpc_url": self._rpc_url(rpc_url),
                "raw": {},
                "allow_remote_put": False,
                "last_error": str(exc),
                "da_found_methods": [],
                "da_methods": {},
                "configure_param_spec": [],
                "configure_param_structure": "unknown",
                "configure_method_raw": None,
                "can_configure_allow_remote_put": False,
                "curl_get_status": self._rpc_curl(self._rpc_url(rpc_url), "da.getStatus", {}),
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
            if before.get("enabled") and before.get("ok"):
                return {
                    "ok": True,
                    "response": {"noop": True},
                    "status": before,
                    "method": before.get("da_methods", {}).get("configure"),
                    "param_encoding": "object",
                }

            registry = client.registry()
            configure_method = registry.resolve_any(["da_configure", "da.configure"])
            if not isinstance(configure_method, str) or not configure_method:
                return {"ok": False, "error": "DA configure method not exposed by node.", "status": before}

            spec = client.get_param_spec(configure_method)
            candidate_dir = self._resolve_candidate_dir(before, dir_path)
            values: dict[str, Any] = {
                "enabled": True,
                "dir": candidate_dir,
                "max_bytes": int(limit_bytes),
                "limit_bytes": int(limit_bytes),
                "mode": str(mode),
            }

            attempts: list[tuple[str, Any]] = [("object", values)]
            if spec:
                ordered: list[Any] = []
                for p in spec:
                    name = p.get("name") if isinstance(p, dict) else None
                    if isinstance(name, str) and name in values:
                        ordered.append(values[name])
                if ordered:
                    attempts.append(("positional", ordered))

            response = None
            used_encoding = "object"
            last_error = ""
            for encoding, payload in attempts:
                try:
                    response = client.call(configure_method, payload)
                    used_encoding = encoding
                    break
                except RpcResponseError as exc:
                    last_error = str(exc)
                    msg = (exc.rpc_error.message or "").lower()
                    if encoding == "object" and exc.rpc_error.code == -32602 and ("missing" in msg or "unexpected keyword" in msg):
                        continue
                    raise

            check = self.get_status(rpc_url)
            if not check.get("enabled") or not check.get("ok"):
                reason = check.get("reason") or check.get("policy_blocked_reason") or last_error or "unknown"
                return {
                    "ok": False,
                    "error": f"Node refused to configure DA ({reason})",
                    "response": response,
                    "status": check,
                    "method": configure_method,
                    "param_encoding": used_encoding,
                    "request_payload": values,
                    "curl_configure": self._rpc_curl(self._rpc_url(rpc_url), configure_method, values),
                    "curl_get_status": check.get("curl_get_status") or self._rpc_curl(self._rpc_url(rpc_url), "da.getStatus", {}),
                }

            return {
                "ok": True,
                "response": response,
                "status": check,
                "method": configure_method,
                "payload": values,
                "param_encoding": used_encoding,
                "request_payload": values,
                "curl_configure": self._rpc_curl(self._rpc_url(rpc_url), configure_method, values),
                "curl_get_status": check.get("curl_get_status") or self._rpc_curl(self._rpc_url(rpc_url), "da.getStatus", {}),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": str(exc),
                "status": self.get_status(rpc_url),
                "curl_get_status": self._rpc_curl(self._rpc_url(rpc_url), "da.getStatus", {}),
            }
        finally:
            client.close()
