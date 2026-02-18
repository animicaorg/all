"""
Upgrade state machine for tracking and resuming upgrade workflows.

Provides idempotent state transitions and persistence for resilience.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class UpgradeState(str, Enum):
    """States in the upgrade workflow."""
    IDLE = "idle"
    PLANNING = "planning"
    ALLOCATING_BUDGET = "allocating_budget"
    SUBMITTING_JOBS = "submitting_jobs"
    MONITORING = "monitoring"
    VERIFYING = "verifying"
    PUBLISHING = "publishing"
    CANARY = "canary"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class JobStatus:
    """Status of a single job in the workflow."""
    job_id: str
    state: str  # "pending", "running", "completed", "failed"
    aicf_job_id: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    result_hash: Optional[str] = None
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> JobStatus:
        return cls(**data)


@dataclass
class UpgradeStatus:
    """
    Complete status of an upgrade workflow.
    
    Persisted to JSON for resumability.
    """
    # Identifiers
    upgrade_id: str
    model_id: str
    target_version: str
    
    # State tracking
    current_state: UpgradeState
    plan_id: Optional[str] = None
    plan_hash: Optional[str] = None
    
    # Job tracking
    job_statuses: Dict[str, JobStatus] = field(default_factory=dict)
    
    # Budget tracking
    budget_allocated: int = 0  # ANM base units
    budget_used: int = 0  # ANM base units
    
    # Error tracking
    errors: List[str] = field(default_factory=list)
    
    # Metadata
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    
    # Published artifacts
    published_manifest_hash: Optional[str] = None
    
    # Canary tracking
    canary_started_at: Optional[str] = None
    canary_promotion_at: Optional[str] = None
    
    # Previous version for rollback
    previous_version: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        # Convert UpgradeState enum to string
        data["current_state"] = self.current_state.value
        return data
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)
    
    @classmethod
    def from_dict(cls, data: dict) -> UpgradeStatus:
        """Create from dictionary."""
        # Convert state string to enum
        data["current_state"] = UpgradeState(data["current_state"])
        
        # Convert job statuses
        if "job_statuses" in data:
            data["job_statuses"] = {
                k: JobStatus.from_dict(v) if isinstance(v, dict) else v
                for k, v in data["job_statuses"].items()
            }
        
        return cls(**data)
    
    @classmethod
    def from_json(cls, json_str: str) -> UpgradeStatus:
        """Create from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)


