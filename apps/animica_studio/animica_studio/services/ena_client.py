"""ENA client supporting local daemon, remote endpoint, and network RPC modes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import time
from typing import Any, Iterator

import requests

from animica_studio.services.rpc_client import RpcClient

log = logging.getLogger(__name__)


class EnaMode(str, Enum):
    LOCAL_DAEMON = "local_daemon"
    REMOTE_HTTP = "remote_http"
    NETWORK_RPC = "network_rpc"


@dataclass
class EnaError(Exception):
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False
    raw: Any = None

    def __str__(self) -> str:
        payload = {"code": self.code, "message": self.message, "details": self.details, "retryable": self.retryable}
        return json.dumps(payload, ensure_ascii=False)


@dataclass
class EnaProfile:
    mode: EnaMode = EnaMode.LOCAL_DAEMON
    endpoint: str = "http://127.0.0.1:8765"
    ws_endpoint: str = ""
    auth_token: str = ""
    model: str = "default"
    rpc_url: str = "http://127.0.0.1:8545/rpc"
    max_fee_per_call: int | None = None


class EnaClient:
    def __init__(self, profile: EnaProfile, *, timeout_s: float = 30.0, retries: int = 3) -> None:
        self._profile = profile
        self._timeout_s = timeout_s
        self._retries = max(1, retries)
        self._session = requests.Session()
        self._failures = 0
        self._circuit_open_until = 0.0

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._profile.auth_token:
            headers["Authorization"] = f"Bearer {self._profile.auth_token}"
        return headers

    def _check_circuit(self) -> None:
        if self._circuit_open_until > time.time():
            raise EnaError(code="circuit_open", message="ENA circuit breaker is open due to repeated failures", retryable=True)

    def _record_failure(self) -> None:
        self._failures += 1
        if self._failures >= 4:
            self._circuit_open_until = time.time() + 20

    def _record_success(self) -> None:
        self._failures = 0
        self._circuit_open_until = 0.0

    def ping(self) -> dict[str, Any]:
        if self._profile.mode == EnaMode.NETWORK_RPC:
            return self._rpc_ping()
        url = self._profile.endpoint.rstrip("/") + "/health"
        payload = self._request_json("GET", url)
        return {
            "ok": bool(payload.get("ok", True)),
            "version": payload.get("version", "unknown"),
            "capabilities": payload.get("capabilities", {}),
        }

    def _rpc_ping(self) -> dict[str, Any]:
        client = RpcClient(self._profile.rpc_url)
        try:
            methods = client._known_methods()  # noqa: SLF001
            has_ena = any(m in methods for m in ("ena.call", "ena_call", "ena.chat", "ena_chat", "ena.stream", "ena_stream"))
            if not has_ena:
                return {"ok": False, "version": "n/a", "capabilities": {}, "reason": "RPC ENA methods not discovered"}
            version = "unknown"
            caps: dict[str, Any] = {"network_rpc": True}
            return {"ok": True, "version": version, "capabilities": caps, "methods": sorted(methods)}
        finally:
            client.close()

    def chat_stream(self, messages: list[dict[str, str]], model: str, tools: list[dict[str, Any]] | None, context: dict[str, Any]) -> Iterator[dict[str, Any]]:
        if self._profile.mode == EnaMode.NETWORK_RPC:
            yield from self._rpc_chat_stream(messages, model, tools, context)
            return
        ws_err: EnaError | None = None
        if self._profile.ws_endpoint:
            try:
                yield from self._ws_chat_stream(messages, model, tools, context)
                return
            except EnaError as exc:
                ws_err = exc
                log.warning("ENA websocket failed, falling back to HTTP stream: %s", exc)
        try:
            yield from self._http_chat_stream(messages, model, tools, context)
        except EnaError as exc:
            if ws_err:
                exc.details["ws_error"] = str(ws_err)
            raise

    def _ws_chat_stream(self, messages: list[dict[str, str]], model: str, tools: list[dict[str, Any]] | None, context: dict[str, Any]) -> Iterator[dict[str, Any]]:
        # requests has no ws; provide explicit normalized error so caller can fallback.
        raise EnaError(code="ws_unavailable", message="WebSocket client unavailable in this build", retryable=True)

    def _http_chat_stream(self, messages: list[dict[str, str]], model: str, tools: list[dict[str, Any]] | None, context: dict[str, Any]) -> Iterator[dict[str, Any]]:
        self._check_circuit()
        url = self._profile.endpoint.rstrip("/") + "/chat"
        payload = {"messages": messages, "model": model or self._profile.model, "tools": tools or [], "context": context}
        for attempt in range(self._retries):
            try:
                resp = self._session.post(url, json=payload, headers=self._headers(), timeout=(5, self._timeout_s), stream=True)
                if resp.status_code >= 400:
                    raise EnaError(code="http_error", message=f"ENA HTTP {resp.status_code}", raw=resp.text, retryable=resp.status_code >= 500)
                self._record_success()
                for line in resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        event = {"type": "token", "text": line}
                    if isinstance(event, dict):
                        yield event
                return
            except EnaError:
                self._record_failure()
                if attempt + 1 >= self._retries:
                    raise
            except Exception as exc:  # noqa: BLE001
                self._record_failure()
                if attempt + 1 >= self._retries:
                    raise EnaError(code="network_failure", message=str(exc), retryable=True) from exc
                time.sleep(0.25 * (2**attempt))

    def _rpc_chat_stream(self, messages: list[dict[str, str]], model: str, tools: list[dict[str, Any]] | None, context: dict[str, Any]) -> Iterator[dict[str, Any]]:
        client = RpcClient(self._profile.rpc_url)
        try:
            method = client._pick_method("ena.stream", "ena_stream", "ena.chat", "ena_chat", "ena.call", "ena_call")  # noqa: SLF001
            params = [{"messages": messages, "model": model or self._profile.model, "tools": tools or [], "context": context}]
            result = client.call(method, params)
            if isinstance(result, list):
                for item in result:
                    if isinstance(item, dict):
                        yield item
                    else:
                        yield {"type": "token", "text": str(item)}
            elif isinstance(result, dict):
                text = result.get("text") or result.get("content") or safe_text(result)
                yield {"type": "token", "text": text}
            else:
                yield {"type": "token", "text": str(result)}
        except Exception as exc:  # noqa: BLE001
            raise EnaError(code="rpc_error", message=str(exc), retryable=True) from exc
        finally:
            client.close()

    def embed(self, texts: list[str]) -> dict[str, Any] | None:
        if self._profile.mode == EnaMode.NETWORK_RPC:
            client = RpcClient(self._profile.rpc_url)
            try:
                method = client._pick_method("ena.embed", "ena_embed")  # noqa: SLF001
                return client.call(method, [{"texts": texts}])
            except Exception:
                return None
            finally:
                client.close()
        url = self._profile.endpoint.rstrip("/") + "/embed"
        try:
            return self._request_json("POST", url, {"texts": texts})
        except EnaError:
            return None

    def submit_training_job(self, bundle_ref: dict[str, Any]) -> dict[str, Any] | None:
        if self._profile.mode == EnaMode.NETWORK_RPC:
            log.info("ENA training submission via node RPC disabled; use local CLI or remote services endpoint")
            return None
        url = self._profile.endpoint.rstrip("/") + "/training/submit"
        try:
            return self._request_json("POST", url, bundle_ref)
        except EnaError:
            return None

    def _request_json(self, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self._check_circuit()
        for attempt in range(self._retries):
            try:
                resp = self._session.request(method, url, json=payload, headers=self._headers(), timeout=(5, self._timeout_s))
                if resp.status_code >= 400:
                    raise EnaError(code="http_error", message=f"HTTP {resp.status_code}", raw=resp.text, retryable=resp.status_code >= 500)
                data = resp.json()
                if not isinstance(data, dict):
                    raise EnaError(code="invalid_json", message="Expected JSON object response", raw=data)
                self._record_success()
                return data
            except EnaError:
                self._record_failure()
                if attempt + 1 >= self._retries:
                    raise
            except Exception as exc:  # noqa: BLE001
                self._record_failure()
                if attempt + 1 >= self._retries:
                    raise EnaError(code="network_failure", message=str(exc), retryable=True) from exc
                time.sleep(0.25 * (2**attempt))
        raise EnaError(code="unknown", message="Unknown ENA failure")


def safe_text(raw: Any) -> str:
    try:
        return json.dumps(raw, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return str(raw)
