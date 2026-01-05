"""
Inference Service Configuration
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings"""
    
    # Model Configuration
    MODEL_PATH: str = "/models"
    MODEL_CACHE_DIR: str = "/tmp/model_cache"
    DEFAULT_MODEL: str = "gpt2"  # CPU fallback model
    AVAILABLE_MODELS: str = "gpt2,distilgpt2"  # Comma-separated
    
    # Inference Configuration
    GPU_ENABLED: bool = False
    MAX_MODEL_LEN: int = 4096
    GPU_MEMORY_UTILIZATION: float = 0.9
    TENSOR_PARALLEL_SIZE: int = 1
    MAX_BATCH_SIZE: int = 32
    
    # Redis for caching
    REDIS_URL: str = "redis://localhost:6379/3"
    
    # Billing integration
    BILLING_SERVICE_URL: str = "http://localhost:8002"
    
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8003
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    # Environment
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    
    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
