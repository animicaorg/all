"""Stratum mining pool backend for Animica."""

from .cli import main
from .config import PoolConfig, load_config_from_env

__all__ = ["PoolConfig", "load_config_from_env", "main"]
