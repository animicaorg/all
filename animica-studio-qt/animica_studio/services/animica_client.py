"""OpenAI-compatible inference client for Animica Studio Qt.

:class:`AnimicaClient` abstracts the OpenAI-compatible
``/v1/chat/completions`` endpoint over :mod:`httpx`. It supports:

* non-streaming and streaming (Server-Sent-Events ``data:`` line) responses,
* provider kinds ``animica`` / ``openai`` (identical wire format) and
  ``ollama`` (tolerant of an OpenAI-compatible ``/v1`` base),
* an optional ``Authorization: Bearer`` header (api_key),
* model / temperature / max_tokens / system-prompt taken from
  :class:`~animica_studio.config.Settings`,
* robust timeouts and human-readable error messages that **never** leak the
  API key (all logging/errors are passed through
  :func:`~animica_studio.config.redact`).

It exposes a blocking :meth:`AnimicaClient.chat` (safe to call from a worker
thread), an ``async`` :meth:`AnimicaClient.achat` with the same contract, and a
small :class:`ChatWorker` (a ``QThread``) helper for running a chat off the GUI
thread when PySide6 is available.

This module imports headless (no QApplication required); the optional
``ChatWorker`` falls back to a no-op base class when PySide6 is unavailable.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

import httpx

from ..config import Settings, ProviderKind, redact
from ..models import Message

logger = logging.getLogger("animica_studio.services.animica_client")

# Type alias for an incoming message: either a raw OpenAI dict or a Message.
MessageLike = Union[dict, "Message"]

__all__ = ["AnimicaClient", "ChatResult", "ChatWorker", "AnimicaClientError"]


class AnimicaClientError(RuntimeError):
    """Raised when a chat/inference request fails irrecoverably."""


@dataclass
class ChatResult:
    """A completed chat turn: assistant prose plus any native tool calls.

    ``tool_calls`` is a list of ``{"id", "name", "arguments"}`` where
    ``arguments`` is the raw JSON string the model emitted (may be empty). When
    the server uses the legacy text protocol instead of native function-calling,
    ``tool_calls`` is empty and the fenced calls live inside ``text``.
    """

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: Optional[str] = None

    def __str__(self) -> str:  # so legacy ``str(result)`` paths keep working
        return self.text


def _extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull native ``tool_calls`` (or a legacy ``function_call``) from a message."""
    out: list[dict[str, Any]] = []
    raw = message.get("tool_calls")
    if isinstance(raw, list):
        for tc in raw:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = fn.get("name") or tc.get("name")
            if not name:
                continue
            out.append(
                {
                    "id": tc.get("id") or "",
                    "name": str(name),
                    "arguments": fn.get("arguments") if isinstance(fn, dict) else "",
                }
            )
    # Legacy single function_call shape.
    fc = message.get("function_call")
    if not out and isinstance(fc, dict) and fc.get("name"):
        out.append({"id": "", "name": str(fc["name"]), "arguments": fc.get("arguments", "")})
    return out


def _accumulate_tool_call_deltas(
    buffer: dict[int, dict[str, Any]], deltas: Any
) -> None:
    """Merge streamed ``delta.tool_calls`` fragments into ``buffer`` by index."""
    if not isinstance(deltas, list):
        return
    for d in deltas:
        if not isinstance(d, dict):
            continue
        idx = d.get("index", 0) or 0
        slot = buffer.setdefault(idx, {"id": "", "name": "", "arguments": ""})
        if d.get("id"):
            slot["id"] = d["id"]
        fn = d.get("function") or {}
        if isinstance(fn, dict):
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["arguments"] += fn["arguments"]


def _normalize_messages(messages: list[MessageLike]) -> list[dict]:
    """Convert a mixed list of ``Message`` objects and dicts into OpenAI dicts."""
    out: list[dict] = []
    for m in messages or []:
        if isinstance(m, Message):
            out.append(m.to_openai())
        elif isinstance(m, dict):
            out.append(m)
        elif hasattr(m, "to_openai"):
            # Duck-typed Message-like object.
            out.append(m.to_openai())  # type: ignore[attr-defined]
        else:
            raise TypeError(
                f"Unsupported message type: {type(m)!r}; expected dict or Message"
            )
    return out


