from __future__ import annotations

import os
import uuid
from typing import Any

import httpx


class ProviderError(RuntimeError):
    pass


class LLMProvider:
    def __init__(self) -> None:
        self.model = os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.max_tokens = int(os.getenv("LLM_MAX_TOKENS", "1200"))
        self.temperature = float(os.getenv("LLM_TEMPERATURE", "0.2"))
        self.openai_key = os.getenv("OPENAI_API_KEY", "").strip()

    async def complete(self, system_prompt: str, user_prompt: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.openai_key:
            try:
                return await self._openai_complete(system_prompt, user_prompt, context)
            except Exception as exc:  # pragma: no cover - defensive fallback
                raise ProviderError(f"OpenAI provider failed: {exc}") from exc
        return self._stub_complete(user_prompt, context)

    async def _openai_complete(self, system_prompt: str, user_prompt: str, context: dict[str, Any] | None) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if context:
            payload["messages"].append({"role": "system", "content": f"context={context}"})

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.openai_key}", "Content-Type": "application/json"},
                json=payload,
            )

        if response.status_code >= 400:
            raise ProviderError(f"OpenAI HTTP {response.status_code}: {response.text[:500]}")

        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {
            "content": content,
            "abi": [],
            "manifest": {"provider": "openai", "model": self.model},
            "requestId": str(uuid.uuid4()),
        }

    def _stub_complete(self, user_prompt: str, context: dict[str, Any] | None) -> dict[str, Any]:
        compact_prompt = user_prompt[:80].replace("\n", " ")
        return {
            "content": (
                "contract Generated {\n"
                f"  // stub fallback generated for prompt: {compact_prompt}\n"
                "  fn version() -> string { return \"animica-modal-stub\" }\n"
                "}"
            ),
            "abi": [{"name": "version", "type": "function"}],
            "manifest": {"provider": "stub", "context": context or {}},
            "requestId": str(uuid.uuid4()),
        }
