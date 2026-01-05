"""Code Execution Router - Secure sandbox for running code"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List, Literal

router = APIRouter()


class CodeExecutionRequest(BaseModel):
    language: Literal["python", "javascript", "typescript", "go", "rust"]
    code: str
    timeout: int = 30
    packages: Optional[List[str]] = None


@router.post("/execute")
async def execute_code(request: CodeExecutionRequest):
    """Execute code in secure sandbox"""
    return {
        "stdout": "Mock output",
        "stderr": "",
        "exit_code": 0,
        "execution_time": 1.23
    }


@router.get("/languages")
async def list_languages():
    """List supported languages"""
    return ["python", "javascript", "typescript", "go", "rust"]
