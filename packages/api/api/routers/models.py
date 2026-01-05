"""Models Router - Model registry and management"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_models():
    """List available models (OpenAI-compatible)"""
    return {
        "object": "list",
        "data": [
            {
                "id": "llama-3-8b-instruct",
                "object": "model",
                "created": 1677652288,
                "owned_by": "animica"
            },
            {
                "id": "mistral-7b-instruct",
                "object": "model",
                "created": 1677652288,
                "owned_by": "animica"
            }
        ]
    }


@router.get("/{model_id}")
async def get_model(model_id: str):
    """Get model details"""
    return {
        "id": model_id,
        "object": "model",
        "created": 1677652288,
        "owned_by": "animica"
    }
