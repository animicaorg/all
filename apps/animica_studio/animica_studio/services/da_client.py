from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from animica_studio.services.rpc_client import RpcClient, RpcResponseError, RpcTransportError


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

    def upload_bytes(self, data: bytes, namespace: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"data": base64.b64encode(data).decode("utf-8")}
        if namespace:
            payload["namespace"] = namespace
        result = self._call_multi(("da_putBlob", "da.putBlob"), [payload])
        return {"blob_id": result, "sha256": hashlib.sha256(data).hexdigest()}

    def upload_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.upload_bytes(json.dumps(payload, sort_keys=True).encode("utf-8"))

    def get_blob(self, blob_id: str) -> bytes:
        out = self._call_multi(("da_getBlob", "da.getBlob"), [blob_id])
        if isinstance(out, dict) and "data" in out:
            return base64.b64decode(out["data"])
        if isinstance(out, str):
            try:
                return bytes.fromhex(out.removeprefix("0x"))
            except Exception:
                return out.encode("utf-8")
        if isinstance(out, (bytes, bytearray)):
            return bytes(out)
        raise RuntimeError("Unable to decode DA get blob response")

    def status(self) -> dict[str, Any]:
        return self._call_multi(("da_getStatus", "da.getStatus", "da_status", "da.status"), [{}])

    def configure(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._call_multi(("da.configure", "da_configure"), [params])
