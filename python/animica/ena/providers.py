from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

import httpx

from .models import EmbeddingProviderConfig, EnaConfigModel, ModelProviderConfig
from .text import keyword_terms, normalize_text, sha256_hex, summarize_passages


class ProviderError(RuntimeError):
    pass


class StructuredOutputError(ProviderError):
    pass


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: Dict[str, Any]


@dataclass
class ToolCall:
    name: str
    arguments: Dict[str, Any]
    call_id: Optional[str] = None


@dataclass
class ModelResponse:
    provider_name: str
    model: str
    content: str = ""
    parsed: Optional[Any] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    usage: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


def _resolve_base_url(config: ModelProviderConfig | EmbeddingProviderConfig) -> Optional[str]:
    return (config.base_url or config.endpoint or "").rstrip("/") or None


def _resolve_api_key(config: ModelProviderConfig | EmbeddingProviderConfig) -> Optional[str]:
    for env_name in config.api_key_env_vars:
        value = os.getenv(env_name)
        if value:
            return value
    return None


def _sleep_with_backoff(backoff_seconds: float, max_backoff_seconds: float, attempt: int) -> None:
    delay = min(backoff_seconds * max(attempt, 1), max_backoff_seconds)
    if delay > 0:
        time.sleep(delay)


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ""
        try:
            return json.loads(stripped)
        except Exception:
            start_object = stripped.find("{")
            end_object = stripped.rfind("}")
            start_array = stripped.find("[")
            end_array = stripped.rfind("]")
            candidates: list[str] = []
            if start_object != -1 and end_object != -1 and end_object > start_object:
                candidates.append(stripped[start_object : end_object + 1])
            if start_array != -1 and end_array != -1 and end_array > start_array:
                candidates.append(stripped[start_array : end_array + 1])
            for candidate in candidates:
                try:
                    return json.loads(candidate)
                except Exception:
                    continue
    return value


