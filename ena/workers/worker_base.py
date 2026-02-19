"""
Base class for all ENA workers.

Provides common utilities for DA interaction, artifact hashing,
result reporting, and error handling.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class WorkerError(Exception):
    """Base exception for worker errors."""
    pass


@dataclass
class WorkerResult:
    """Result from a worker job execution."""
    job_id: str
    job_type: str
    status: str  # "success", "failed", "partial"
    
    # Output artifacts (DA commitment hashes)
    artifacts: Dict[str, str]  # name -> hash
    
    # Metrics and metadata
    metrics: Dict[str, Any]
    
    # Execution details
    started_at: str  # ISO8601
    completed_at: str  # ISO8601
    execution_time_seconds: float
    
    # Error info (if failed)
    error_message: Optional[str] = None
    error_traceback: Optional[str] = None
    
    # Checkpoint info (for resume)
    checkpoint_hash: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)
    
    def to_json(self) -> str:
        """Convert to JSON."""
        return json.dumps(self.to_dict(), indent=2)


class WorkerBase(ABC):
    """
    Base class for all ENA workers.
    
    Provides common functionality:
    - DA download/upload stubs
    - Artifact hashing
    - Result reporting
    - Error handling
    - Checkpoint support
    """
    
    def __init__(
        self,
        job_spec: dict,
        output_dir: Optional[Path] = None,
        mock_mode: bool = False,
    ):
        """
        Initialize worker.
        
        Args:
            job_spec: Job specification from AICF
            output_dir: Directory for outputs (default: temp dir)
            mock_mode: If True, simulate execution without real compute
        """
        self.job_spec = job_spec
        self.job_id = job_spec.get("job_id", "unknown")
        self.job_type = job_spec.get("job_type", "unknown")
        self.mock_mode = mock_mode
        
        # Setup output directory
        if output_dir is None:
            self.output_dir = Path(tempfile.mkdtemp(prefix=f"ena_worker_{self.job_id}_"))
        else:
            self.output_dir = Path(output_dir)
            self.output_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Worker initialized: job_id={self.job_id}, type={self.job_type}, mock={self.mock_mode}")
        logger.info(f"Output directory: {self.output_dir}")
    
    @abstractmethod
    def execute(self) -> WorkerResult:
        """
        Execute the job and return results.
        
        Subclasses must implement this method.
        """
        pass
    
    # ---- DA Interaction (Stubs) ----
    
    def download_from_da(self, commitment_hash: str, output_path: Path) -> None:
        """
        Download artifact from DA by commitment hash.
        
        Args:
            commitment_hash: DA commitment hash
            output_path: Local path to save artifact
        """
        logger.info(f"DA download: {commitment_hash} -> {output_path}")
        
        if self.mock_mode:
            # Create dummy file
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(f"mock_data_for_{commitment_hash}")
            logger.info(f"MOCK: Created dummy file at {output_path}")
            return
        
        # Phase 2: DA layer integration (DA client wiring pending)
        # For now, stub implementation
        raise NotImplementedError("DA download not yet implemented. Use mock_mode=True for testing.")
    
    def upload_to_da(self, artifact_path: Path) -> str:
        """
        Upload artifact to DA and return commitment hash.
        
        Args:
            artifact_path: Local path to artifact (file or directory)
            
        Returns:
            DA commitment hash
        """
        logger.info(f"DA upload: {artifact_path}")
        
        # Compute artifact hash (handle both files and directories)
        if artifact_path.is_dir():
            artifact_hash = self.hash_directory(artifact_path)
        else:
            artifact_hash = self.hash_file(artifact_path)
        
        if self.mock_mode:
            # Return mock commitment hash
            commitment = f"da://mock/{artifact_hash[:16]}"
            logger.info(f"MOCK: Upload complete, commitment={commitment}")
            return commitment
        
        # Phase 2: DA layer integration (DA client wiring pending)
        # For now, stub implementation
        raise NotImplementedError("DA upload not yet implemented. Use mock_mode=True for testing.")
    
    # ---- Artifact Hashing ----
    
    def hash_file(self, path: Path) -> str:
        """
        Compute SHA256 hash of a file.
        
        Args:
            path: Path to file
            
        Returns:
            Hex-encoded SHA256 hash
        """
        hasher = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                hasher.update(chunk)
        return hasher.hexdigest()
    
    def hash_directory(self, path: Path) -> str:
        """
        Compute deterministic hash of directory contents.
        
        Args:
            path: Path to directory
            
        Returns:
            Hex-encoded SHA256 hash
        """
        hasher = hashlib.sha256()
        
        # Sort files for determinism
        files = sorted(path.rglob('*'))
        files = [f for f in files if f.is_file()]
        
        for file_path in files:
            # Hash relative path
            rel_path = file_path.relative_to(path)
            hasher.update(str(rel_path).encode('utf-8'))
            
            # Hash file content
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    hasher.update(chunk)
        
        return hasher.hexdigest()
    
    # ---- Result Reporting ----
    
    def create_result(
        self,
        status: str,
        artifacts: Dict[str, str],
        metrics: Dict[str, Any],
        started_at: str,
        completed_at: str,
        error_message: Optional[str] = None,
        error_traceback: Optional[str] = None,
        checkpoint_hash: Optional[str] = None,
    ) -> WorkerResult:
        """
        Create a WorkerResult object.
        
        Args:
            status: "success", "failed", or "partial"
            artifacts: Dict of artifact_name -> DA commitment hash
            metrics: Metrics from job execution
            started_at: ISO8601 start time
            completed_at: ISO8601 completion time
            error_message: Error message if failed
            error_traceback: Error traceback if failed
            checkpoint_hash: Checkpoint hash for resume
            
        Returns:
            WorkerResult object
        """
        started = datetime.fromisoformat(started_at)
        completed = datetime.fromisoformat(completed_at)
        execution_time = (completed - started).total_seconds()
        
        return WorkerResult(
            job_id=self.job_id,
            job_type=self.job_type,
            status=status,
            artifacts=artifacts,
            metrics=metrics,
            started_at=started_at,
            completed_at=completed_at,
            execution_time_seconds=execution_time,
            error_message=error_message,
            error_traceback=error_traceback,
            checkpoint_hash=checkpoint_hash,
        )
    
    # ---- Checkpoint Support ----
    
    def save_checkpoint(self, checkpoint_data: dict, checkpoint_name: str = "checkpoint.json") -> str:
        """
        Save checkpoint data for resume.
        
        Args:
            checkpoint_data: Checkpoint state
            checkpoint_name: Checkpoint file name
            
        Returns:
            Hash of checkpoint
        """
        checkpoint_path = self.output_dir / checkpoint_name
        
        with open(checkpoint_path, 'w') as f:
            json.dump(checkpoint_data, f, indent=2)
        
        checkpoint_hash = self.hash_file(checkpoint_path)
        logger.info(f"Checkpoint saved: {checkpoint_path}, hash={checkpoint_hash[:16]}")
        
        return checkpoint_hash
    
    def load_checkpoint(self, checkpoint_path: Path) -> dict:
        """
        Load checkpoint data for resume.
        
        Args:
            checkpoint_path: Path to checkpoint file
            
        Returns:
            Checkpoint data
        """
        with open(checkpoint_path, 'r') as f:
            checkpoint_data = json.load(f)
        
        logger.info(f"Checkpoint loaded: {checkpoint_path}")
        return checkpoint_data
    
    # ---- Metrics Helpers ----
    
    def save_metrics(self, metrics: dict, metrics_name: str = "metrics.json") -> Path:
        """
        Save metrics to JSON file.
        
        Args:
            metrics: Metrics dict
            metrics_name: Metrics file name
            
        Returns:
            Path to metrics file
        """
        metrics_path = self.output_dir / metrics_name
        
        with open(metrics_path, 'w') as f:
            json.dump(metrics, f, indent=2)
        
        logger.info(f"Metrics saved: {metrics_path}")
        return metrics_path
    
    def load_metrics(self, metrics_path: Path) -> dict:
        """
        Load metrics from JSON file.
        
        Args:
            metrics_path: Path to metrics file
            
        Returns:
            Metrics dict
        """
        with open(metrics_path, 'r') as f:
            metrics = json.load(f)
        
        logger.info(f"Metrics loaded: {metrics_path}")
        return metrics
