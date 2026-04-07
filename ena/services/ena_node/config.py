"""
Configuration for ENA node service.
"""

import os
from pathlib import Path


class Config:
    """ENA node configuration."""
    
    # RPC
    RPC_URL = os.getenv("ENA_RPC_URL", "https://mainnet.animica.org/rpc")
    RPC_TIMEOUT = int(os.getenv("ENA_RPC_TIMEOUT", "30"))
    RPC_MAX_RETRIES = int(os.getenv("ENA_RPC_MAX_RETRIES", "3"))
    RPC_RETRY_BACKOFF = float(os.getenv("ENA_RPC_RETRY_BACKOFF", "2.0"))
    
    # Service
    SERVICE_ADDRESS = os.getenv(
        "ENA_SERVICE_ADDRESS",
        "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq000000"
    )
    
    # AICF (AI Compute Fund)
    AICF_ADDRESS = os.getenv(
        "ENA_AICF_ADDRESS",
        "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq111111"
    )
    AICF_BP = int(os.getenv("ENA_AICF_BP", "2500"))  # Basis points (2500 = 25%)
    REQUIRE_AICF = os.getenv("ENA_REQUIRE_AICF", "true").lower() in ("true", "1", "yes")
    
    # Payment mode
    PAYMENT_MODE = os.getenv("ENA_PAYMENT_MODE", "per_call_tx")
    
    # Pricing (base units: 1 ANM = 1e9)
    FEE_PER_CALL = int(os.getenv("ENA_FEE_PER_CALL", "10000000"))  # 0.01 ANM
    FEE_PER_TOKEN = int(os.getenv("ENA_FEE_PER_TOKEN", "1000"))    # 0.000001 ANM
    MIN_FEE_PER_CALL = int(os.getenv("ENA_MIN_FEE_PER_CALL", "10000000"))  # Minimum total
    
    # Database
    DB_PATH = os.getenv("ENA_DB_PATH", "./ena_data/ena.db")
    TRAINING_DIR = os.getenv("ENA_TRAINING_DIR", "./ena_data/training")
    CHECKPOINTS_DIR = os.getenv("ENA_CHECKPOINTS_DIR", "./ena_data/checkpoints")
    
    # Models
    DEFAULT_MODEL = os.getenv("ENA_DEFAULT_MODEL", "ena.tiny.v1")
    MODELS_DIR = os.getenv("ENA_MODELS_DIR", "./ena/models")
    
    # Server
    HOST = os.getenv("ENA_HOST", "0.0.0.0")
    PORT = int(os.getenv("ENA_PORT", "8080"))
    
    # Rate limiting
    RATE_LIMIT_PER_ADDRESS = int(os.getenv("ENA_RATE_LIMIT_PER_ADDRESS", "100"))
    RATE_LIMIT_PER_IP = int(os.getenv("ENA_RATE_LIMIT_PER_IP", "200"))
    
    # Circuit breaker
    CIRCUIT_BREAKER_THRESHOLD = int(os.getenv("ENA_CIRCUIT_BREAKER_THRESHOLD", "5"))
    CIRCUIT_BREAKER_TIMEOUT = int(os.getenv("ENA_CIRCUIT_BREAKER_TIMEOUT", "60"))
    
    # Admin
    ADMIN_TOKEN = os.getenv("ENA_ADMIN_TOKEN", "change_me_in_production")
    
    # Limits
    MAX_PROMPT_LENGTH = int(os.getenv("ENA_MAX_PROMPT_LENGTH", "2000"))
    MAX_TOKENS_PER_CALL = int(os.getenv("ENA_MAX_TOKENS_PER_CALL", "500"))
    
    # Logging
    LOG_LEVEL = os.getenv("ENA_LOG_LEVEL", "INFO")
    LOG_FILE = os.getenv("ENA_LOG_FILE", "./ena_data/ena.log")
    
    # Development mode (NEVER use in production!)
    DEV_MODE = os.getenv("ENA_DEV_MODE", "0") == "1"
    
    @classmethod
    def ensure_dirs(cls):
        """Ensure necessary directories exist."""
        db_dir = Path(cls.DB_PATH).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        training_dir = Path(cls.TRAINING_DIR)
        training_dir.mkdir(parents=True, exist_ok=True)

        checkpoints_dir = Path(cls.CHECKPOINTS_DIR)
        checkpoints_dir.mkdir(parents=True, exist_ok=True)

        log_dir = Path(cls.LOG_FILE).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        
        models_dir = Path(cls.MODELS_DIR)
        models_dir.mkdir(parents=True, exist_ok=True)
