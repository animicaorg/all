# Compute Marketplace Smart Contract (Python-VM)
"""
ComputeMarketplace: Decentralized compute job marketplace for Animica platform.

This contract manages:
- Job submissions with ANM token escrow
- Provider bidding and selection  
- Proof of compute verification
- Payment settlement

Compatible with Animica Python-VM execution environment.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import time


class JobStatus(Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    """Compute job specification"""
    job_id: str
    requester: str  # Wallet address
    job_type: str  # "llm_inference", "code_execution", "quantum_simulation"
    specs: Dict  # Job-specific parameters
    max_price: int  # Maximum price in ANM (smallest unit)
    timeout: int  # Seconds
    created_at: int
    status: JobStatus
    assigned_provider: Optional[str]
    result_hash: Optional[str]
    payment_released: bool


@dataclass
class Provider:
    """GPU/compute provider registration"""
    address: str
    stake: int  # ANM tokens staked
    capabilities: List[str]  # ["gpu_a100", "tpu_v4", "quantum_qpu"]
    reputation_score: int  # 0-1000
    total_jobs_completed: int
    active: bool
    registered_at: int


# Storage (managed by Python-VM state layer)
_jobs: Dict[str, Job] = {}
_providers: Dict[str, Provider] = {}
_escrow: Dict[str, int] = {}  # job_id -> escrowed amount
_provider_earnings: Dict[str, int] = {}  # provider_address -> total earned


# Contract state
_owner: str = ""
_marketplace_fee_bps: int = 250  # 2.5% (basis points)
_min_stake: int = 1000 * 10**18  # 1000 ANM minimum stake


def init(owner: str):
    """Initialize contract"""
    global _owner
    _owner = owner


def register_provider(
    provider_address: str,
    capabilities: List[str],
    stake_amount: int
) -> Tuple[bool, str]:
    """Register as a compute provider"""
    if stake_amount < _min_stake:
        return False, f"Insufficient stake. Minimum: {_min_stake}"
    
    if provider_address in _providers:
        return False, "Provider already registered"
    
    provider = Provider(
        address=provider_address,
        stake=stake_amount,
        capabilities=capabilities,
        reputation_score=500,
        total_jobs_completed=0,
        active=True,
        registered_at=int(time.time())
    )
    
    _providers[provider_address] = provider
    return True, f"Provider registered with stake {stake_amount}"


def submit_job(
    requester: str,
    job_type: str,
    specs: Dict,
    max_price: int,
    timeout: int,
    payment_amount: int
) -> Tuple[bool, str]:
    """Submit a compute job with payment"""
    if payment_amount < max_price:
        return False, "Payment insufficient for max_price"
    
    job_id = f"{requester}_{int(time.time())}_{len(_jobs)}"
    
    job = Job(
        job_id=job_id,
        requester=requester,
        job_type=job_type,
        specs=specs,
        max_price=max_price,
        timeout=timeout,
        created_at=int(time.time()),
        status=JobStatus.PENDING,
        assigned_provider=None,
        result_hash=None,
        payment_released=False
    )
    
    _jobs[job_id] = job
    _escrow[job_id] = payment_amount
    
    return True, job_id


def get_job(job_id: str) -> Optional[Dict]:
    """Get job details"""
    if job_id not in _jobs:
        return None
    
    job = _jobs[job_id]
    return {
        "job_id": job.job_id,
        "requester": job.requester,
        "job_type": job.job_type,
        "status": job.status.value,
        "assigned_provider": job.assigned_provider,
        "payment_released": job.payment_released,
    }
