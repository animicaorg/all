"""
Billing Service Configuration
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings"""
    
    # Database
    DATABASE_URL: str = "postgresql://animica:animica_dev_password@localhost:5432/animica_compute"
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/2"
    
    # Stripe
    STRIPE_API_KEY: str = "sk_test_example"
    STRIPE_WEBHOOK_SECRET: str = "whsec_example"
    STRIPE_PUBLISHABLE_KEY: str = "pk_test_example"
    
    # PayPal
    PAYPAL_MODE: str = "sandbox"  # or 'live'
    PAYPAL_CLIENT_ID: Optional[str] = None
    PAYPAL_CLIENT_SECRET: Optional[str] = None
    PAYPAL_WEBHOOK_ID: Optional[str] = None
    
    # ANM Payments
    ANIMICA_RPC_URL: str = "http://localhost:8545"
    ANM_PAYMENT_ENABLED: bool = True
    ANM_PAYMENT_CONFIRMATION_BLOCKS: int = 6
    
    # Credits
    DEFAULT_FREE_CREDITS: int = 1000
    CREDIT_TO_USD_RATE: float = 0.01
    
    # Plans
    PLAN_STARTER_CREDITS: int = 1000
    PLAN_PRO_CREDITS: int = 50000
    PLAN_PRO_PRICE_USD: int = 2900  # cents
    
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8002
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    # Environment
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    
    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
