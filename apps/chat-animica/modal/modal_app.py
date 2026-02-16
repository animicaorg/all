from __future__ import annotations

import os
import uuid
from typing import Any

import modal

APP_NAME = os.getenv("MODAL_APP_NAME", "chat-animica-llm")
app = modal.App(APP_NAME)
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install_from_requirements("requirements.txt")
)


@app.function(image=image, timeout=60)
@modal.asgi_app()
def web_app():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field

    from guardrails import ANIMICA_STRICT_GUARDRAILS, enforce_output
    from provider import LLMProvider, ProviderError

    service = FastAPI(title="chat-animica-llm", version="1.0.0")
    provider = LLMProvider()

    class ChatRequest(BaseModel):
        prompt: str = Field(min_length=1)
        mode: str = "strict"
        context: dict[str, Any] | None = None

    class ContractRequest(ChatRequest):
        pass

    async def _handle(prompt: str, mode: str, context: dict[str, Any] | None):
        system_prompt = f"{ANIMICA_STRICT_GUARDRAILS}\nMode={mode}"
        raw = await provider.complete(system_prompt=system_prompt, user_prompt=prompt, context=context)
        safe = enforce_output(raw)
        return {
            "content": safe.content,
            "abi": safe.abi,
            "manifest": safe.manifest,
            "requestId": str(raw.get("requestId") or uuid.uuid4()),
        }

    @service.get("/health")
    async def health_check():
        return {"ok": True, "app": APP_NAME}

    @service.post("/v1/chat")
    async def chat(payload: ChatRequest):
        try:
            return await _handle(payload.prompt, payload.mode, payload.context)
        except ProviderError as exc:
            return JSONResponse(status_code=502, content={"error": str(exc), "requestId": str(uuid.uuid4())})

    @service.post("/v1/contracts/generate")
    async def generate_contract(payload: ContractRequest):
        try:
            return await _handle(payload.prompt, payload.mode, payload.context)
        except ProviderError as exc:
            return JSONResponse(status_code=502, content={"error": str(exc), "requestId": str(uuid.uuid4())})

    return service
