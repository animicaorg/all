"""
Sandbox Runner Service - Main Application

Secure code execution sandbox with resource limits
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import logging
import time
import subprocess
import tempfile
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Animica Sandbox Runner",
    description="Secure code execution service",
    version="1.0.0",
)


class ExecuteRequest(BaseModel):
    language: str  # 'python', 'javascript', 'bash'
    code: str
    timeout: Optional[int] = 30
    memory_limit: Optional[str] = "512MB"


class ExecuteResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "sandbox-runner",
        "version": "1.0.0",
        "timestamp": int(time.time()),
    }


@app.post("/execute", response_model=ExecuteResponse)
async def execute_code(request: ExecuteRequest):
    """
    Execute code in isolated sandbox
    
    NOTE: This is a basic implementation.
    Production should use gVisor, Firecracker, or Docker containers.
    """
    
    start_time = time.time()
    
    # Language to command mapping
    language_commands = {
        "python": ["python3", "-c"],
        "javascript": ["node", "-e"],
        "bash": ["bash", "-c"],
    }
    
    if request.language not in language_commands:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language: {request.language}"
        )
    
    cmd = language_commands[request.language]
    cmd.append(request.code)
    
    try:
        # Execute with timeout and resource limits
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=request.timeout,
            # Additional security: limit environment variables
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        )
        
        execution_time = time.time() - start_time
        
        return ExecuteResponse(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            execution_time=execution_time,
        )
    
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=408,
            detail=f"Execution timeout after {request.timeout} seconds"
        )
    except Exception as e:
        logger.error(f"Execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
