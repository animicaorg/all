"""
Animica DA Provider module.

Provides data structures and utilities for storage providers who contribute
disk space to the network and earn AICF credits.
"""

from __future__ import annotations

from .registry import (
    AuditChallenge,
    AuditResponse,
    AuditResult,
    BlobAssignment,
    ProviderEntry,
    ProviderRegistry,
    create_provider_entry,
    create_provider_id,
    register_provider,
)

try:
    from .service import ProviderService, SimpleRateLimiter

    __all__ = [
        "ProviderEntry",
        "ProviderRegistry",
        "BlobAssignment",
        "AuditChallenge",
        "AuditResponse",
        "AuditResult",
        "create_provider_entry",
        "create_provider_id",
        "register_provider",
        "ProviderService",
        "SimpleRateLimiter",
    ]
except ImportError:
    # FastAPI not available
    __all__ = [
        "ProviderEntry",
        "ProviderRegistry",
        "BlobAssignment",
        "AuditChallenge",
        "AuditResponse",
        "AuditResult",
        "create_provider_entry",
        "create_provider_id",
        "register_provider",
    ]
