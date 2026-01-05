"""
Billing Service - Main Application
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging
import time
from billing_service.config import settings
from billing_service.database import init_db, close_db
from billing_service.routers import billing, webhooks

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    logger.info("Starting Billing Service")
    
    # Initialize database
    try:
        await init_db()
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
    
    yield
    
    # Cleanup
    await close_db()
    logger.info("Shutting down Billing Service")


# Create FastAPI application
app = FastAPI(
    title="Animica Billing Service",
    description="Billing and payments service with Stripe, PayPal, and ANM support",
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


# Request logging middleware
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """Log all requests"""
    start_time = time.time()
    
    response = await call_next(request)
    
    duration = time.time() - start_time
    logger.info(
        f"{request.method} {request.url.path} - "
        f"Status: {response.status_code} - "
        f"Duration: {duration:.3f}s"
    )
    
    response.headers["X-Process-Time"] = str(duration)
    return response


# Exception handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle all uncaught exceptions"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_server_error",
                "message": "An internal error occurred"
            }
        }
    )


# Health check
@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "billing-service",
        "version": "1.0.0",
        "timestamp": int(time.time())
    }


@app.get("/", tags=["System"])
async def root():
    """Root endpoint"""
    return {
        "name": "Animica Billing Service",
        "version": "1.0.0",
        "docs": "/docs"
    }


# Include routers
app.include_router(billing.router, prefix="/billing", tags=["Billing"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["Webhooks"])


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "billing_service.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
