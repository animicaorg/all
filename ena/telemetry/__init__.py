"""
Telemetry system for ENA opt-in data collection.

Collects training examples from ENA usage with aggressive redaction
and user control. Privacy-first design.
"""

from .config import TelemetryConfig, load_telemetry_config, save_telemetry_config
from .collector import TelemetryCollector, RedactionPolicy
from .curator import TelemetryCurator

__all__ = [
    "TelemetryConfig",
    "load_telemetry_config",
    "save_telemetry_config",
    "TelemetryCollector",
    "RedactionPolicy",
    "TelemetryCurator",
]
