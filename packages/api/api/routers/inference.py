"""
LLM Inference Router

OpenAI-compatible API for chat completions, text completions, and embeddings.
Proxies requests to the inference service.
"""

from fastapi import APIRouter, HTTPException, status, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Literal
import httpx
import json
import os

router = APIRouter()

# Inference service URL from environment
INFERENCE_SERVICE_URL = os.getenv("INFERENCE_SERVICE_URL", "http://inference-service:8003")
BILLING_SERVICE_URL = os.getenv("BILLING_SERVICE_URL", "http://billing-service:8002")


class Message(BaseModel):
    """Chat message"""
    role: Literal["system", "user", "assistant"]
    content: str


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request"""
    model: str = "llama-3-8b-instruct"
    messages: List[Message]
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=4096)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    stream: bool = False
    stop: Optional[List[str]] = None


class CompletionRequest(BaseModel):
    """Text completion request"""
    model: str = "llama-3-8b-instruct"
    prompt: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=16, ge=1, le=4096)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    stream: bool = False
    stop: Optional[List[str]] = None


class EmbeddingRequest(BaseModel):
    """Embedding generation request"""
    model: str = "text-embedding-ada-002"
    input: str | List[str]


@router.post("/chat/completions")
async def chat_completions(request: ChatCompletionRequest, http_request: Request):
    """
    Create a chat completion (OpenAI-compatible).
    
    Supports streaming via Server-Sent Events when stream=True.
    """
    # Check user authentication
    user_id = getattr(http_request.state, "user_id", None)
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    # Check balance (optional - billing service can handle this)
    # For now, we'll let the inference service handle it
    
    async with httpx.AsyncClient() as client:
        try:
            # Forward request to inference service
            headers = {
                "X-User-ID": user_id,
                "Content-Type": "application/json"
            }
            
            response = await client.post(
                f"{INFERENCE_SERVICE_URL}/v1/chat/completions",
                json=request.dict(),
                headers=headers,
                timeout=120.0
            )
            response.raise_for_status()
            
            if request.stream:
                # Stream response back to client
                async def event_generator():
                    async for chunk in response.aiter_bytes():
                        yield chunk
                
                return StreamingResponse(
                    event_generator(),
                    media_type="text/event-stream"
                )
            else:
                # Return complete response
                return response.json()
                
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Inference request failed")
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Inference service unavailable: {str(e)}"
            )


@router.post("/completions")
async def completions(request: CompletionRequest, http_request: Request):
    """
    Create a text completion (OpenAI-compatible).
    """
    # Check user authentication
    user_id = getattr(http_request.state, "user_id", None)
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    async with httpx.AsyncClient() as client:
        try:
            headers = {
                "X-User-ID": user_id,
                "Content-Type": "application/json"
            }
            
            response = await client.post(
                f"{INFERENCE_SERVICE_URL}/v1/completions",
                json=request.dict(),
                headers=headers,
                timeout=120.0
            )
            response.raise_for_status()
            
            if request.stream:
                async def event_generator():
                    async for chunk in response.aiter_bytes():
                        yield chunk
                
                return StreamingResponse(
                    event_generator(),
                    media_type="text/event-stream"
                )
            else:
                return response.json()
                
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Completion request failed")
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Inference service unavailable: {str(e)}"
            )


@router.post("/embeddings")
async def embeddings(request: EmbeddingRequest, http_request: Request):
    """
    Generate embeddings for input text.
    """
    # Check user authentication
    user_id = getattr(http_request.state, "user_id", None)
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    async with httpx.AsyncClient() as client:
        try:
            headers = {
                "X-User-ID": user_id,
                "Content-Type": "application/json"
            }
            
            response = await client.post(
                f"{INFERENCE_SERVICE_URL}/v1/embeddings",
                json=request.dict(),
                headers=headers,
                timeout=60.0
            )
            response.raise_for_status()
            return response.json()
                
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Embeddings request failed")
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Inference service unavailable: {str(e)}"
            )
