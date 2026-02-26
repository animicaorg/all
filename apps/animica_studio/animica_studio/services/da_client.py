from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import time
from base64 import b64encode
from pathlib import Path
from typing import Any

from animica_studio.services.rpc_client import RpcClient, RpcParseError, RpcResponseError, RpcTransportError

log = logging.getLogger(__name__)

_DA_PARAM_ENCODING_BY_URL: dict[str, dict[str, str]] = {}


class DaUploadError(RuntimeError):
    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class DaClient:
    def __init__(self, rpc_url: str) -> None:
        self.rpc_url = rpc_url.rstrip("/")
        if not self.rpc_url.endswith("/rpc"):
            self.rpc_url += "/rpc"
        self._param_encoding = _DA_PARAM_ENCODING_BY_URL.setdefault(self.rpc_url, {})

    @staticmethod
    def _parse_put_blob_meta(method: str, meta: dict[str, Any]) -> dict[str, Any]:
        raw = meta.get("raw") if isinstance(meta.get("raw"), dict) else {}
        params_raw = raw.get("params")
        param_structure = str(meta.get("param_structure") or "unknown")
        param_spec = meta.get("params") if isinstance(meta.get("params"), list) else []
        names = [str(p.get("name") or "") for p in param_spec if isinstance(p, dict)]
        schema_encoding = "unknown"
        expected_len = 0
        expected_keys: set[str] = set()

        if isinstance(params_raw, list):
            normalized = [p for p in params_raw if isinstance(p, dict)]
            names = [str(p.get("name") or "") for p in normalized]
            if (
                len(normalized) == 2
                and names[:2] == ["namespace", "data"]
                and param_structure != "object"
            ):
                schema_encoding = "positional"
                expected_len = 2
            elif len(normalized) == 2 and names[:2] == ["namespace", "data"] and param_structure == "object":
                schema_encoding = "object"
                expected_keys = {"namespace", "data"}
            elif len(normalized) == 1:
                p0 = normalized[0]
                schema = p0.get("schema") if isinstance(p0.get("schema"), dict) else {}
                props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
                prop_keys = set(str(k) for k in props)
                if {"namespace", "data"}.issubset(prop_keys):
                    schema_encoding = "object"
                    expected_keys = {"namespace", "data"}
        elif isinstance(params_raw, dict):
            schema = params_raw.get("schema") if isinstance(params_raw.get("schema"), dict) else {}
            props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
            prop_keys = set(str(k) for k in props)
            if {"namespace", "data"}.issubset(prop_keys):
                schema_encoding = "object"
                expected_keys = {"namespace", "data"}

        return {
            "method": method,
            "param_structure": param_structure,
            "param_spec": names,
            "schema_encoding": schema_encoding,
            "expected_len": expected_len,
            "expected_keys": sorted(expected_keys),
        }

    @staticmethod
    def _is_method_unavailable_error(exc: Exception) -> bool:
        """Return ``True`` when the RPC error indicates unknown/unavailable method."""
        if isinstance(exc, RpcResponseError):
            code = exc.rpc_error.code
            msg = (exc.rpc_error.message or "").lower()
            return code == -32601 or "method not found" in msg or "not available" in msg
        return isinstance(exc, RpcTransportError)

    def _call_multi(self, methods: tuple[str, ...], params: list[Any] | dict[str, Any]) -> Any:
        c = RpcClient(self.rpc_url, connect_timeout=3.0, read_timeout=10.0, max_retries=1)
        failures: list[str] = []
        try:
            if hasattr(c, "resolve_method"):
                try:
                    method = c.resolve_method(methods[0], list(methods))
                    if isinstance(params, list) and params and isinstance(params[0], dict):
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
    def _build_upload_diagnostics(
        *,
        method: str,
        namespace: int,
        data_hex: str,
        server_info: dict[str, Any],
        schema: dict[str, Any],
        chosen_encoding: str,
        attempt_log: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "resolved_method": method,
            "param_spec": schema.get("param_spec", []),
            "schema_encoding": schema.get("schema_encoding", "unknown"),
            "chosen_encoding": chosen_encoding,
            "expected_arity": schema.get("expected_len", 0),
            "params_len": 2,
            "params": [namespace, f"<hex:{max((len(data_hex)-2)//2, 0)} bytes>"],
            "namespace": namespace,
            "data_hex_length": len(data_hex),
            "server_version": server_info.get("version") if isinstance(server_info, dict) else None,
            "attempts": attempt_log or [],
        }

    @staticmethod
    def _build_da_put_blob_params(namespace_int: int, data_hex: str) -> list[Any]:
        params: list[Any] = [namespace_int, data_hex]
        assert isinstance(params, list)
        assert len(params) == 2
        assert all(not isinstance(p, list) for p in params)
        return params

    @staticmethod
    def _build_da_dot_put_blob_params(raw_bytes: bytes, namespace_label: str, metadata: dict[str, Any]) -> dict[str, Any]:
        params_obj: dict[str, Any] = {
            "bytes": b64encode(raw_bytes).decode("ascii"),
            "namespace": namespace_label,
            "metadata": metadata,
        }
        assert isinstance(params_obj, dict)
        assert "bytes" in params_obj
        return params_obj

    @staticmethod
    def _build_da_ingest_local_params(path: str, namespace_int: int) -> dict[str, Any]:
        params_obj: dict[str, Any] = {"path": path, "namespace": namespace_int}
        assert isinstance(params_obj, dict)
        assert "path" in params_obj
        return params_obj

    @staticmethod
    def _retry_encoding_for_error(message: str, current_encoding: str) -> str | None:
        lowered = message.lower()
        if "too many positional arguments" in lowered and current_encoding != "object":
            return "object"
        if "missing required params: namespace" in lowered and current_encoding != "positional":
            return "positional"
        return None

    @staticmethod
    def _log_upload_attempt(method: str, encoding: str, params: list[Any] | dict[str, Any]) -> None:
        if isinstance(params, list):
            log.debug("DA upload method=%s encoding=%s params_type=list params_len=%d", method, encoding, len(params))
            return
        log.debug(
            "DA upload method=%s encoding=%s params_type=dict params_keys=%s",
            method,
            encoding,
            sorted(params.keys()),
        )

    @staticmethod
    def _metadata_payload(raw_bytes: bytes, content_type: str | None, tags: dict[str, Any] | None) -> bytes:
        if not content_type and not tags:
            return raw_bytes
        envelope = {
            "content_type": content_type,
            "tags": tags or {},
            "payload_b64": b64encode(raw_bytes).decode("ascii"),
        }
        return json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")

    @staticmethod
    def _extract_status_flags(status: Any) -> tuple[bool, bool | None]:
        if not isinstance(status, dict):
            return False, None
        enabled = bool(status.get("enabled", True))
        allow_remote_put = status.get("allow_remote_put")
        return enabled, bool(allow_remote_put) if isinstance(allow_remote_put, bool) else None

    def upload_blob(
        self,
        namespace: int,
        raw_bytes: bytes,
        content_type: str | None = None,
        tags: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        namespace_int = self._parse_namespace(namespace)
        metadata: dict[str, Any] = {}
        if content_type:
            metadata["content_type"] = content_type
        if tags:
            metadata["tags"] = dict(tags)

        c = RpcClient(self.rpc_url, connect_timeout=3.0, read_timeout=10.0, max_retries=1)
        try:
            registry = c.registry()
            status: Any = {}
            for status_method in ("da.getStatus", "da.status", "da_getStatus", "da_status"):
                if registry.has_method(status_method):
                    status = c.call(status_method, [])
                    break
            enabled, allow_remote_put = self._extract_status_flags(status)
            if not enabled:
                raise DaUploadError("DA is disabled", diagnostics={"status": status})

            if allow_remote_put is True and registry.has_method("da.putBlob"):
                params = self._build_da_dot_put_blob_params(raw_bytes, "studio/checkpoint", metadata)
                self._log_upload_attempt("da.putBlob", "by-name", params)
                result = c.call("da.putBlob", params)
                return {"blob_id": result, "sha256": hashlib.sha256(raw_bytes).hexdigest()}

            if registry.has_method("da.ingestLocal") or registry.has_method("da_ingestLocal"):
                ingest_info = self.get_ingest_dir()
                ingest_dir = Path(str(ingest_info.get("dir") or "").strip() or tempfile.gettempdir())
                ingest_dir.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=ingest_dir, prefix="studio-upload-", suffix=".blob", delete=False) as fh:
                    fh.write(raw_bytes)
                    node_path = str(fh.name)
                method = "da.ingestLocal" if registry.has_method("da.ingestLocal") else "da_ingestLocal"
                params = self._build_da_ingest_local_params(node_path, namespace_int)
                self._log_upload_attempt(method, "by-name", params)
                result = c.call(method, params)
                return {"blob_id": result, "sha256": hashlib.sha256(raw_bytes).hexdigest()}

            payload = self._metadata_payload(raw_bytes, content_type, tags)
            data_hex = "0x" + payload.hex()
            self._validate_hex_data(data_hex)
            method = "da_putBlob" if registry.has_method("da_putBlob") else "da.putBlob"
            params = self._build_da_put_blob_params(namespace_int, data_hex)
            self._log_upload_attempt(method, "positional", params)
            result = c.call(method, params)
            return {"blob_id": result, "sha256": hashlib.sha256(raw_bytes).hexdigest()}
        finally:
            c.close()

    def upload_bytes(self, data: bytes, namespace: int | str | None = None) -> dict[str, Any]:
        namespace_int = self._parse_namespace(namespace)
        return self.upload_blob(namespace_int, bytes(data), None, None)

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

    def getStatus(self) -> dict[str, Any]:
        return self.status()

    def get_status(self) -> dict[str, Any]:
        return self.status()

    def configure(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._call_multi(("da.configure", "da_configure"), params)

    def has_blob(self, blob_id: str) -> bool:
        out = self._call_multi(("da.has", "da_has"), [blob_id])
        if isinstance(out, dict):
            return bool(out.get("exists"))
        return bool(out)

    def get_ingest_dir(self) -> dict[str, Any]:
        out = self._call_multi(("da.getIngestDir", "da_getIngestDir"), [])
        return out if isinstance(out, dict) else {"dir": str(out or "")}

    def ingest_local(self, node_path: str, namespace: int | str | None = None) -> dict[str, Any]:
        ns = self._parse_namespace(namespace)
        return self._call_multi(("da.ingestLocal", "da_ingestLocal"), {"path": node_path, "namespace": ns})

    def wait_for_blob(self, blob_id: str, *, timeout_s: float = 30.0, interval_s: float = 2.0) -> bool:
        deadline = time.monotonic() + max(timeout_s, 0.0)
        wait = max(interval_s, 0.1)
        while time.monotonic() <= deadline:
            if self.has_blob(blob_id):
                return True
            time.sleep(wait)
            wait = min(wait * 1.7, 5.0)
        return self.has_blob(blob_id)