class AnimicaClient:
    """A thin, synchronous-and-async OpenAI-compatible chat client."""

    def __init__(self, settings: Settings) -> None:
        if settings is None:  # defensive: callers should always pass settings
            raise ValueError("AnimicaClient requires a Settings instance")
        self.settings = settings

    # ------------------------------------------------------------------ #
    # Request construction helpers
    # ------------------------------------------------------------------ #
    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        api_key = (self.settings.api_key or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _build_payload(
        self,
        messages: list[dict],
        *,
        stream: bool,
        tools: Optional[list[dict]],
        temperature: Optional[float],
        max_tokens: Optional[int],
        model: Optional[str],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.settings.model,
            "messages": messages,
            "stream": bool(stream),
        }
        temp = self.settings.temperature if temperature is None else temperature
        if temp is not None:
            payload["temperature"] = float(temp)
        mt = self.settings.max_tokens if max_tokens is None else max_tokens
        if mt:
            payload["max_tokens"] = int(mt)
        if tools:
            payload["tools"] = tools
            # Let the server decide whether to call a tool.
            payload["tool_choice"] = "auto"
        # Ollama's OpenAI-compatible shim is generally tolerant, but disabling
        # tools it cannot honor avoids 400s on minimal backends.
        kind = ProviderKind.from_value(self.settings.provider_kind)
        if kind is ProviderKind.OLLAMA and tools:
            # Ollama >=0.4 supports tools; keep them but don't force a choice.
            payload.pop("tool_choice", None)
        return payload

    def _timeout(self) -> httpx.Timeout:
        total = float(self.settings.request_timeout or 120.0)
        # Generous read timeout for streaming; bounded connect/write.
        return httpx.Timeout(total, connect=min(30.0, total), read=total, write=total)

    def _redact(self, text: str) -> str:
        return redact(text or "", self.settings.api_key or "")

    # ------------------------------------------------------------------ #
    # Response parsing helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_full_text(data: dict[str, Any]) -> str:
        """Pull the assistant message text from a non-streaming response body."""
        try:
            choices = data.get("choices") or []
            if not choices:
                return ""
            message = choices[0].get("message") or {}
            content = message.get("content")
            if content is None:
                # Some backends return tool calls with null content.
                return ""
            if isinstance(content, list):
                # Some providers return content parts; join text parts.
                parts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") in (None, "text")
                ]
                return "".join(parts)
            return str(content)
        except (AttributeError, IndexError, KeyError, TypeError):
            return ""

    @staticmethod
    def _first_message(data: dict[str, Any]) -> dict[str, Any]:
        try:
            choices = data.get("choices") or []
            if not choices:
                return {}
            msg = choices[0].get("message")
            return msg if isinstance(msg, dict) else {}
        except (AttributeError, IndexError, KeyError, TypeError):
            return {}

    @staticmethod
    def _finish_reason(data: dict[str, Any]) -> Optional[str]:
        try:
            choices = data.get("choices") or []
            if not choices:
                return None
            return choices[0].get("finish_reason")
        except (AttributeError, IndexError, KeyError, TypeError):
            return None

    @staticmethod
    def _delta_from_sse_obj(obj: dict[str, Any]) -> str:
        """Extract the incremental content delta from one streamed SSE object."""
        try:
            choices = obj.get("choices") or []
            if not choices:
                return ""
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content is None:
                return ""
            if isinstance(content, list):
                parts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") in (None, "text")
                ]
                return "".join(parts)
            return str(content)
        except (AttributeError, IndexError, KeyError, TypeError):
            return ""

    @staticmethod
    def _iter_sse_payloads(line: str) -> Optional[str]:
        """Return the JSON payload portion of an SSE ``data:`` line, or None."""
        line = line.strip()
        if not line or line.startswith(":"):
            return None
        if not line.startswith("data:"):
            return None
        payload = line[len("data:"):].strip()
        return payload or None

    def _error_message(self, status: int, body: str) -> str:
        body = self._redact(body)
        snippet = body.strip()
        if len(snippet) > 500:
            snippet = snippet[:500] + "…"
        # Try to surface a clean message from an OpenAI-style error body.
        try:
            parsed = json.loads(body)
            err = parsed.get("error")
            if isinstance(err, dict) and err.get("message"):
                snippet = str(err["message"])
            elif isinstance(err, str):
                snippet = err
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
        hint = ""
        if status in (401, 403):
            hint = " (check your API key / authorization)"
        elif status == 404:
            hint = " (check the endpoint URL and model name)"
        elif status == 429:
            hint = " (rate limited; slow down or check quota)"
        return f"HTTP {status} from inference endpoint{hint}: {snippet}"

    # ------------------------------------------------------------------ #
    # Public: synchronous chat
    # ------------------------------------------------------------------ #
    def chat(
        self,
        messages: Union[list[dict], list["Message"]],
        *,
        stream: bool = True,
        on_chunk: Optional[Callable[[str], None]] = None,
        tools: Optional[list[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> str:
        """Run a blocking chat completion and return the full assistant text.

        Safe to call from a worker thread. When ``stream`` is True and
        ``on_chunk`` is given, ``on_chunk(delta)`` is invoked per token delta.
        ``cancel`` is polled cooperatively; when it returns True the request is
        aborted and whatever text has accumulated so far is returned.
        """
        return self.chat_full(
            messages,
            stream=stream,
            on_chunk=on_chunk,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            cancel=cancel,
        ).text

    def chat_full(
        self,
        messages: Union[list[dict], list["Message"]],
        *,
        stream: bool = True,
        on_chunk: Optional[Callable[[str], None]] = None,
        tools: Optional[list[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> ChatResult:
        """Like :meth:`chat` but returns the full :class:`ChatResult`.

        Surfaces native ``tool_calls`` when the server emits them, so the agent
        can use OpenAI function-calling on capable backends and fall back to the
        fenced-text protocol on minimal ones.
        """
        norm = _normalize_messages(list(messages))
        payload = self._build_payload(
            norm,
            stream=stream,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
        )
        url = self.settings.chat_completions_url
        headers = self._headers()
        timeout = self._timeout()

        def _cancelled() -> bool:
            try:
                return bool(cancel and cancel())
            except Exception:  # cancel callback must never crash the request
                return False

        logger.debug(
            "chat -> %s payload=%s",
            self._redact(url),
            self._redact(json.dumps({**payload, "messages": f"<{len(norm)} msgs>"})),
        )

        try:
            with httpx.Client(timeout=timeout) as client:
                if stream:
                    return self._chat_stream_sync(
                        client, url, headers, payload, on_chunk, _cancelled
                    )
                return self._chat_once_sync(client, url, headers, payload)
        except AnimicaClientError:
            raise
        except httpx.TimeoutException as exc:
            raise AnimicaClientError(
                f"Request timed out after {self.settings.request_timeout}s: "
                f"{self._redact(str(exc))}"
            ) from exc
        except httpx.ConnectError as exc:
            raise AnimicaClientError(
                f"Could not connect to {self._redact(url)}: {self._redact(str(exc))}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AnimicaClientError(
                f"HTTP error contacting inference endpoint: {self._redact(str(exc))}"
            ) from exc

    def _chat_once_sync(
        self,
        client: httpx.Client,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> ChatResult:
        resp = client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise AnimicaClientError(self._error_message(resp.status_code, resp.text))
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise AnimicaClientError(
                f"Malformed JSON from endpoint: {self._redact(resp.text[:300])}"
            ) from exc
        message = self._first_message(data)
        return ChatResult(
            text=self._extract_full_text(data),
            tool_calls=_extract_tool_calls(message),
            finish_reason=self._finish_reason(data),
        )

    def _chat_stream_sync(
        self,
        client: httpx.Client,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        on_chunk: Optional[Callable[[str], None]],
        cancelled: Callable[[], bool],
    ) -> ChatResult:
        accumulated: list[str] = []
        tool_buf: dict[int, dict[str, Any]] = {}
        finish_reason: Optional[str] = None
        with client.stream("POST", url, headers=headers, json=payload) as resp:
            if resp.status_code >= 400:
                body = resp.read().decode("utf-8", "replace")
                raise AnimicaClientError(self._error_message(resp.status_code, body))
            for line in resp.iter_lines():
                if cancelled():
                    break
                payload_str = self._iter_sse_payloads(line)
                if payload_str is None:
                    continue
                if payload_str == "[DONE]":
                    break
                try:
                    obj = json.loads(payload_str)
                except (json.JSONDecodeError, ValueError):
                    continue
                fr = self._finish_reason(obj)
                if fr:
                    finish_reason = fr
                choices = obj.get("choices") or []
                if choices:
                    _accumulate_tool_call_deltas(
                        tool_buf, (choices[0].get("delta") or {}).get("tool_calls")
                    )
                delta = self._delta_from_sse_obj(obj)
                if delta:
                    accumulated.append(delta)
                    if on_chunk is not None:
                        try:
                            on_chunk(delta)
                        except Exception:  # UI callbacks must not abort streaming
                            logger.exception("on_chunk callback raised; continuing")
        return ChatResult(
            text="".join(accumulated),
            tool_calls=[tool_buf[k] for k in sorted(tool_buf) if tool_buf[k].get("name")],
            finish_reason=finish_reason,
        )

    # ------------------------------------------------------------------ #
    # Public: async chat
    # ------------------------------------------------------------------ #
    async def achat(
        self,
        messages: Union[list[dict], list["Message"]],
        *,
        stream: bool = True,
        on_chunk: Optional[Callable[[str], None]] = None,
        tools: Optional[list[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> str:
        """Async counterpart of :meth:`chat` with the same contract."""
        return (
            await self.achat_full(
                messages,
                stream=stream,
                on_chunk=on_chunk,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                model=model,
                cancel=cancel,
            )
        ).text

    async def achat_full(
        self,
        messages: Union[list[dict], list["Message"]],
        *,
        stream: bool = True,
        on_chunk: Optional[Callable[[str], None]] = None,
        tools: Optional[list[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> ChatResult:
        """Async counterpart of :meth:`chat_full`."""
        norm = _normalize_messages(list(messages))
        payload = self._build_payload(
            norm,
            stream=stream,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
        )
        url = self.settings.chat_completions_url
        headers = self._headers()
        timeout = self._timeout()

        def _cancelled() -> bool:
            try:
                return bool(cancel and cancel())
            except Exception:
                return False

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if stream:
                    return await self._chat_stream_async(
                        client, url, headers, payload, on_chunk, _cancelled
                    )
                return await self._chat_once_async(client, url, headers, payload)
        except AnimicaClientError:
            raise
        except httpx.TimeoutException as exc:
            raise AnimicaClientError(
                f"Request timed out after {self.settings.request_timeout}s: "
                f"{self._redact(str(exc))}"
            ) from exc
        except httpx.ConnectError as exc:
            raise AnimicaClientError(
                f"Could not connect to {self._redact(url)}: {self._redact(str(exc))}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AnimicaClientError(
                f"HTTP error contacting inference endpoint: {self._redact(str(exc))}"
            ) from exc

    async def _chat_once_async(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> ChatResult:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise AnimicaClientError(self._error_message(resp.status_code, resp.text))
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise AnimicaClientError(
                f"Malformed JSON from endpoint: {self._redact(resp.text[:300])}"
            ) from exc
        message = self._first_message(data)
        return ChatResult(
            text=self._extract_full_text(data),
            tool_calls=_extract_tool_calls(message),
            finish_reason=self._finish_reason(data),
        )

    async def _chat_stream_async(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        on_chunk: Optional[Callable[[str], None]],
        cancelled: Callable[[], bool],
    ) -> ChatResult:
        accumulated: list[str] = []
        tool_buf: dict[int, dict[str, Any]] = {}
        finish_reason: Optional[str] = None
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")
                raise AnimicaClientError(self._error_message(resp.status_code, body))
            async for line in resp.aiter_lines():
                if cancelled():
                    break
                payload_str = self._iter_sse_payloads(line)
                if payload_str is None:
                    continue
                if payload_str == "[DONE]":
                    break
                try:
                    obj = json.loads(payload_str)
                except (json.JSONDecodeError, ValueError):
                    continue
                fr = self._finish_reason(obj)
                if fr:
                    finish_reason = fr
                choices = obj.get("choices") or []
                if choices:
                    _accumulate_tool_call_deltas(
                        tool_buf, (choices[0].get("delta") or {}).get("tool_calls")
                    )
                delta = self._delta_from_sse_obj(obj)
                if delta:
                    accumulated.append(delta)
                    if on_chunk is not None:
                        try:
                            on_chunk(delta)
                        except Exception:
                            logger.exception("on_chunk callback raised; continuing")
        return ChatResult(
            text="".join(accumulated),
            tool_calls=[tool_buf[k] for k in sorted(tool_buf) if tool_buf[k].get("name")],
            finish_reason=finish_reason,
        )

    # ------------------------------------------------------------------ #
    # Public: models + connectivity
    # ------------------------------------------------------------------ #
    def list_models(self) -> list[str]:
        """Return available model ids via ``GET {endpoint}/models``.

        Returns an empty list when the endpoint does not implement the route or
        on transport error (never raises for connectivity issues).
        """
        url = self.settings.models_url
        headers = self._headers()
        try:
            with httpx.Client(timeout=self._timeout()) as client:
                resp = client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            logger.debug("list_models transport error: %s", self._redact(str(exc)))
            return []
        if resp.status_code >= 400:
            logger.debug(
                "list_models HTTP %s: %s",
                resp.status_code,
                self._redact(resp.text[:200]),
            )
            return []
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            return []
        return self._parse_model_ids(data)

    @staticmethod
    def _parse_model_ids(data: Any) -> list[str]:
        ids: list[str] = []
        items: list[Any] = []
        if isinstance(data, dict):
            # OpenAI shape: {"data": [{"id": "..."}]}; Ollama: {"models": [...]}
            if isinstance(data.get("data"), list):
                items = data["data"]
            elif isinstance(data.get("models"), list):
                items = data["models"]
        elif isinstance(data, list):
            items = data
        for item in items:
            if isinstance(item, dict):
                mid = item.get("id") or item.get("name") or item.get("model")
                if mid:
                    ids.append(str(mid))
            elif isinstance(item, str):
                ids.append(item)
        # De-duplicate preserving order.
        seen: set[str] = set()
        unique: list[str] = []
        for mid in ids:
            if mid not in seen:
                seen.add(mid)
                unique.append(mid)
        return unique

    def test_connection(self) -> tuple[bool, str]:
        """Probe the endpoint; return ``(ok, human_message)``.

        Tries ``/models`` first (cheap), then falls back to a tiny chat ping if
        the models route is unavailable. Never leaks the API key.
        """
        url = self.settings.models_url
        headers = self._headers()
        try:
            with httpx.Client(timeout=self._timeout()) as client:
                resp = client.get(url, headers=headers)
        except httpx.ConnectError as exc:
            return False, f"Cannot connect to {self._redact(self.settings.endpoint)}: {self._redact(str(exc))}"
        except httpx.TimeoutException:
            return False, f"Connection to {self._redact(self.settings.endpoint)} timed out"
        except httpx.HTTPError as exc:
            return False, f"Connection error: {self._redact(str(exc))}"

        if resp.status_code < 400:
            try:
                models = self._parse_model_ids(resp.json())
            except (json.JSONDecodeError, ValueError):
                models = []
            if models:
                shown = ", ".join(models[:5])
                more = "…" if len(models) > 5 else ""
                return True, f"Connected. Models: {shown}{more}"
            return True, "Connected (no models listed)."

        if resp.status_code in (401, 403):
            return False, "Authentication failed (check your API key)."
        if resp.status_code == 404:
            # /models may not exist; fall back to a minimal chat ping.
            return self._ping_via_chat()
        return False, self._error_message(resp.status_code, resp.text)

    def _ping_via_chat(self) -> tuple[bool, str]:
        try:
            text = self.chat(
                [{"role": "user", "content": "ping"}],
                stream=False,
                max_tokens=1,
            )
            return True, "Connected (chat endpoint responded)."
        except AnimicaClientError as exc:
            return False, str(exc)


# ----------------------------------------------------------------------------- #
# QThread helper (optional; degrades gracefully when PySide6 is unavailable)
# ----------------------------------------------------------------------------- #
try:  # pragma: no cover - exercised in the GUI, not in headless import tests
    from PySide6.QtCore import QThread, Signal

    class ChatWorker(QThread):
        """Run an :class:`AnimicaClient` chat on a background ``QThread``.

        Emits :attr:`chunk` per streamed delta, :attr:`finished_text` with the
        full assistant text on success, and :attr:`failed` with a redacted error
        message on failure. Cancel via :meth:`cancel` (cooperative).
        """

        chunk = Signal(str)
        finished_text = Signal(str)
        failed = Signal(str)

        def __init__(
            self,
            client: AnimicaClient,
            messages: list[MessageLike],
            *,
            stream: bool = True,
            tools: Optional[list[dict]] = None,
            temperature: Optional[float] = None,
            max_tokens: Optional[int] = None,
            model: Optional[str] = None,
            parent: Optional[object] = None,
        ) -> None:
            super().__init__(parent)
            self._client = client
            self._messages = messages
            self._stream = stream
            self._tools = tools
            self._temperature = temperature
            self._max_tokens = max_tokens
            self._model = model
            self._cancelled = False

        def cancel(self) -> None:
            """Request cooperative cancellation of the in-flight chat."""
            self._cancelled = True

        def run(self) -> None:  # noqa: D401 - QThread entrypoint
            try:
                text = self._client.chat(
                    self._messages,
                    stream=self._stream,
                    on_chunk=self.chunk.emit,
                    tools=self._tools,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    model=self._model,
                    cancel=lambda: self._cancelled,
                )
                self.finished_text.emit(text)
            except AnimicaClientError as exc:
                self.failed.emit(str(exc))
            except Exception as exc:  # pragma: no cover - safety net
                self.failed.emit(f"Unexpected error: {redact(str(exc))}")

except ImportError:  # pragma: no cover - headless without PySide6
    ChatWorker = None  # type: ignore[assignment]


# ----------------------------------------------------------------------------- #
# Tiny self-test (guarded by network availability; never hard-fails offline)
# ----------------------------------------------------------------------------- #
def _selftest() -> int:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    client = AnimicaClient(settings)
    print(f"Endpoint:   {settings.endpoint}")
    print(f"Chat URL:   {settings.chat_completions_url}")
    print(f"Models URL: {settings.models_url}")
    print(f"Model:      {settings.model}")
    print(f"Provider:   {settings.provider_kind.value}")

    # Verify header redaction (no secret leakage) on a fake key.
    probe = AnimicaClient(Settings(api_key="sk-supersecret-value-1234567890"))
    hdrs = probe._headers()
    assert hdrs.get("Authorization", "").startswith("Bearer "), "auth header missing"
    sample = f"Authorization: {hdrs['Authorization']} model=anm-fast-8b"
    redacted = probe._redact(sample)
    assert "supersecret" not in redacted, "API key leaked through redact()"
    print("Redaction OK:", redacted)

    print("\nProbing endpoint (network optional)...")
    try:
        ok, msg = client.test_connection()
        print(f"  test_connection -> ok={ok}: {msg}")
        if ok:
            models = client.list_models()
            print(f"  list_models -> {models[:10]}")
    except Exception as exc:  # offline / unreachable is fine for the self-test
        print(f"  (no live endpoint reachable: {redact(str(exc))})")

    # Exercise the async path against a non-routable host without failing hard.
    async def _async_probe() -> None:
        offline = AnimicaClient(
            Settings(endpoint="http://127.0.0.1:9/v1", request_timeout=2.0)
        )
        try:
            await offline.achat(
                [{"role": "user", "content": "hi"}], stream=False
            )
        except AnimicaClientError as exc:
            print(f"  achat (expected offline error): {redact(str(exc))[:120]}")

    try:
        asyncio.run(_async_probe())
    except Exception as exc:  # pragma: no cover
        print(f"  async probe note: {redact(str(exc))}")

    print("\nSelf-test complete.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_selftest())
