"""
Authentication Service Configuration
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Database
    DATABASE_URL: str = "postgresql://animica:animica_dev_password@localhost:5432/animica_compute"
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/1"
    
    # JWT Configuration
    JWT_SECRET_KEY: str = "dev_jwt_secret_change_in_production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRATION_DAYS: int = 30
    
    # Animica Blockchain
    ANIMICA_RPC_URL: str = "http://localhost:8545"
    
    # OAuth2 (optional)
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GITHUB_CLIENT_ID: Optional[str] = None
    GITHUB_CLIENT_SECRET: Optional[str] = None
    
    # Security
    API_KEY_PREFIX: str = "anm_"
    BCRYPT_ROUNDS: int = 12
    WALLET_CHALLENGE_TTL: int = 300  # 5 minutes
    
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8001
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"
    
    # Environment
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    
    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
