"""
Inference Service - Main Application

OpenAI-compatible API for LLM inference
"""

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
import logging
import time
import json
import asyncio

from inference.config import settings
from inference.models.manager import model_manager

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    logger.info("Starting Inference Service")
    
    # Load default model
    if settings.DEFAULT_MODEL:
        logger.info(f"Pre-loading default model: {settings.DEFAULT_MODEL}")
        model_manager.load_model(settings.DEFAULT_MODEL)
    
    yield
    
    # Cleanup
    logger.info("Shutting down Inference Service")


# Create FastAPI application
app = FastAPI(
    title="Animica Inference Service",
    description="OpenAI-compatible LLM inference service",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request/Response Models (OpenAI-compatible)
class Message(BaseModel):
    role: str  # 'system', 'user', 'assistant'
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Message]
    max_tokens: Optional[int] = 100
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = 0.9
    n: Optional[int] = 1
    stream: Optional[bool] = False
    stop: Optional[List[str]] = None


class Choice(BaseModel):
    index: int
    message: Message
    finish_reason: str


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[Choice]
    usage: Usage


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str = "animica"


# Helper functions
async def get_user_id(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> Optional[str]:
    """Extract user ID from auth (mock for now)"""
    # TODO: Integrate with auth service
    if authorization or x_api_key:
        return "00000000-0000-0000-0000-000000000001"  # Mock user
    return None


def format_prompt(messages: List[Message]) -> str:
    """Format messages into a single prompt"""
    prompt_parts = []
    for msg in messages:
        if msg.role == "system":
            prompt_parts.append(f"System: {msg.content}")
        elif msg.role == "user":
            prompt_parts.append(f"User: {msg.content}")
        elif msg.role == "assistant":
            prompt_parts.append(f"Assistant: {msg.content}")
    
    prompt_parts.append("Assistant:")
    return "\n".join(prompt_parts)


# Endpoints
@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint"""
    loaded_models = model_manager.list_loaded_models()
    return {
        "status": "healthy",
        "service": "inference-service",
        "version": "1.0.0",
        "timestamp": int(time.time()),
        "gpu_enabled": settings.GPU_ENABLED,
        "loaded_models": loaded_models,
    }


@app.get("/", tags=["System"])
async def root():
    """Root endpoint"""
    return {
        "name": "Animica Inference Service",
        "version": "1.0.0",
        "docs": "/docs"
    }


@app.get("/v1/models", tags=["Models"])
async def list_models():
    """List available models (OpenAI-compatible)"""
    available = settings.AVAILABLE_MODELS.split(",")
    
    return {
        "object": "list",
        "data": [
            ModelInfo(
                id=model,
                created=int(time.time()),
            )
            for model in available
        ]
    }


@app.post("/v1/chat/completions", tags=["Chat"])
async def chat_completions(
    request: ChatCompletionRequest,
    user_id: Optional[str] = Header(None, alias="X-User-ID")
):
    """
    Create chat completion (OpenAI-compatible)
    
    Supports streaming and non-streaming responses.
    """
    
    try:
        # Format messages into prompt
        prompt = format_prompt(request.messages)
        
        # Count prompt tokens
        prompt_tokens = model_manager.count_tokens(request.model, prompt)
        
        if request.stream:
            # Streaming response
            async def generate_stream():
                """Generate streaming response"""
                
                # Generate text (in real implementation, would stream token by token)
                generated_text = model_manager.generate(
                    model_name=request.model,
                    prompt=prompt,
                    max_tokens=request.max_tokens or 100,
                    temperature=request.temperature or 0.7,
                    top_p=request.top_p or 0.9,
                )
                
                # Simulate streaming by sending chunks
                words = generated_text.split()
                for i, word in enumerate(words):
                    chunk = {
                        "id": f"chatcmpl-{int(time.time())}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": request.model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "content": word + " " if i < len(words) - 1 else word
                                },
                                "finish_reason": None
                            }
                        ]
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
                    await asyncio.sleep(0.05)  # Simulate streaming delay
                
                # Send final chunk
                final_chunk = {
                    "id": f"chatcmpl-{int(time.time())}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": request.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop"
                        }
                    ]
                }
                yield f"data: {json.dumps(final_chunk)}\n\n"
                yield "data: [DONE]\n\n"
            
            return StreamingResponse(
                generate_stream(),
                media_type="text/event-stream"
            )
        
        else:
            # Non-streaming response
            generated_text = model_manager.generate(
                model_name=request.model,
                prompt=prompt,
                max_tokens=request.max_tokens or 100,
                temperature=request.temperature or 0.7,
                top_p=request.top_p or 0.9,
            )
            
            completion_tokens = model_manager.count_tokens(request.model, generated_text)
            
            response = ChatCompletionResponse(
                id=f"chatcmpl-{int(time.time())}",
                created=int(time.time()),
                model=request.model,
                choices=[
                    Choice(
                        index=0,
                        message=Message(role="assistant", content=generated_text),
                        finish_reason="stop"
                    )
                ],
                usage=Usage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens
                )
            )
            
            # TODO: Record usage in billing service
            # await record_usage(user_id, prompt_tokens + completion_tokens, request.model)
            
            return response
    
    except Exception as e:
        logger.error(f"Chat completion failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=settings.HOST)
    parser.add_argument("--port", type=int, default=settings.PORT)
    args = parser.parse_args()
    
    uvicorn.run(
        "inference.main:app",
        host=args.host,
        port=args.port,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