def _validate_json_schema(value: Any, schema: Dict[str, Any], *, path: str = "$") -> None:
    if not schema:
        return
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            raise StructuredOutputError(f"{path}: expected object")
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise StructuredOutputError(f"{path}: missing required key {key}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                _validate_json_schema(item, properties[key], path=f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise StructuredOutputError(f"{path}: unexpected key {key}")
    elif schema_type == "array":
        if not isinstance(value, list):
            raise StructuredOutputError(f"{path}: expected array")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_json_schema(item, item_schema, path=f"{path}[{index}]")
    elif schema_type == "string":
        if not isinstance(value, str):
            raise StructuredOutputError(f"{path}: expected string")
        if "enum" in schema and value not in schema["enum"]:
            raise StructuredOutputError(f"{path}: value {value!r} not in enum")
    elif schema_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise StructuredOutputError(f"{path}: expected integer")
    elif schema_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise StructuredOutputError(f"{path}: expected number")
    elif schema_type == "boolean":
        if not isinstance(value, bool):
            raise StructuredOutputError(f"{path}: expected boolean")
    elif schema_type is None:
        return


def _ensure_json(content: Any, schema: Dict[str, Any]) -> Any:
    value = _coerce_json(content)
    if isinstance(value, str):
        raise StructuredOutputError("structured output was not valid JSON")
    _validate_json_schema(value, schema)
    return value


def _extract_content_from_message(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts: List[str] = []
        for item in message:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif "text" in item:
                    parts.append(str(item["text"]))
                elif "content" in item:
                    parts.append(str(item["content"]))
        return "".join(parts)
    if isinstance(message, dict):
        if "content" in message:
            return _extract_content_from_message(message["content"])
        if "text" in message:
            return str(message["text"])
    return str(message or "")


def _prompt_from_messages(messages: Sequence[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for message in messages:
        role = message.get("role", "user")
        content = normalize_text(_extract_content_from_message(message.get("content", "")))
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


class BaseModelProvider:
    def __init__(self, provider_name: str, config: ModelProviderConfig):
        self.provider_name = provider_name
        self.config = config

    def capabilities(self) -> Dict[str, bool]:
        return {
            "tool_calling": False,
            "json_schema": False,
            "chat": True,
        }

    def list_models(self) -> List[Dict[str, Any]]:
        return [{"id": self.config.model, "provider": self.provider_name}]

    def test(self) -> Dict[str, Any]:
        response = self.chat(
            [{"role": "user", "content": "Reply with JSON: {\"ok\":true,\"provider\":\"name\"}"}],
            response_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "provider": {"type": "string"},
                },
                "required": ["ok", "provider"],
                "additionalProperties": True,
            },
        )
        return {
            "provider": self.provider_name,
            "model": self.config.model,
            "ok": bool(response.parsed and response.parsed.get("ok") is True),
            "response": response.parsed or response.content,
            "capabilities": self.capabilities(),
        }

    def chat(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        tools: Optional[Sequence[ToolDefinition]] = None,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> ModelResponse:
        raise NotImplementedError

    def structured(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        schema: Dict[str, Any],
        tools: Optional[Sequence[ToolDefinition]] = None,
    ) -> ModelResponse:
        response = self.chat(messages, tools=tools, response_schema=schema)
        if response.parsed is None:
            response.parsed = _ensure_json(response.content, schema)
        return response

    def summarize(self, prompt: str, passages: Iterable[str]) -> str:
        passages_list = [normalize_text(item) for item in passages if normalize_text(item)]
        schema = {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        }
        response = self.structured(
            [
                {
                    "role": "system",
                    "content": "Summarize the provided evidence and stay grounded in the evidence only.",
                },
                {
                    "role": "user",
                    "content": json.dumps({"prompt": prompt, "passages": passages_list[:12]}, ensure_ascii=False),
                },
            ],
            schema=schema,
        )
        return normalize_text(response.parsed["summary"])

    def classify(self, text: str, labels: Sequence[str]) -> Dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "label": {"type": "string", "enum": list(labels)},
                "reason": {"type": "string"},
            },
            "required": ["label", "reason"],
            "additionalProperties": False,
        }
        response = self.structured(
            [
                {"role": "system", "content": "Classify the text into exactly one label."},
                {
                    "role": "user",
                    "content": json.dumps({"labels": list(labels), "text": text}, ensure_ascii=False),
                },
            ],
            schema=schema,
        )
        return response.parsed

    def extract(self, text: str, schema: Dict[str, Any], instruction: str = "Extract the requested fields.") -> Any:
        response = self.structured(
            [
                {"role": "system", "content": instruction},
                {"role": "user", "content": text},
            ],
            schema=schema,
        )
        return response.parsed

    def rewrite(self, text: str, instruction: str) -> str:
        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }
        response = self.structured(
            [
                {"role": "system", "content": instruction},
                {"role": "user", "content": text},
            ],
            schema=schema,
        )
        return normalize_text(response.parsed["text"])


