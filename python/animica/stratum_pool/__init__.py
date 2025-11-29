"""Stratum mining pool backend for Animica."""

from .config import PoolConfig, load_config_from_env
from .cli import main

__all__ = ["PoolConfig", "load_config_from_env", "main"]
