"""
Training plan specification.

Defines deterministic training plans that can be submitted to AICF.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any


class JobType(str, Enum):
    """Types of jobs in the training pipeline."""
    TRAIN_SFT = "ena.train.sft"  # Supervised fine-tuning
    EVAL = "ena.eval"  # Evaluation run
    DISTILL_CPU = "ena.distill.cpu"  # Distillation for CPU runtime


@dataclass
class JobSpec:
    """Specification for a single job in the training pipeline."""
    job_type: JobType
    job_id: str  # Unique identifier
    
    # Input references
    base_model: Optional[str] = None  # DA commitment or model ID
    dataset_hashes: List[str] = field(default_factory=list)  # DA commitment hashes
    eval_suite_hash: Optional[str] = None  # DA commitment hash
    
    # Hyperparameters (bounded for determinism)
    hyperparams: Dict[str, Any] = field(default_factory=dict)
    
    # Resource requirements
    max_gpu_hours: float = 10.0
    max_cost_anm: int = 1_000_000_000  # 1 ANM default
    
    # Dependencies (job_ids that must complete first)
    depends_on: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)
    
    def compute_hash(self) -> str:
        """Compute deterministic hash of job spec."""
        d = self.to_dict()
        canonical = json.dumps(d, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


@dataclass
class TrainingPlan:
    """
    Complete training plan with all jobs and dependencies.
    
    This is the deterministic specification for an entire upgrade cycle.
    """
    plan_id: str  # Unique plan identifier
    model_id: str  # Target model (e.g., "ena")
    target_version: str  # Target version to produce
    
    # Jobs in execution order
    jobs: List[JobSpec]
    
    # Budget constraints
    max_total_cost_anm: int  # Maximum total AICF spend
    
    # Dataset references
    dataset_commitments: List[str]  # DA commitment hashes
    
    # Metadata
    created_at: str  # ISO8601 timestamp
    creator: str  # Address or identifier
    description: str
    
    # Execution tracking (updated during execution)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)
    
    def to_json(self) -> str:
        """Convert to JSON."""
        return json.dumps(self.to_dict(), indent=2)
    
    def compute_hash(self) -> str:
        """Compute deterministic hash of plan (excluding execution tracking)."""
        d = self.to_dict()
        # Exclude fields set during execution
        d.pop("started_at", None)
        d.pop("completed_at", None)
        
        canonical = json.dumps(d, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    
    @classmethod
    def from_dict(cls, data: dict) -> TrainingPlan:
        """Create from dictionary."""
        # Convert job specs
        data["jobs"] = [JobSpec(**j) for j in data["jobs"]]
        return cls(**data)
    
    @classmethod
    def from_json(cls, json_str: str) -> TrainingPlan:
        """Create from JSON."""
        data = json.loads(json_str)
        return cls.from_dict(data)
    
    def get_job_by_id(self, job_id: str) -> Optional[JobSpec]:
        """Get a job by its ID."""
        for job in self.jobs:
            if job.job_id == job_id:
                return job
        return None
    
    def validate_dependencies(self) -> bool:
        """Validate that all job dependencies exist."""
        job_ids = {job.job_id for job in self.jobs}
        
        for job in self.jobs:
            for dep_id in job.depends_on:
                if dep_id not in job_ids:
                    return False
        
        return True
    
    def get_execution_order(self) -> List[str]:
        """
        Get job IDs in valid execution order (topological sort).
        
        Returns:
            List of job IDs in execution order
        
        Raises:
            ValueError: If dependencies form a cycle
        """
        # Build dependency graph
        graph = {job.job_id: set(job.depends_on) for job in self.jobs}
        
        # Topological sort (Kahn's algorithm)
        result = []
        no_deps = [jid for jid, deps in graph.items() if not deps]
        
        while no_deps:
            current = no_deps.pop(0)
            result.append(current)
            
            # Remove this node from all dependency lists
            for jid in graph:
                graph[jid].discard(current)
            
            # Find new nodes with no dependencies
            for jid, deps in graph.items():
                if not deps and jid not in result and jid not in no_deps:
                    no_deps.append(jid)
        
        if len(result) != len(self.jobs):
            raise ValueError("Circular dependency detected in job graph")
        
        return result
    
    def estimate_cost(self) -> int:
        """Estimate total cost in ANM base units."""
        return sum(job.max_cost_anm for job in self.jobs)


def create_default_training_plan(
    model_id: str,
    target_version: str,
    creator: str,
    dataset_hashes: List[str],
    base_model: str = "qwen2.5-coder-1.5b",
) -> TrainingPlan:
    """
    Create a default training plan for ENA upgrade.
    
    This creates a teacher-student pipeline:
    1. Train teacher model (SFT)
    2. Evaluate teacher
    3. Distill to student (CPU-optimized)
    4. Evaluate student
    
    Args:
        model_id: Model identifier
        target_version: Target version string
        creator: Creator address/identifier
        dataset_hashes: List of dataset DA commitment hashes
        base_model: Base model to fine-tune from
    
    Returns:
        TrainingPlan with default jobs
    """
    now = datetime.utcnow().isoformat() + "Z"
    plan_id = f"{model_id}_upgrade_{target_version}_{int(datetime.utcnow().timestamp())}"
    
    # Job 1: Train teacher model
    train_job = JobSpec(
        job_type=JobType.TRAIN_SFT,
        job_id=f"{plan_id}_train_teacher",
        base_model=base_model,
        dataset_hashes=dataset_hashes,
        hyperparams={
            "learning_rate": 1e-5,
            "batch_size": 4,
            "epochs": 3,
            "max_seq_length": 2048,
            "gradient_accumulation_steps": 4,
        },
        max_gpu_hours=20.0,
        max_cost_anm=5_000_000_000,  # 5 ANM
    )
    
    # Job 2: Evaluate teacher
    eval_teacher_job = JobSpec(
        job_type=JobType.EVAL,
        job_id=f"{plan_id}_eval_teacher",
        base_model=f"output:{train_job.job_id}",  # Reference to training output
        eval_suite_hash="",  # Will be filled in by coordinator
        hyperparams={
            "tasks": ["accuracy", "perplexity", "toxicity", "regression"],
        },
        max_gpu_hours=2.0,
        max_cost_anm=500_000_000,  # 0.5 ANM
        depends_on=[train_job.job_id],
    )
    
    # Job 3: Distill to CPU model
    distill_job = JobSpec(
        job_type=JobType.DISTILL_CPU,
        job_id=f"{plan_id}_distill_cpu",
        base_model=f"output:{train_job.job_id}",
        hyperparams={
            "target_model_size": "1.5B",
            "quantization": "gguf_q4_0",
            "temperature": 2.0,
            "alpha": 0.5,
        },
        max_gpu_hours=10.0,
        max_cost_anm=2_000_000_000,  # 2 ANM
        depends_on=[eval_teacher_job.job_id],
    )
    
    # Job 4: Evaluate student
    eval_student_job = JobSpec(
        job_type=JobType.EVAL,
        job_id=f"{plan_id}_eval_student",
        base_model=f"output:{distill_job.job_id}",
        eval_suite_hash="",
        hyperparams={
            "tasks": ["accuracy", "perplexity", "toxicity", "regression"],
            "runtime": "llama.cpp",  # CPU runtime
        },
        max_gpu_hours=1.0,
        max_cost_anm=200_000_000,  # 0.2 ANM
        depends_on=[distill_job.job_id],
    )
    
    return TrainingPlan(
        plan_id=plan_id,
        model_id=model_id,
        target_version=target_version,
        jobs=[train_job, eval_teacher_job, distill_job, eval_student_job],
        max_total_cost_anm=10_000_000_000,  # 10 ANM max
        dataset_commitments=dataset_hashes,
        created_at=now,
        creator=creator,
        description=f"Teacher-student training pipeline for {model_id} v{target_version}",
    )
