"""
API Gateway Configuration

Settings and configuration for the API Gateway service.
"""

from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    """Application settings"""
    
    # Application
    APP_NAME: str = "Animica API Gateway"
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    
    # API Server
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_DOCS_ENABLED: bool = True
    
    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:5173"]
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"
    
    # Database
    DATABASE_URL: str = "postgresql://animica:password@localhost:5432/animica_compute"
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # Service URLs
    AUTH_SERVICE_URL: str = "http://localhost:8001"
    BILLING_SERVICE_URL: str = "http://localhost:8002"
    INFERENCE_SERVICE_URL: str = "http://localhost:8003"
    SANDBOX_SERVICE_URL: str = "http://localhost:8004"
    GITHUB_SERVICE_URL: str = "http://localhost:8005"
    MODEL_REGISTRY_URL: str = "http://localhost:8006"
    
    # Authentication
    JWT_SECRET_KEY: str = "dev_secret_change_in_production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 15
    
    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 100
    RATE_LIMIT_BURST: int = 20
    
    # Security
    HSTS_ENABLED: bool = True
    CSP_ENABLED: bool = True
    
    class Config:
        env_file = ".env"
        case_sensitive = True


# Create settings instance
settings = Settings()
