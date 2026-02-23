from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from animica_studio.services.rpc_client import RpcClient, RpcParseError, RpcResponseError, RpcTransportError

log = logging.getLogger(__name__)


class DaUploadError(RuntimeError):
    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class DaClient:
    def __init__(self, rpc_url: str) -> None:
        self.rpc_url = rpc_url.rstrip("/")
        if not self.rpc_url.endswith("/rpc"):
            self.rpc_url += "/rpc"

    @staticmethod
    def _is_method_unavailable_error(exc: Exception) -> bool:
        """Return ``True`` when the RPC error indicates unknown/unavailable method."""
        if isinstance(exc, RpcResponseError):
            code = exc.rpc_error.code
            msg = (exc.rpc_error.message or "").lower()
            return code == -32601 or "method not found" in msg or "not available" in msg
        return isinstance(exc, RpcTransportError)

    def _call_multi(self, methods: tuple[str, ...], params: list[Any]) -> Any:
        c = RpcClient(self.rpc_url, connect_timeout=3.0, read_timeout=10.0, max_retries=1)
        failures: list[str] = []
        try:
            if hasattr(c, "resolve_method"):
                try:
                    method = c.resolve_method(methods[0], list(methods))
                    if params and isinstance(params[0], dict):
                        return c.call_with_schema(method, params[0])
                    return c.call(method, params)
                except Exception as exc:  # noqa: BLE001
                    if not self._is_method_unavailable_error(exc):
                        raise
                    failures.append(str(exc))
            else:
                for method in methods:
                    try:
                        return c.call(method, params)
                    except Exception as exc:  # noqa: BLE001
                        if not self._is_method_unavailable_error(exc):
                            raise
                        failures.append(f"{method}: {exc}")
            details = "; ".join(failures) if failures else "no details"
            raise RuntimeError(f"DA RPC unavailable for methods: {', '.join(methods)} ({details})")
        finally:
            c.close()

    @staticmethod
    def _parse_namespace(namespace: int | str | None) -> int:
        if namespace is None or namespace == "":
            return 0
        if isinstance(namespace, bool):
            raise ValueError("Namespace must be an integer >= 0")
        try:
            value = int(namespace)
        except (TypeError, ValueError) as exc:
            raise ValueError("Namespace must be an integer >= 0") from exc
        if value < 0:
            raise ValueError("Namespace must be an integer >= 0")
        return value

    @staticmethod
    def _validate_hex_data(data_hex: str) -> None:
        if not isinstance(data_hex, str) or not data_hex.startswith("0x"):
            raise ValueError("Blob data must be 0x-prefixed hex")
        if (len(data_hex) - 2) % 2 != 0:
            raise ValueError("Blob data hex length must be even")

    @staticmethod
    def _build_upload_diagnostics(*, method: str, namespace: int, data_hex: str, server_info: dict[str, Any]) -> dict[str, Any]:
        return {
            "resolved_method": method,
            "params_len": 2,
            "params": [namespace, f"<hex:{max((len(data_hex)-2)//2, 0)} bytes>"],
            "namespace": namespace,
            "data_hex_length": len(data_hex),
            "server_version": server_info.get("version") if isinstance(server_info, dict) else None,
        }

    def upload_bytes(self, data: bytes, namespace: int | str | None = None) -> dict[str, Any]:
        namespace_int = self._parse_namespace(namespace)
        data_hex = "0x" + bytes(data).hex()
        self._validate_hex_data(data_hex)

        c = RpcClient(self.rpc_url, connect_timeout=3.0, read_timeout=10.0, max_retries=1)
        try:
            method = c.resolve_method("da_putBlob", ["da_putBlob", "da.putBlob"])
            spec = c.get_param_spec(method)
            if len(spec) < 2:
                raise RpcParseError(f"OpenRPC param spec for {method} is incomplete: expected namespace + data")
            first_name = str(spec[0].get("name") or "")
            second_name = str(spec[1].get("name") or "")
            if first_name.lower() != "namespace" or second_name.lower() != "data":
                raise RpcParseError(f"Unexpected param order for {method}: {[p.get('name') for p in spec]}")

            log.debug("DA putBlob resolved method=%s params=%s", method, [p.get("name") for p in spec])

            params = [namespace_int, data_hex]
            try:
                result = c.call(method, params)
            except RpcResponseError as exc:
                if exc.rpc_error.code == -32602:
                    server_info: dict[str, Any] = {}
                    try:
                        server_info = getattr(c.registry(), "server_info", {})
                    except Exception:  # noqa: BLE001
                        server_info = {}
                    diag = self._build_upload_diagnostics(
                        method=method,
                        namespace=namespace_int,
                        data_hex=data_hex,
                        server_info=server_info,
                    )
                    raise DaUploadError(f"Invalid DA upload params: {exc}", diagnostics=diag) from exc
                raise
        finally:
            c.close()
        return {"blob_id": result, "sha256": hashlib.sha256(data).hexdigest()}

    def upload_json(self, payload: dict[str, Any], namespace: int | str | None = None) -> dict[str, Any]:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return self.upload_bytes(encoded, namespace=namespace)

    def get_blob(self, blob_id: str) -> bytes:
        out = self._call_multi(("da_getBlob", "da.getBlob"), [blob_id])
        if isinstance(out, dict) and "data" in out:
            data = out["data"]
            if isinstance(data, str) and data.startswith("0x"):
                return bytes.fromhex(data.removeprefix("0x"))
            return str(data).encode("utf-8")
        if isinstance(out, str):
            try:
                return bytes.fromhex(out.removeprefix("0x"))
            except Exception:
                return out.encode("utf-8")
        if isinstance(out, (bytes, bytearray)):
            return bytes(out)
        raise RuntimeError("Unable to decode DA get blob response")

    def status(self) -> dict[str, Any]:
        return self._call_multi(("da_getStatus", "da.getStatus", "da_status", "da.status"), [])

    def configure(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._call_multi(("da.configure", "da_configure"), [params])
