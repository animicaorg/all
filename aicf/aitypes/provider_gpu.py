"""
aicf.aitypes.provider_gpu - GPU Provider Extensions (Phase 2)
==============================================================

Extended provider schema for GPU contributor program.
Tracks GPU-specific capabilities, reputation, and attribution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional
from datetime import datetime, timezone

__all__ = [
    "GPUCapabilities",
    "ProviderReputation",
    "ProviderExtended",
]


@dataclass(frozen=True)
class GPUCapabilities:
    """
    GPU-specific capabilities for ENA providers.
    Helps with job matching and pricing hints.
    """
    model_family: str  # e.g., "nvidia-h100", "amd-mi300", "intel-gaudi3"
    max_context: int  # Maximum context length (tokens)
    throughput: int  # Tokens per second (estimated)
    memory_gb: int  # VRAM in GB
    
    # Pricing hints (in nano-ANM per 1000 tokens)
    price_per_1k_input: Optional[int] = None
    price_per_1k_output: Optional[int] = None
    
    # Feature flags
    supports_batching: bool = False
    supports_quantization: bool = False
    supports_flash_attention: bool = False
    
    def __post_init__(self):
        if not self.model_family:
            raise ValueError("model_family cannot be empty")
        if self.max_context <= 0:
            raise ValueError("max_context must be > 0")
        if self.throughput < 0:
            raise ValueError("throughput must be >= 0")
        if self.memory_gb <= 0:
            raise ValueError("memory_gb must be > 0")
        if self.price_per_1k_input is not None and self.price_per_1k_input < 0:
            raise ValueError("price_per_1k_input must be >= 0")
        if self.price_per_1k_output is not None and self.price_per_1k_output < 0:
            raise ValueError("price_per_1k_output must be >= 0")


@dataclass(frozen=False)
class ProviderReputation:
    """
    Provider reputation tracking for slashing and quality scoring.
    
    Mutable to allow incremental updates.
    NOT stored on-chain; kept in provider registry DB.
    """
    provider_id: str
    
    # Job completion stats
    successful_jobs: int = 0
    failed_jobs: int = 0
    total_tokens_processed: int = 0
    
    # Latency tracking (milliseconds)
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    
    # Correctness & quality (future: proof verification)
    correctness_score: float = 1.0  # 0.0 to 1.0
    quality_score: float = 1.0  # 0.0 to 1.0
    
    # Slash/ban tracking
    slash_count: int = 0
    ban_count: int = 0
    is_jailed: bool = False
    jail_until: Optional[datetime] = None
    
    # Last update timestamp
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def success_rate(self) -> float:
        """Calculate job success rate (0.0 to 1.0)"""
        total = self.successful_jobs + self.failed_jobs
        if total == 0:
            return 1.0
        return self.successful_jobs / total
    
    def overall_score(self) -> float:
        """
        Composite reputation score (0.0 to 1.0).
        Combines success rate, correctness, and quality.
        """
        return (
            self.success_rate() * 0.4 +
            self.correctness_score * 0.3 +
            self.quality_score * 0.3
        )
    
    def is_banned(self, now: Optional[datetime] = None) -> bool:
        """Check if provider is currently jailed/banned"""
        if not self.is_jailed:
            return False
        if self.jail_until is None:
            return True  # Permanent jail
        if now is None:
            now = datetime.now(timezone.utc)
        return now < self.jail_until
    
    def record_success(self, latency_ms: float, tokens: int):
        """Record a successful job completion"""
        self.successful_jobs += 1
        self.total_tokens_processed += tokens
        # Update latency with exponential moving average
        alpha = 0.1  # Smoothing factor
        self.avg_latency_ms = (1 - alpha) * self.avg_latency_ms + alpha * latency_ms
        self.last_updated = datetime.now(timezone.utc)
    
    def record_failure(self):
        """Record a failed job"""
        self.failed_jobs += 1
        self.last_updated = datetime.now(timezone.utc)
    
    def apply_slash(self, reason: str):
        """Apply a slash penalty to the provider"""
        self.slash_count += 1
        # Degrade scores based on slash severity
        self.correctness_score = max(0.0, self.correctness_score - 0.1)
        self.quality_score = max(0.0, self.quality_score - 0.05)
        self.last_updated = datetime.now(timezone.utc)
    
    def jail(self, duration_hours: Optional[int] = None):
        """Jail the provider for a duration (None = permanent)"""
        self.is_jailed = True
        self.ban_count += 1
        if duration_hours is not None:
            from datetime import timedelta
            self.jail_until = datetime.now(timezone.utc) + timedelta(hours=duration_hours)
        else:
            self.jail_until = None
        self.last_updated = datetime.now(timezone.utc)
    
    def unjail(self):
        """Remove jail status"""
        self.is_jailed = False
        self.jail_until = None
        self.last_updated = datetime.now(timezone.utc)


@dataclass(frozen=True)
class ProviderExtended:
    """
    Extended provider record with GPU capabilities and reputation.
    Combines base Provider fields with Phase 2 extensions.
    """
    # Base fields (from Provider)
    id: str
    capabilities: int  # Capability flags (AI=1, QUANTUM=2)
    endpoints: Dict[str, str]
    stake: int
    status: str  # ProviderStatus value
    created_at: datetime
    updated_at: datetime
    last_heartbeat: Optional[datetime] = None
    region: Optional[str] = None
    meta: Dict[str, str] = field(default_factory=dict)
    
    # Phase 2 extensions
    gpu_capabilities: Optional[GPUCapabilities] = None
    payout_address: Optional[bytes] = None  # 32-byte address for rewards
    bond_amount: int = 0  # Anti-spam bond (in nano-ANM)
    
    def __post_init__(self):
        """Validate extended fields"""
        if self.payout_address is not None and len(self.payout_address) != 32:
            raise ValueError("payout_address must be 32 bytes")
        if self.bond_amount < 0:
            raise ValueError("bond_amount must be >= 0")
