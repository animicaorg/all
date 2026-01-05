"""
API Gateway - Main Application

FastAPI application serving as the entry point for all Animica Compute Platform APIs.
"""

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging
import time
from typing import Optional

from api.middleware.auth import AuthMiddleware
from api.middleware.rate_limit import RateLimitMiddleware
from api.routers import auth, inference, code, billing, marketplace, models
from api.config import settings

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    logger.info("Starting Animica API Gateway")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Debug mode: {settings.DEBUG}")
    
    # Startup
    yield
    
    # Shutdown
    logger.info("Shutting down Animica API Gateway")


# Create FastAPI application
app = FastAPI(
    title="Animica Compute Platform API",
    description="API Gateway for Animica Compute + LLM Cloud Platform",
    version="1.0.0",
    docs_url="/docs" if settings.API_DOCS_ENABLED else None,
    redoc_url="/redoc" if settings.API_DOCS_ENABLED else None,
    lifespan=lifespan,
)


# Add middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)


# Custom middleware
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """Log all requests"""
    start_time = time.time()
    
    # Process request
    response = await call_next(request)
    
    # Calculate duration
    duration = time.time() - start_time
    
    # Log request
    logger.info(
        f"{request.method} {request.url.path} - "
        f"Status: {response.status_code} - "
        f"Duration: {duration:.3f}s"
    )
    
    # Add custom headers
    response.headers["X-Process-Time"] = str(duration)
    
    return response


# Exception handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle all uncaught exceptions"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "internal_server_error",
                "message": "An internal error occurred",
            }
        }
    )


# Health check endpoint
@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint for load balancers"""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": int(time.time())
    }


@app.get("/", tags=["System"])
async def root():
    """Root endpoint"""
    return {
        "name": "Animica Compute Platform API",
        "version": "1.0.0",
        "docs": "/docs" if settings.API_DOCS_ENABLED else None,
    }


# Include routers
app.include_router(auth.router, prefix="/v1/auth", tags=["Authentication"])
app.include_router(inference.router, prefix="/v1", tags=["Inference"])
app.include_router(code.router, prefix="/v1/code", tags=["Code Execution"])
app.include_router(billing.router, prefix="/v1/billing", tags=["Billing"])
app.include_router(marketplace.router, prefix="/v1/marketplace", tags=["Marketplace"])
app.include_router(models.router, prefix="/v1/models", tags=["Models"])


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "api.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
