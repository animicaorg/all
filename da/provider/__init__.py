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

# Assignment and audit modules
from .assignment import (
    assign_blob,
    get_blob_providers,
    AssignmentError,
)

from .audit import (
    AuditDatabase,
    create_challenge,
    verify_response,
    update_provider_score,
)

from .audit_scheduler import (
    AuditScheduler,
    AuditSchedulerConfig,
    jail_provider,
    unjail_provider,
    get_jailed_providers,
)

try:
    from .service import ProviderService, SimpleRateLimiter

    __all__ = [
        # Registry
        "ProviderEntry",
        "ProviderRegistry",
        "BlobAssignment",
        "AuditChallenge",
        "AuditResponse",
        "AuditResult",
        "create_provider_entry",
        "create_provider_id",
        "register_provider",
        # Assignment
        "assign_blob",
        "get_blob_providers",
        "AssignmentError",
        # Audit
        "AuditDatabase",
        "create_challenge",
        "verify_response",
        "update_provider_score",
        # Audit Scheduler
        "AuditScheduler",
        "AuditSchedulerConfig",
        "jail_provider",
        "unjail_provider",
        "get_jailed_providers",
        # Service
        "ProviderService",
        "SimpleRateLimiter",
    ]
except ImportError:
    # FastAPI not available
    __all__ = [
        # Registry
        "ProviderEntry",
        "ProviderRegistry",
        "BlobAssignment",
        "AuditChallenge",
        "AuditResponse",
        "AuditResult",
        "create_provider_entry",
        "create_provider_id",
        "register_provider",
        # Assignment
        "assign_blob",
        "get_blob_providers",
        "AssignmentError",
        # Audit
        "AuditDatabase",
        "create_challenge",
        "verify_response",
        "update_provider_score",
        # Audit Scheduler
        "AuditScheduler",
        "AuditSchedulerConfig",
        "jail_provider",
        "unjail_provider",
        "get_jailed_providers",
    ]