class UpgradeStateMachine:
    """
    State machine for managing upgrade workflows.
    
    Features:
    - Idempotent state transitions
    - Persistence to JSON
    - Resume from any state
    - Automatic state file updates
    """
    
    # Valid state transitions
    VALID_TRANSITIONS = {
        UpgradeState.IDLE: {UpgradeState.PLANNING},
        UpgradeState.PLANNING: {UpgradeState.ALLOCATING_BUDGET, UpgradeState.FAILED},
        UpgradeState.ALLOCATING_BUDGET: {UpgradeState.SUBMITTING_JOBS, UpgradeState.FAILED},
        UpgradeState.SUBMITTING_JOBS: {UpgradeState.MONITORING, UpgradeState.FAILED},
        UpgradeState.MONITORING: {UpgradeState.VERIFYING, UpgradeState.FAILED},
        UpgradeState.VERIFYING: {UpgradeState.PUBLISHING, UpgradeState.FAILED},
        UpgradeState.PUBLISHING: {UpgradeState.CANARY, UpgradeState.FAILED},
        UpgradeState.CANARY: {UpgradeState.COMPLETED, UpgradeState.FAILED, UpgradeState.ROLLED_BACK},
        UpgradeState.COMPLETED: set(),
        UpgradeState.FAILED: {UpgradeState.ROLLED_BACK, UpgradeState.IDLE},
        UpgradeState.ROLLED_BACK: {UpgradeState.IDLE},
    }
    
    def __init__(self, state_file: Path):
        """
        Initialize state machine.
        
        Args:
            state_file: Path to state persistence file
        """
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Load or create initial state
        if self.state_file.exists():
            self.status = self._load_state()
        else:
            self.status = None
    
    def _load_state(self) -> UpgradeStatus:
        """Load state from file."""
        try:
            json_str = self.state_file.read_text()
            status = UpgradeStatus.from_json(json_str)
            logger.info(f"Loaded state: {status.upgrade_id} in {status.current_state.value}")
            return status
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            raise
    
    def _save_state(self):
        """Save state to file."""
        if not self.status:
            return
        
        try:
            # Update timestamp
            self.status.updated_at = datetime.utcnow().isoformat() + "Z"
            
            # Write to temp file first (atomic write)
            temp_file = self.state_file.with_suffix(".tmp")
            temp_file.write_text(self.status.to_json())
            temp_file.replace(self.state_file)
            
            logger.debug(f"Saved state: {self.status.current_state.value}")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
            raise
    
    def create_upgrade(
        self,
        upgrade_id: str,
        model_id: str,
        target_version: str,
        previous_version: Optional[str] = None,
    ) -> UpgradeStatus:
        """
        Create a new upgrade workflow.
        
        Args:
            upgrade_id: Unique upgrade identifier
            model_id: Model being upgraded
            target_version: Target version
            previous_version: Previous version (for rollback)
        
        Returns:
            New UpgradeStatus
        """
        if self.status and self.status.current_state not in {
            UpgradeState.IDLE,
            UpgradeState.COMPLETED,
            UpgradeState.FAILED,
            UpgradeState.ROLLED_BACK,
        }:
            raise ValueError(f"Cannot create new upgrade: current state is {self.status.current_state.value}")
        
        self.status = UpgradeStatus(
            upgrade_id=upgrade_id,
            model_id=model_id,
            target_version=target_version,
            current_state=UpgradeState.IDLE,
            previous_version=previous_version,
        )
        
        self._save_state()
        logger.info(f"Created upgrade: {upgrade_id} for {model_id} v{target_version}")
        return self.status
    
    def transition(self, new_state: UpgradeState, error: Optional[str] = None) -> bool:
        """
        Transition to a new state.
        
        Args:
            new_state: Target state
            error: Optional error message (for FAILED transitions)
        
        Returns:
            True if transition was valid and executed
        """
        if not self.status:
            raise ValueError("No upgrade in progress")
        
        current = self.status.current_state
        
        # Check if transition is valid
        if new_state not in self.VALID_TRANSITIONS[current]:
            logger.warning(f"Invalid transition: {current.value} -> {new_state.value}")
            return False
        
        # Perform transition
        self.status.current_state = new_state
        
        if error:
            self.status.errors.append(f"[{datetime.utcnow().isoformat()}] {error}")
        
        self._save_state()
        logger.info(f"Transitioned: {current.value} -> {new_state.value}")
        return True
    
    def update_job_status(self, job_id: str, status: JobStatus):
        """Update status for a specific job."""
        if not self.status:
            raise ValueError("No upgrade in progress")
        
        self.status.job_statuses[job_id] = status
        self._save_state()
    
    def set_plan(self, plan_id: str, plan_hash: str):
        """Set the training plan."""
        if not self.status:
            raise ValueError("No upgrade in progress")
        
        self.status.plan_id = plan_id
        self.status.plan_hash = plan_hash
        self._save_state()
    
    def allocate_budget(self, amount: int):
        """Record budget allocation."""
        if not self.status:
            raise ValueError("No upgrade in progress")
        
        self.status.budget_allocated = amount
        self._save_state()
    
    def record_spending(self, amount: int):
        """Record budget spending."""
        if not self.status:
            raise ValueError("No upgrade in progress")
        
        self.status.budget_used += amount
        self._save_state()
    
    def set_published_manifest(self, manifest_hash: str):
        """Record published manifest hash."""
        if not self.status:
            raise ValueError("No upgrade in progress")
        
        self.status.published_manifest_hash = manifest_hash
        self._save_state()
    
    def start_canary(self):
        """Record canary deployment start."""
        if not self.status:
            raise ValueError("No upgrade in progress")
        
        self.status.canary_started_at = datetime.utcnow().isoformat() + "Z"
        self._save_state()
    
    def promote_canary(self):
        """Record canary promotion."""
        if not self.status:
            raise ValueError("No upgrade in progress")
        
        self.status.canary_promotion_at = datetime.utcnow().isoformat() + "Z"
        self._save_state()
    
    def get_status(self) -> Optional[UpgradeStatus]:
        """Get current status."""
        return self.status
    
    def can_resume(self) -> bool:
        """Check if workflow can be resumed."""
        if not self.status:
            return False
        
        # Can resume from any state except terminal states
        return self.status.current_state not in {
            UpgradeState.COMPLETED,
            UpgradeState.ROLLED_BACK,
        }
    
    def get_failed_jobs(self) -> List[JobStatus]:
        """Get list of failed jobs."""
        if not self.status:
            return []
        
        return [
            job for job in self.status.job_statuses.values()
            if job.state == "failed"
        ]
    
    def get_completed_jobs(self) -> List[JobStatus]:
        """Get list of completed jobs."""
        if not self.status:
            return []
        
        return [
            job for job in self.status.job_statuses.values()
            if job.state == "completed"
        ]
    
    def get_pending_jobs(self) -> List[JobStatus]:
        """Get list of pending jobs."""
        if not self.status:
            return []
        
        return [
            job for job in self.status.job_statuses.values()
            if job.state == "pending"
        ]
