"""
Evaluation suite definitions for ENA.

Defines evaluation categories, tasks, and overall suite structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

__all__ = [
    "EvalCategory",
    "EvalTask",
    "EvalSuite",
    "TaskResult",
    "EvalResult",
]


class EvalCategory(str, Enum):
    """Evaluation categories for ENA."""
    CODE_REASONING = "code_reasoning"
    BLOCKCHAIN_PROTOCOL = "blockchain_protocol"
    WALLET_USER_FLOWS = "wallet_user_flows"
    SECURITY = "security"
    MATH_LOGIC = "math_logic"
    TOOL_USE = "tool_use"
    LONG_CONTEXT = "long_context"
    INSTRUCTION_FOLLOWING = "instruction_following"
    SAFETY_POLICY = "safety_policy"


@dataclass
class EvalTask:
    """
    A single evaluation task.
    
    Attributes:
        task_id: Unique identifier for this task
        category: Category this task belongs to
        prompt: Input prompt for the model
        expected: Expected output or reference answer
        grader_type: Type of grader to use (exact_match, regex, code_test, etc.)
        grader_config: Configuration for the grader
        weight: Weight of this task in overall score (default 1.0)
        metadata: Additional metadata about the task
    """
    task_id: str
    category: EvalCategory
    prompt: str
    expected: Any
    grader_type: str = "exact_match"
    grader_config: Dict[str, Any] = field(default_factory=dict)
    weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    """
    Result of evaluating a single task.
    
    Attributes:
        task_id: ID of the task
        passed: Whether the task passed
        score: Score for this task (0.0 to 1.0)
        model_output: What the model produced
        expected: What was expected
        message: Explanation of the result
        metadata: Additional result metadata
    """
    task_id: str
    passed: bool
    score: float
    model_output: Any
    expected: Any
    message: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalResult:
    """
    Overall evaluation result for a suite run.
    
    Attributes:
        suite_name: Name of the suite
        suite_version: Version of the suite
        model_hash: Hash of the model evaluated
        total_score: Overall score (0.0 to 100.0)
        category_scores: Scores by category
        task_results: Results for each task
        num_tasks: Total number of tasks
        num_passed: Number of tasks passed
        pass_rate: Percentage of tasks passed
        started_at: When evaluation started
        completed_at: When evaluation completed
        metadata: Additional result metadata
    """
    suite_name: str
    suite_version: str
    model_hash: str
    total_score: float
    category_scores: Dict[str, float]
    task_results: List[TaskResult]
    num_tasks: int
    num_passed: int
    pass_rate: float
    started_at: str
    completed_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "suite_name": self.suite_name,
            "suite_version": self.suite_version,
            "model_hash": self.model_hash,
            "total_score": self.total_score,
            "category_scores": self.category_scores,
            "task_results": [
                {
                    "task_id": r.task_id,
                    "passed": r.passed,
                    "score": r.score,
                    "model_output": r.model_output,
                    "expected": r.expected,
                    "message": r.message,
                    "metadata": r.metadata,
                }
                for r in self.task_results
            ],
            "num_tasks": self.num_tasks,
            "num_passed": self.num_passed,
            "pass_rate": self.pass_rate,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "metadata": self.metadata,
        }


@dataclass
class EvalSuite:
    """
    An evaluation suite containing multiple tasks.
    
    Attributes:
        name: Suite name (e.g., "ena_v1")
        version: Suite version
        tasks: List of tasks in this suite
        category_weights: Weight for each category in overall score
        description: Description of this suite
    """
    name: str
    version: str
    tasks: List[EvalTask] = field(default_factory=list)
    category_weights: Dict[EvalCategory, float] = field(default_factory=dict)
    description: str = ""
    
    def add_task(self, task: EvalTask):
        """Add a task to this suite."""
        self.tasks.append(task)
    
    def get_tasks_by_category(self, category: EvalCategory) -> List[EvalTask]:
        """Get all tasks in a specific category."""
        return [t for t in self.tasks if t.category == category]
    
    def get_categories(self) -> List[EvalCategory]:
        """Get all categories in this suite."""
        categories = set(t.category for t in self.tasks)
        return sorted(categories, key=lambda c: c.value)
