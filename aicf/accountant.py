"""
AICF Job Accountant - Track job costs and enforce billing limits.

Integrates with the billing module to:
- Track resource units consumed per job and submitter
- Apply free tier vs paid mode logic
- Enforce quotas and raise clear errors for over-quota scenarios
- Provide in-memory and file-backed storage options
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Literal, Optional, Tuple


@dataclass
class JobAccountingRecord:
    """
    Accounting record for a job.
    
    Attributes:
        job_id: Unique job identifier
        submitter: Address or ID of job submitter
        plan: Billing plan (e.g., "free", "pro")
        resource_units: Resource units consumed
        status: Job status (queued, running, completed, failed)
        charge: Computed charge for this job
        timestamp: Unix timestamp of record creation
    """
    
    job_id: str
    submitter: str
    plan: str
    resource_units: int
    status: Literal["queued", "running", "completed", "failed"]
    charge: float
    timestamp: float


class JobAccountant:
    """
    AICF Job Accountant - tracks job costs and enforces billing limits.
    
    Features:
    - Tracks jobs by job_id, submitter, plan, resource units, and charge
    - Computes charges based on free tier and paid rates
    - Enforces quotas in free vs paid mode
    - Thread-safe in-memory storage
    - Optional file-backed persistence
    
    Free Mode:
    - All jobs accepted up to free tier units
    - Charges always 0
    - Over-quota jobs rejected with clear error
    
    Paid Mode:
    - Jobs charged after free tier exhausted
    - No hard quota (pay-as-you-go)
    - Charges computed based on rate_per_unit
    """
    
    def __init__(
        self,
        *,
        rate_per_unit: float = 0.0,
        free_units: int = 1000,
        mode: Literal["free", "paid"] = "free",
    ):
        """
        Initialize job accountant.
        
        Args:
            rate_per_unit: Cost per resource unit (default 0)
            free_units: Free tier units per period (default 1000)
            mode: Billing mode - "free" or "paid" (default "free")
        """
        self._lock = threading.RLock()
        self._records: Dict[str, JobAccountingRecord] = {}
        self._submitter_usage: Dict[str, int] = {}  # submitter -> total units used
        
        self.rate_per_unit = rate_per_unit
        self.free_units = free_units
        self.mode = mode
    
    def record_job(
        self,
        job_id: str,
        submitter: str,
        plan: str,
        resource_units: int,
        status: Literal["queued", "running", "completed", "failed"] = "queued",
    ) -> JobAccountingRecord:
        """
        Record a job and compute its charge.
        
        Args:
            job_id: Unique job identifier
            submitter: Address or ID of job submitter
            plan: Billing plan
            resource_units: Resource units for this job
            status: Job status (default "queued")
            
        Returns:
            JobAccountingRecord with computed charge
            
        Raises:
            ValueError: If job would exceed quota in free mode
        """
        with self._lock:
            # Check if job already recorded
            if job_id in self._records:
                return self._records[job_id]
            
            # Get current usage for submitter
            units_used = self._submitter_usage.get(submitter, 0)
            
            # Compute charge considering free tier
            remaining_free = max(0, self.free_units - units_used)
            billable_units = max(0, resource_units - remaining_free)
            charge = billable_units * self.rate_per_unit
            
            # Enforce quota in free mode
            if self.mode == "free" and (units_used + resource_units) > self.free_units:
                raise ValueError(
                    f"Job would exceed free tier quota. "
                    f"Used: {units_used}/{self.free_units} units, "
                    f"Requested: {resource_units} units. "
                    f"Upgrade to paid mode or wait for quota reset."
                )
            
            # Create record
            record = JobAccountingRecord(
                job_id=job_id,
                submitter=submitter,
                plan=plan,
                resource_units=resource_units,
                status=status,
                charge=charge,
                timestamp=time.time(),
            )
            
            # Store record and update usage
            self._records[job_id] = record
            self._submitter_usage[submitter] = units_used + resource_units
            
            return record
    
    def update_job_status(
        self,
        job_id: str,
        status: Literal["queued", "running", "completed", "failed"],
    ) -> None:
        """
        Update job status.
        
        Args:
            job_id: Unique job identifier
            status: New job status
        """
        with self._lock:
            if job_id not in self._records:
                raise KeyError(f"Job {job_id} not found in accountant")
            
            # Create updated record (dataclass is frozen conceptually, so replace)
            old_record = self._records[job_id]
            self._records[job_id] = JobAccountingRecord(
                job_id=old_record.job_id,
                submitter=old_record.submitter,
                plan=old_record.plan,
                resource_units=old_record.resource_units,
                status=status,
                charge=old_record.charge,
                timestamp=old_record.timestamp,
            )
    
    def get_record(self, job_id: str) -> Optional[JobAccountingRecord]:
        """
        Get accounting record for a job.
        
        Args:
            job_id: Unique job identifier
            
        Returns:
            JobAccountingRecord or None if not found
        """
        with self._lock:
            return self._records.get(job_id)
    
    def get_submitter_usage(self, submitter: str) -> Tuple[int, float]:
        """
        Get total usage for a submitter.
        
        Args:
            submitter: Address or ID of submitter
            
        Returns:
            Tuple of (total_units, total_charge)
        """
        with self._lock:
            units = self._submitter_usage.get(submitter, 0)
            
            # Calculate total charge for this submitter
            charge = 0.0
            for record in self._records.values():
                if record.submitter == submitter:
                    charge += record.charge
            
            return (units, charge)
    
    def get_all_records(self) -> Dict[str, JobAccountingRecord]:
        """
        Get all accounting records.
        
        Returns:
            Dictionary mapping job_id to JobAccountingRecord
        """
        with self._lock:
            return dict(self._records)
    
    def reset_submitter_usage(self, submitter: str) -> None:
        """
        Reset usage tracking for a submitter (for quota period reset).
        
        Args:
            submitter: Address or ID of submitter
        """
        with self._lock:
            self._submitter_usage[submitter] = 0
    
    def clear(self) -> None:
        """Clear all records and usage tracking."""
        with self._lock:
            self._records.clear()
            self._submitter_usage.clear()


class FileBackedJobAccountant(JobAccountant):
    """
    File-backed job accountant with periodic persistence.
    
    Extends JobAccountant with automatic saving to JSON file.
    """
    
    def __init__(
        self,
        file_path: str | Path,
        *,
        rate_per_unit: float = 0.0,
        free_units: int = 1000,
        mode: Literal["free", "paid"] = "free",
        auto_save_interval: float = 60.0,
    ):
        """
        Initialize file-backed job accountant.
        
        Args:
            file_path: Path to JSON file for persistence
            rate_per_unit: Cost per resource unit (default 0)
            free_units: Free tier units per period (default 1000)
            mode: Billing mode - "free" or "paid" (default "free")
            auto_save_interval: Auto-save interval in seconds (default 60)
        """
        super().__init__(
            rate_per_unit=rate_per_unit,
            free_units=free_units,
            mode=mode,
        )
        
        self.file_path = Path(file_path)
        self.auto_save_interval = auto_save_interval
        self._last_save_time = 0.0
        
        # Load existing data
        self._load()
    
    def _load(self) -> None:
        """Load records from file."""
        if not self.file_path.exists():
            return
        
        try:
            with open(self.file_path, "r") as f:
                data = json.load(f)
            
            with self._lock:
                # Load records
                for job_id, record_data in data.get("records", {}).items():
                    self._records[job_id] = JobAccountingRecord(**record_data)
                
                # Load submitter usage
                self._submitter_usage = data.get("submitter_usage", {})
        except Exception as e:
            print(f"Warning: Could not load accountant data from {self.file_path}: {e}")
    
    def save(self) -> None:
        """Save records to file."""
        with self._lock:
            data = {
                "records": {
                    job_id: asdict(record)
                    for job_id, record in self._records.items()
                },
                "submitter_usage": self._submitter_usage,
                "config": {
                    "rate_per_unit": self.rate_per_unit,
                    "free_units": self.free_units,
                    "mode": self.mode,
                },
            }
        
        # Ensure parent directory exists
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write atomically via temp file
        temp_path = self.file_path.with_suffix(".tmp")
        try:
            with open(temp_path, "w") as f:
                json.dump(data, f, indent=2)
            temp_path.replace(self.file_path)
            self._last_save_time = time.time()
        except Exception as e:
            print(f"Warning: Could not save accountant data to {self.file_path}: {e}")
            if temp_path.exists():
                temp_path.unlink()
    
    def _auto_save(self) -> None:
        """Auto-save if interval has elapsed."""
        now = time.time()
        if now - self._last_save_time >= self.auto_save_interval:
            self.save()
    
    def record_job(
        self,
        job_id: str,
        submitter: str,
        plan: str,
        resource_units: int,
        status: Literal["queued", "running", "completed", "failed"] = "queued",
    ) -> JobAccountingRecord:
        """Record job and auto-save."""
        record = super().record_job(job_id, submitter, plan, resource_units, status)
        self._auto_save()
        return record
    
    def update_job_status(
        self,
        job_id: str,
        status: Literal["queued", "running", "completed", "failed"],
    ) -> None:
        """Update job status and auto-save."""
        super().update_job_status(job_id, status)
        self._auto_save()


__all__ = [
    "JobAccountingRecord",
    "JobAccountant",
    "FileBackedJobAccountant",
]
