"""
animica.ai.gateway — an OpenAI-compatible HTTP gateway over Animica's model layer.

``animica ai serve`` builds this FastAPI app. It exposes the standard OpenAI
surface and routes each request to an ENA model/embedding adapter
(``animica.ena.providers``), so the same `ollama`/`openai`/`deterministic`
providers the CLI uses back the HTTP API too:

    GET  /v1/models                  list routable models (configured + local)
    POST /v1/chat/completions        chat (streaming via SSE + non-streaming)
    POST /v1/completions             legacy text completion
    POST /v1/embeddings              embeddings
    GET  /health                     liveness

Hardening (5.2.0 §3/§17):
  * Consistent OpenAI error envelope: ``{"error": {"message", "type", "code"}}``.
  * Optional bearer-token auth (``--api-key``); when set, every /v1/* call must
    send ``Authorization: Bearer <key>`` or get a 401 in OpenAI's shape.
  * JSON mode: ``response_format={"type":"json_object"}`` injects a system
    instruction to return strict JSON (best-effort — the model still does the work).
  * Tools: ``tools`` are described into the system prompt so tool-aware models can
    use them; structured ``tool_calls`` extraction is intentionally NOT faked here.

FastAPI is imported lazily by ``build_app`` so importing this module (and hence
``animica.ai``) does not require the ``[backend]`` extra until you actually serve.
"""

import json
import time
import uuid
from typing import Any, Optional


class GatewayNotInstalled(RuntimeError):
    """Raised when the web-serving extra (FastAPI/uvicorn) is not installed."""


_INSTALL_HINT = (
    'animica ai serve needs the web stack. Install it with:\n'
    '    pip install "animica[backend]"'
)


