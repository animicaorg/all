"""
Telemetry configuration.

Manages opt-in settings for ENA data collection.
Privacy-first: opt_in defaults to False.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TelemetryConfig:
    """
    Configuration for ENA telemetry.
    
    Privacy-first design:
    - opt_in defaults to False
    - User must explicitly enable
    - Can be revoked at any time
    """
    opt_in: bool = False
    user_id_hash: Optional[str] = None  # Hashed user identifier (never raw)
    collection_start_date: Optional[str] = None  # ISO8601
    
    # What to collect
    collect_prompts: bool = True
    collect_responses: bool = True
    collect_feedback: bool = True
    collect_usage_stats: bool = True
    
    # Redaction settings
    redact_emails: bool = True
    redact_long_numbers: bool = True  # Numbers > 10 digits (phone, CC, etc.)
    redact_api_keys: bool = True
    redact_urls: bool = False  # URLs are usually safe
    
    # Buffer settings
    max_buffer_size: int = 1000  # Max samples in buffer before requiring curation
    auto_curate: bool = False  # Auto-upload without manual review (not recommended)
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)
    
    def to_json(self) -> str:
        """Convert to JSON."""
        return json.dumps(self.to_dict(), indent=2)
    
    @classmethod
    def from_dict(cls, data: dict) -> TelemetryConfig:
        """Load from dictionary."""
        return cls(**data)
    
    @classmethod
    def from_json(cls, json_str: str) -> TelemetryConfig:
        """Load from JSON."""
        data = json.loads(json_str)
        return cls.from_dict(data)


def get_config_path() -> Path:
    """Get path to telemetry config file."""
    # ~/.animica/ena_telemetry.json
    animica_dir = Path.home() / ".animica"
    animica_dir.mkdir(exist_ok=True)
    return animica_dir / "ena_telemetry.json"


def load_telemetry_config() -> TelemetryConfig:
    """
    Load telemetry configuration from file.
    
    Returns default config (opt_in=False) if file doesn't exist.
    """
    config_path = get_config_path()
    
    if not config_path.exists():
        logger.info("No telemetry config found, using defaults (opt_in=False)")
        return TelemetryConfig()
    
    try:
        with open(config_path, 'r') as f:
            data = json.load(f)
        config = TelemetryConfig.from_dict(data)
        logger.info(f"Telemetry config loaded: opt_in={config.opt_in}")
        return config
    except Exception as e:
        logger.warning(f"Failed to load telemetry config: {e}, using defaults")
        return TelemetryConfig()


def save_telemetry_config(config: TelemetryConfig) -> None:
    """
    Save telemetry configuration to file.
    
    Args:
        config: TelemetryConfig to save
    """
    config_path = get_config_path()
    
    try:
        with open(config_path, 'w') as f:
            json.dump(config.to_dict(), f, indent=2)
        logger.info(f"Telemetry config saved: {config_path}")
    except Exception as e:
        logger.error(f"Failed to save telemetry config: {e}")
        raise


def is_telemetry_enabled() -> bool:
    """
    Check if telemetry is enabled.
    
    Returns:
        True if user has opted in, False otherwise
    """
    config = load_telemetry_config()
    return config.opt_in


def enable_telemetry(user_id_hash: str) -> None:
    """
    Enable telemetry collection.
    
    Args:
        user_id_hash: Hashed user identifier
    """
    from datetime import datetime
    
    config = load_telemetry_config()
    config.opt_in = True
    config.user_id_hash = user_id_hash
    config.collection_start_date = datetime.utcnow().isoformat()
    save_telemetry_config(config)
    
    logger.info("Telemetry enabled")
    print("✓ Telemetry enabled")
    print("  Thank you for helping improve ENA!")
    print("  You can disable this at any time with: animica config set telemetry.opt_in false")


def disable_telemetry() -> None:
    """Disable telemetry collection."""
    config = load_telemetry_config()
    config.opt_in = False
    save_telemetry_config(config)
    
    logger.info("Telemetry disabled")
    print("✓ Telemetry disabled")
    print("  Your existing buffer data has NOT been deleted.")
    print("  To delete it, run: animica data clear")
