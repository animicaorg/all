"""Configuration for Animica Bridge"""

import os
from dataclasses import dataclass


@dataclass
class Settings:
    """Bridge service configuration"""
    
    # Animica blockchain connection
    ANIMICA_RPC_URL: str = os.getenv("ANIMICA_RPC_URL", "http://localhost:8545")
    ANIMICA_CHAIN_ID: int = int(os.getenv("ANIMICA_CHAIN_ID", "1337"))
    
    # Bridge wallet (for signing transactions)
    BRIDGE_PRIVATE_KEY: str = os.getenv("BRIDGE_PRIVATE_KEY", "")
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://animica:animica_dev_password@postgres:5432/animica_compute")
    
    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    
    # Monitoring
    POLLING_INTERVAL: int = int(os.getenv("POLLING_INTERVAL", "12"))  # seconds
    CONFIRMATION_BLOCKS: int = int(os.getenv("CONFIRMATION_BLOCKS", "3"))
    
    # Payment processing
    ANM_DECIMALS: int = 8  # ANM token decimals
    MIN_PAYMENT_AMOUNT: int = 1_000_000  # Minimum payment (0.01 ANM)
    
    # Credits conversion
    CREDITS_PER_ANM: float = float(os.getenv("CREDITS_PER_ANM", "1000.0"))
    
    # Contributor rewards
    MIN_REWARD_AMOUNT: int = 10_000_000  # Minimum reward (0.1 ANM)
    REWARD_BATCH_SIZE: int = 50  # Max rewards per transaction


settings = Settings()