def _approx_tokens(text: str) -> int:
    # Cheap, dependency-free estimate (~4 chars/token). Labeled approximate.
    return max(1, len(text or "") // 4)


# --------------------------------------------------------------------------- #
# Model routing — map a requested model name to an ENA adapter.
# --------------------------------------------------------------------------- #
def _list_ollama_models(base: str = "http://127.0.0.1:11434") -> list[str]:
    try:
        import urllib.request
        with urllib.request.urlopen(base.rstrip("/") + "/api/tags", timeout=4) as r:  # noqa: S310
            data = json.loads(r.read().decode("utf-8"))
        return [m.get("name") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def list_models() -> list[dict[str, Any]]:
    """All routable models in OpenAI ``/v1/models`` shape."""
    from animica.ena import config as ena_config

    cfg = ena_config.load_config()
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for key, mp in (cfg.model_providers or {}).items():
        if mp.model in seen:
            continue
        seen.add(mp.model)
        out.append({"id": mp.model, "object": "model", "created": 0,
                    "owned_by": f"animica:{mp.provider}"})
    for name in _list_ollama_models():
        if name not in seen:
            seen.add(name)
            out.append({"id": name, "object": "model", "created": 0, "owned_by": "animica:ollama"})
    return out


def resolve_chat_adapter(model: Optional[str], default_provider: Optional[str] = None):
    """Resolve (adapter, provider_key, model_name) for a chat request.

    Routing: a requested model that matches a configured provider's model or a
    local Ollama model is routed there; otherwise the default provider is used
    (with the requested model name applied so e.g. an ollama tag still works).
    """
    from animica.ena import config as ena_config
    from animica.ena.models import ModelProviderConfig
    from animica.ena.providers import build_model_adapter

    cfg = ena_config.load_config()
    # 1) exact match against a configured provider's model
    if model:
        for key, mp in (cfg.model_providers or {}).items():
            if mp.model == model:
                return build_model_adapter(mp), key, mp.model
        # 2) a local ollama tag
        if model in _list_ollama_models():
            mp = ModelProviderConfig(name="ollama", provider="ollama", transport="http",
                                     model=model, base_url="http://127.0.0.1:11434")
            return build_model_adapter(mp), "ollama", model
    # 3) default provider (optionally overriding the model name)
    key = default_provider or cfg.default_model_provider
    mp = cfg.model_provider(key)
    if model:
        mp.model = model
    return build_model_adapter(mp), key, mp.model


def resolve_embedding_adapter(model: Optional[str], default_provider: Optional[str] = None):
    from animica.ena import config as ena_config
    from animica.ena.providers import build_embedding_adapter

    cfg = ena_config.load_config()
    if model:
        for key, ep in (cfg.embedding_providers or {}).items():
            if ep.model == model:
                return build_embedding_adapter(ep), key, ep.model
    key = default_provider or cfg.default_embedding_provider
    ep = cfg.embedding_provider(key)
    if model:
        ep.model = model
    return build_embedding_adapter(ep), key, ep.model


# --------------------------------------------------------------------------- #
# Message flattening — ENA adapters take (prompt, system, history).
# --------------------------------------------------------------------------- #
def _flatten(messages: list[dict[str, Any]], response_format: Any, tools: Any):
    system_parts: list[str] = []
    history: list[dict[str, str]] = []
    last_user = ""
    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "system":
            system_parts.append(content)
        elif role in ("user", "assistant", "tool"):
            history.append({"role": "user" if role == "tool" else role, "content": content})
            if role == "user":
                last_user = content
    # The final user turn is the prompt; the rest is history.
    if history and history[-1]["role"] == "user":
        history = history[:-1]
    if isinstance(response_format, dict) and response_format.get("type") == "json_object":
        system_parts.append("Respond ONLY with a single valid JSON object. No prose, no code fences.")
    if tools:
        try:
            names = ", ".join(t.get("function", {}).get("name", "?") for t in tools)
            system_parts.append(f"You may use these tools when helpful: {names}.")
        except Exception:
            pass
    system = "\n".join(p for p in system_parts if p) or None
    return last_user, system, history


def _http_post(url: str, payload: dict, headers: dict, timeout: float = 60.0) -> dict:
    import urllib.request
    body = json.dumps(payload).encode("utf-8")
    h = {"content-type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def native_tools_choices(cfg, messages: list, tools: list, tool_choice,
                         max_tokens, temperature):
    """Call a tool-capable upstream natively and return OpenAI-format ``choices``
    with STRUCTURED ``tool_calls`` — not a prompt-injection emulation.

    Supports ollama (/api/chat with tools) and OpenAI-compatible (/chat/completions,
    which already returns the right shape). Returns ``None`` for providers with no
    native tool support, so the caller falls back to the prompt-injection path.
    """
    provider = (getattr(cfg, "provider", "") or "").lower()
    if provider == "ollama":
        base = (cfg.base_url or "http://127.0.0.1:11434").rstrip("/")
        payload = {"model": cfg.model, "messages": messages, "tools": tools, "stream": False,
                   "options": {"temperature": cfg.temperature if temperature is None else temperature,
                               "num_predict": max_tokens or cfg.max_tokens}}
        data = _http_post(base + "/api/chat", payload, {}, timeout=cfg.timeout_seconds)
        msg = data.get("message") or {}
        tool_calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            # OpenAI requires arguments as a JSON STRING; ollama returns an object.
            tool_calls.append({"id": f"call_{i}", "type": "function",
                               "function": {"name": fn.get("name"),
                                            "arguments": args if isinstance(args, str) else json.dumps(args)}})
        out_msg: dict = {"role": "assistant", "content": msg.get("content") or None}
        if tool_calls:
            out_msg["tool_calls"] = tool_calls
        return [{"index": 0, "message": out_msg,
                 "finish_reason": "tool_calls" if tool_calls else "stop"}]
    if provider in ("openai", "openai_compatible"):
        from animica.ena.providers import _api_key
        base = (cfg.base_url or cfg.endpoint or "").rstrip("/")
        if not base:
            return None
        headers = {}
        k = _api_key(cfg)
        if k:
            headers["authorization"] = f"Bearer {k}"
        payload = {"model": cfg.model, "messages": messages, "tools": tools, "stream": False}
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        data = _http_post(base + "/chat/completions", payload, headers, timeout=cfg.timeout_seconds)
        return data.get("choices")
    return None


def build_app(*, api_key: Optional[str] = None,
              provider: Optional[str] = None,
              model: Optional[str] = None,
              rate_limit_per_min: Optional[int] = None):
    """Construct the FastAPI gateway app. Raises GatewayNotInstalled if FastAPI absent."""
    try:
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse, StreamingResponse
    except Exception as exc:  # noqa: BLE001
        raise GatewayNotInstalled(_INSTALL_HINT) from exc

    from animica.ena.providers import ProviderError

    app = FastAPI(title="Animica AI Gateway", version="5.2.0")

    def _err(message: str, status: int, etype: str = "invalid_request_error",
             code: Optional[str] = None):
        return JSONResponse(status_code=status,
                            content={"error": {"message": message, "type": etype, "code": code}})

    def _auth_ok(request: Request) -> bool:
        if not api_key:
            return True
        hdr = request.headers.get("authorization", "")
        return hdr.startswith("Bearer ") and hdr[7:].strip() == api_key

    # Optional fixed-window per-client rate limit (off unless rate_limit_per_min set).
    _hits: dict = {}

    def _rate_ok(request: Request) -> bool:
        if not rate_limit_per_min:
            return True
        client = request.headers.get("authorization") or (
            request.client.host if request.client else "anon")
        now = int(time.time())
        window = now - (now % 60)
        cur = _hits.get(client)
        if not cur or cur[0] != window:
            _hits[client] = [window, 1]
            return True
        if cur[1] >= rate_limit_per_min:
            return False
        cur[1] += 1
        return True

    def _guard(request: Request):
        """Combined auth + rate-limit gate. Returns an error response or None."""
        if not _auth_ok(request):
            return _err("missing or invalid API key", 401, "authentication_error", "invalid_api_key")
        if not _rate_ok(request):
            return _err(f"rate limit exceeded ({rate_limit_per_min}/min)", 429,
                        "rate_limit_error", "rate_limited")
        return None

    @app.get("/health")
    def health():  # noqa: ANN202
        return {"status": "ok", "service": "animica-ai-gateway", "version": "5.2.0"}

    @app.get("/v1/models")
    def models_route(request: Request):  # noqa: ANN202
        _g = _guard(request)
        if _g is not None:
            return _g
        return {"object": "list", "data": list_models()}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):  # noqa: ANN202
        _g = _guard(request)
        if _g is not None:
            return _g
        try:
            body = await request.json()
        except Exception:
            return _err("request body must be valid JSON", 400)
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return _err("'messages' must be a non-empty array", 400)

        req_model = body.get("model") or model
        try:
            adapter, key, resolved_model = resolve_chat_adapter(req_model, provider)
        except Exception as e:  # noqa: BLE001
            return _err(f"could not resolve model: {e}", 400, "invalid_request_error", "model_not_found")

        prompt, system, history = _flatten(messages, body.get("response_format"), body.get("tools"))
        max_tokens = body.get("max_tokens")
        temperature = body.get("temperature")
        stream = bool(body.get("stream"))
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())

        # Native tool calling: for tool-capable upstreams (ollama / openai-compatible)
        # call them with `tools` and return STRUCTURED tool_calls. Non-streaming only;
        # on any failure or an unsupported provider we fall through to the
        # prompt-injection path (tools are still described to the model via _flatten).
        tools = body.get("tools")
        if tools and not stream:
            try:
                native = native_tools_choices(adapter.cfg, messages, tools,
                                              body.get("tool_choice"), max_tokens, temperature)
            except Exception:  # noqa: BLE001 — degrade to prompt-injection
                native = None
            if native:
                pt = _approx_tokens(json.dumps(messages))
                return JSONResponse(content={
                    "id": cid, "object": "chat.completion", "created": created,
                    "model": resolved_model, "choices": native,
                    "usage": {"prompt_tokens": pt, "completion_tokens": 0, "total_tokens": pt},
                })

        def _generate() -> str:
            return adapter.generate(prompt, system=system, history=history,
                                    max_tokens=max_tokens, temperature=temperature)

        if not stream:
            try:
                content = _generate()
            except ProviderError as e:
                return _err(str(e), 502, "upstream_error", "provider_error")
            except Exception as e:  # noqa: BLE001
                return _err(f"generation failed: {e}", 500, "internal_error")
            pt, ct = _approx_tokens(prompt + (system or "")), _approx_tokens(content)
            return JSONResponse(content={
                "id": cid, "object": "chat.completion", "created": created, "model": resolved_model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
            })

        # Streaming: ENA adapters are non-streaming, so we generate fully then
        # emit the text as SSE deltas — real content, chunked (OpenAI shape).
        def _sse():
            def chunk(delta: dict, finish=None):
                payload = {"id": cid, "object": "chat.completion.chunk", "created": created,
                           "model": resolved_model,
                           "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                return f"data: {json.dumps(payload)}\n\n"
            yield chunk({"role": "assistant"})
            try:
                content = _generate()
            except ProviderError as e:
                yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'upstream_error'}})}\n\n"
                yield "data: [DONE]\n\n"
                return
            except Exception as e:  # noqa: BLE001
                yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'internal_error'}})}\n\n"
                yield "data: [DONE]\n\n"
                return
            # Emit in word-ish chunks for a streaming feel.
            buf = content
            step = max(1, len(buf) // 40) if buf else 1
            for i in range(0, len(buf), step):
                yield chunk({"content": buf[i:i + step]})
            yield chunk({}, finish="stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(_sse(), media_type="text/event-stream")

    @app.post("/v1/completions")
    async def completions(request: Request):  # noqa: ANN202
        _g = _guard(request)
        if _g is not None:
            return _g
        try:
            body = await request.json()
        except Exception:
            return _err("request body must be valid JSON", 400)
        prompt = body.get("prompt")
        if isinstance(prompt, list):
            prompt = "\n".join(str(p) for p in prompt)
        if not prompt:
            return _err("'prompt' is required", 400)
        try:
            adapter, key, resolved_model = resolve_chat_adapter(body.get("model") or model, provider)
            text = adapter.generate(str(prompt), max_tokens=body.get("max_tokens"),
                                    temperature=body.get("temperature"))
        except ProviderError as e:
            return _err(str(e), 502, "upstream_error", "provider_error")
        except Exception as e:  # noqa: BLE001
            return _err(f"generation failed: {e}", 500, "internal_error")
        pt, ct = _approx_tokens(str(prompt)), _approx_tokens(text)
        return {"id": "cmpl-" + uuid.uuid4().hex[:24], "object": "text_completion",
                "created": int(time.time()), "model": resolved_model,
                "choices": [{"index": 0, "text": text, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}}

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):  # noqa: ANN202
        _g = _guard(request)
        if _g is not None:
            return _g
        try:
            body = await request.json()
        except Exception:
            return _err("request body must be valid JSON", 400)
        inp = body.get("input")
        if inp is None:
            return _err("'input' is required", 400)
        texts = [inp] if isinstance(inp, str) else list(inp)
        try:
            adapter, key, resolved_model = resolve_embedding_adapter(body.get("model") or model, provider)
            vectors = adapter.embed(texts)
        except ProviderError as e:
            return _err(str(e), 502, "upstream_error", "provider_error")
        except Exception as e:  # noqa: BLE001
            return _err(f"embedding failed: {e}", 500, "internal_error")
        data = [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)]
        total = sum(_approx_tokens(t) for t in texts)
        return {"object": "list", "data": data, "model": resolved_model,
                "usage": {"prompt_tokens": total, "total_tokens": total}}

    return app