class DeterministicModelProvider(BaseModelProvider):
    def capabilities(self) -> Dict[str, bool]:
        return {
            "tool_calling": False,
            "json_schema": True,
            "chat": True,
        }

    def chat(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        tools: Optional[Sequence[ToolDefinition]] = None,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> ModelResponse:
        prompt = _prompt_from_messages(messages)
        content = ""
        for message in reversed(messages):
            if message.get("role") in {"user", "tool"}:
                content = normalize_text(_extract_content_from_message(message.get("content", "")))
                if content:
                    break
        if not content:
            content = normalize_text(prompt.split("user:", 1)[-1] if "user:" in prompt else prompt)
        if response_schema:
            parsed = self._structured_from_prompt(content, response_schema, tools=tools or [])
            return ModelResponse(
                provider_name=self.provider_name,
                model=self.config.model,
                content=json.dumps(parsed, ensure_ascii=False),
                parsed=parsed,
                finish_reason="stop",
            )
        return ModelResponse(
            provider_name=self.provider_name,
            model=self.config.model,
            content=content or "No relevant evidence found.",
            finish_reason="stop",
        )

    def _structured_from_prompt(
        self,
        prompt: str,
        schema: Dict[str, Any],
        *,
        tools: Sequence[ToolDefinition],
    ) -> Any:
        properties = schema.get("properties", {})
        if "summary" in properties:
            passages = []
            payload = _coerce_json(prompt)
            if isinstance(payload, dict):
                passages = payload.get("passages", [])
                prompt_text = str(payload.get("prompt", ""))
            else:
                prompt_text = prompt
            summary = " ".join(summarize_passages(prompt_text, passages, max_sentences=5))
            return {"summary": summary or normalize_text(prompt_text)}
        if "label" in properties and "enum" in properties["label"]:
            labels = properties["label"]["enum"]
            terms = set(keyword_terms(prompt))
            chosen = next((label for label in labels if label.lower() in prompt.lower()), labels[0])
            if terms:
                for label in labels:
                    if any(term in label.lower() for term in terms):
                        chosen = label
                        break
            return {"label": chosen, "reason": "deterministic fallback selected the closest matching label"}
        if "ok" in properties and "provider" in properties:
            return {"ok": True, "provider": self.provider_name}
        if "action" in properties:
            if tools:
                tool_name = tools[0].name
                arguments: Dict[str, Any] = {}
                payload = _coerce_json(prompt)
                if tool_name == "search_context":
                    if isinstance(payload, dict):
                        arguments["query"] = payload.get("task") or payload.get("query") or normalize_text(prompt)
                    else:
                        arguments["query"] = normalize_text(prompt)
                elif tool_name == "fetch_url" and isinstance(payload, dict) and payload.get("urls"):
                    arguments["url"] = payload["urls"][0]
                elif tool_name == "crawl_urls" and isinstance(payload, dict) and payload.get("urls"):
                    arguments["urls"] = payload["urls"]
                return {"action": "tool", "tool_name": tool_name, "arguments": arguments}
            return {"action": "final", "answer": normalize_text(prompt)}
        if "text" in properties:
            return {"text": normalize_text(prompt)}
        if "answer" in properties:
            return {"answer": normalize_text(prompt)}
        if "steps" in properties:
            steps: List[Dict[str, Any]] = []
            lowered = prompt.lower()
            if "fetch" in lowered or "crawl" in lowered:
                steps.append({"goal": "Collect external evidence", "tool": "fetch_url"})
            if "search" in lowered or "repo" in lowered or "code" in lowered:
                steps.append({"goal": "Search indexed context", "tool": "search_context"})
            steps.append({"goal": "Summarize findings", "tool": None})
            return {"steps": steps}
        return {}


class HttpModelProvider(BaseModelProvider):
    def __init__(self, provider_name: str, config: ModelProviderConfig):
        super().__init__(provider_name, config)
        base_url = _resolve_base_url(config)
        if not base_url:
            raise ProviderError(f"model provider {provider_name} is missing base_url/endpoint")
        self.base_url = base_url
        self.api_key = _resolve_api_key(config)

    def _request_json(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        headers = dict(self.config.headers)
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        timeout = httpx.Timeout(self.config.timeout_seconds)
        last_error: Optional[Exception] = None
        for attempt in range(self.config.retry_policy.attempts + 1):
            try:
                with httpx.Client(timeout=timeout, headers=headers) as client:
                    response = client.request(method, f"{self.base_url}{path}", json=payload)
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < self.config.retry_policy.attempts:
                    _sleep_with_backoff(
                        self.config.retry_policy.backoff_seconds,
                        self.config.retry_policy.max_backoff_seconds,
                        attempt + 1,
                    )
        raise ProviderError(f"request failed for {self.provider_name}{path}: {last_error}")


class OpenAICompatibleModelProvider(HttpModelProvider):
    def capabilities(self) -> Dict[str, bool]:
        return {
            "tool_calling": True,
            "json_schema": True,
            "chat": True,
        }

    def list_models(self) -> List[Dict[str, Any]]:
        payload = self._request_json("GET", "/models")
        return list(payload.get("data", []))

    def chat(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        tools: Optional[Sequence[ToolDefinition]] = None,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> ModelResponse:
        body: Dict[str, Any] = {
            "model": self.config.model,
            "messages": list(messages),
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
        if response_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "ena_structured_response",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        if self.config.extra_body:
            body.update(self.config.extra_body)
        payload = self._request_json("POST", "/chat/completions", body)
        choice = payload.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = _extract_content_from_message(message.get("content", ""))
        tool_calls = []
        for item in message.get("tool_calls", []) or []:
            arguments = _coerce_json(item.get("function", {}).get("arguments", "{}"))
            if not isinstance(arguments, dict):
                arguments = {}
            tool_calls.append(
                ToolCall(
                    name=item.get("function", {}).get("name", ""),
                    arguments=arguments,
                    call_id=item.get("id"),
                )
            )
        parsed = _ensure_json(content, response_schema) if response_schema else None
        return ModelResponse(
            provider_name=self.provider_name,
            model=self.config.model,
            content=content,
            parsed=parsed,
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason"),
            usage=payload.get("usage", {}),
            raw=payload,
        )


class OllamaModelProvider(HttpModelProvider):
    def capabilities(self) -> Dict[str, bool]:
        return {
            "tool_calling": True,
            "json_schema": True,
            "chat": True,
        }

    def list_models(self) -> List[Dict[str, Any]]:
        payload = self._request_json("GET", "/api/tags")
        return list(payload.get("models", []))

    def chat(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        tools: Optional[Sequence[ToolDefinition]] = None,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> ModelResponse:
        body: Dict[str, Any] = {
            "model": self.config.model,
            "messages": list(messages),
            "stream": False,
            "options": {
                "temperature": self.config.temperature,
                "num_predict": self.config.max_tokens,
            },
        }
        if response_schema:
            body["format"] = response_schema
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
        if self.config.extra_body:
            body.update(self.config.extra_body)
        payload = self._request_json("POST", "/api/chat", body)
        message = payload.get("message", {})
        content = _extract_content_from_message(message.get("content", ""))
        tool_calls = []
        for item in message.get("tool_calls", []) or []:
            function = item.get("function", {}) if isinstance(item, dict) else {}
            arguments = _coerce_json(function.get("arguments", "{}"))
            if not isinstance(arguments, dict):
                arguments = {}
            tool_calls.append(
                ToolCall(
                    name=function.get("name", ""),
                    arguments=arguments,
                    call_id=item.get("id"),
                )
            )
        parsed = _ensure_json(content, response_schema) if response_schema else None
        return ModelResponse(
            provider_name=self.provider_name,
            model=self.config.model,
            content=content,
            parsed=parsed,
            tool_calls=tool_calls,
            finish_reason="stop" if payload.get("done") else None,
            usage={
                "prompt_eval_count": payload.get("prompt_eval_count"),
                "eval_count": payload.get("eval_count"),
            },
            raw=payload,
        )


class BaseEmbeddingProvider:
    def __init__(self, provider_name: str, config: EmbeddingProviderConfig):
        self.provider_name = provider_name
        self.config = config

    def capabilities(self) -> Dict[str, Any]:
        return {"semantic": True, "provider": self.provider_name, "model": self.config.model}

    def test(self) -> Dict[str, Any]:
        vectors = self.embed_texts(["animica semantic search smoke test", "another sample"])
        dims = len(vectors[0]) if vectors else 0
        return {
            "provider": self.provider_name,
            "model": self.config.model,
            "ok": bool(vectors and dims > 0),
            "dimensions": dims,
        }

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        raise NotImplementedError


class DisabledEmbeddingProvider(BaseEmbeddingProvider):
    def capabilities(self) -> Dict[str, Any]:
        return {"semantic": False, "provider": self.provider_name, "model": self.config.model}

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        raise ProviderError("embedding provider is disabled")


class HashingEmbeddingProvider(BaseEmbeddingProvider):
    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for text in texts:
            raw = [0.0] * 64
            for token in keyword_terms(text):
                digest = sha256_hex(token)
                index = int(digest[:4], 16) % len(raw)
                sign = 1.0 if int(digest[4:6], 16) % 2 == 0 else -1.0
                raw[index] += sign
            norm = math.sqrt(sum(value * value for value in raw))
            if norm:
                vectors.append([value / norm for value in raw])
            else:
                vectors.append(raw)
        return vectors


class HttpEmbeddingProvider(BaseEmbeddingProvider):
    def __init__(self, provider_name: str, config: EmbeddingProviderConfig):
        super().__init__(provider_name, config)
        base_url = _resolve_base_url(config)
        if not base_url:
            raise ProviderError(f"embedding provider {provider_name} is missing base_url/endpoint")
        self.base_url = base_url
        self.api_key = _resolve_api_key(config)

    def _request_json(self, method: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = dict(self.config.headers)
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        timeout = httpx.Timeout(self.config.timeout_seconds)
        last_error: Optional[Exception] = None
        for attempt in range(self.config.retry_policy.attempts + 1):
            try:
                with httpx.Client(timeout=timeout, headers=headers) as client:
                    response = client.request(method, f"{self.base_url}{path}", json=payload)
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < self.config.retry_policy.attempts:
                    _sleep_with_backoff(
                        self.config.retry_policy.backoff_seconds,
                        self.config.retry_policy.max_backoff_seconds,
                        attempt + 1,
                    )
        raise ProviderError(f"request failed for {self.provider_name}{path}: {last_error}")


class OpenAICompatibleEmbeddingProvider(HttpEmbeddingProvider):
    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        body: Dict[str, Any] = {"model": self.config.model, "input": list(texts)}
        if self.config.dimensions:
            body["dimensions"] = self.config.dimensions
        if self.config.extra_body:
            body.update(self.config.extra_body)
        payload = self._request_json("POST", "/embeddings", body)
        items = sorted(payload.get("data", []), key=lambda item: item.get("index", 0))
        return [list(item.get("embedding", [])) for item in items]


class OllamaEmbeddingProvider(HttpEmbeddingProvider):
    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        body: Dict[str, Any] = {"model": self.config.model, "input": list(texts)}
        if self.config.extra_body:
            body.update(self.config.extra_body)
        try:
            payload = self._request_json("POST", "/api/embed", body)
            embeddings = payload.get("embeddings", [])
            if embeddings:
                return [list(item) for item in embeddings]
        except ProviderError:
            pass
        results: List[List[float]] = []
        for text in texts:
            payload = self._request_json("POST", "/api/embeddings", {"model": self.config.model, "prompt": text})
            results.append(list(payload.get("embedding", [])))
        return results


def create_model_provider(config: EnaConfigModel, provider_name: Optional[str] = None) -> BaseModelProvider:
    name = provider_name or config.default_model_provider
    provider_config = config.model_providers.get(name)
    if provider_config is None:
        raise ProviderError(f"unknown model provider: {name}")
    if provider_config.provider == "deterministic":
        return DeterministicModelProvider(name, provider_config)
    if provider_config.provider == "openai_compatible":
        return OpenAICompatibleModelProvider(name, provider_config)
    if provider_config.provider == "ollama":
        return OllamaModelProvider(name, provider_config)
    raise ProviderError(f"unsupported model provider type: {provider_config.provider}")


def create_embedding_provider(config: EnaConfigModel, provider_name: Optional[str] = None) -> BaseEmbeddingProvider:
    name = provider_name or config.default_embedding_provider
    provider_config = config.embedding_providers.get(name)
    if provider_config is None:
        raise ProviderError(f"unknown embedding provider: {name}")
    if provider_config.provider == "disabled":
        return DisabledEmbeddingProvider(name, provider_config)
    if provider_config.provider == "hashing":
        return HashingEmbeddingProvider(name, provider_config)
    if provider_config.provider == "openai_compatible":
        return OpenAICompatibleEmbeddingProvider(name, provider_config)
    if provider_config.provider == "ollama":
        return OllamaEmbeddingProvider(name, provider_config)
    raise ProviderError(f"unsupported embedding provider type: {provider_config.provider}")
