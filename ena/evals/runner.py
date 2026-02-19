"""
Evaluation runner for executing eval suites.

Provides infrastructure for running evaluations on models and producing results.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .suite import EvalCategory, EvalTask, EvalSuite, EvalResult, TaskResult
from .grader import get_grader

__all__ = ["EvalRunner"]


class EvalRunner:
    """
    Runner for executing evaluation suites.
    
    Takes a suite and a model function, runs all tasks, and produces results.
    """
    
    def __init__(
        self,
        suite: EvalSuite,
        model_function: Callable[[str], str],
        model_hash: Optional[str] = None,
    ):
        """
        Initialize eval runner.
        
        Args:
            suite: Evaluation suite to run
            model_function: Function that takes prompt and returns response
            model_hash: Hash of the model (optional, will be computed if not provided)
        """
        self.suite = suite
        self.model_function = model_function
        self.model_hash = model_hash or self._compute_model_hash()
    
    def _compute_model_hash(self) -> str:
        """Compute a hash for the model (placeholder)."""
        # In a real implementation, this would hash the model weights
        # For now, just return a placeholder
        return hashlib.sha3_256(b"placeholder_model").hexdigest()
    
    def run(self) -> EvalResult:
        """
        Run the entire evaluation suite.
        
        Returns:
            EvalResult with scores and task results
        """
        started_at = datetime.now(timezone.utc).isoformat()
        
        task_results: List[TaskResult] = []
        
        # Run each task
        for task in self.suite.tasks:
            result = self._run_task(task)
            task_results.append(result)
        
        completed_at = datetime.now(timezone.utc).isoformat()
        
        # Calculate overall scores
        total_score, category_scores = self._calculate_scores(task_results)
        num_passed = sum(1 for r in task_results if r.passed)
        pass_rate = num_passed / len(task_results) if task_results else 0.0
        
        return EvalResult(
            suite_name=self.suite.name,
            suite_version=self.suite.version,
            model_hash=self.model_hash,
            total_score=total_score,
            category_scores=category_scores,
            task_results=task_results,
            num_tasks=len(task_results),
            num_passed=num_passed,
            pass_rate=pass_rate,
            started_at=started_at,
            completed_at=completed_at,
        )
    
    def _run_task(self, task: EvalTask) -> TaskResult:
        """
        Run a single evaluation task.
        
        Args:
            task: Task to run
        
        Returns:
            TaskResult with score and details
        """
        try:
            # Get model output
            model_output = self.model_function(task.prompt)
            
            # Get grader and evaluate
            grader = get_grader(task.grader_type, task.grader_config)
            passed, score, message = grader.grade(model_output, task.expected)
            
            # Apply task weight
            weighted_score = score * task.weight
            
            return TaskResult(
                task_id=task.task_id,
                passed=passed,
                score=weighted_score,
                model_output=model_output,
                expected=task.expected,
                message=message,
            )
            
        except Exception as e:
            return TaskResult(
                task_id=task.task_id,
                passed=False,
                score=0.0,
                model_output=None,
                expected=task.expected,
                message=f"Error: {str(e)}",
            )
    
    def _calculate_scores(
        self,
        task_results: List[TaskResult],
    ) -> tuple[float, Dict[str, float]]:
        """
        Calculate overall and category scores.
        
        Args:
            task_results: Results from all tasks
        
        Returns:
            (total_score, category_scores) where:
            - total_score is 0-100
            - category_scores is dict of category -> score (0-100)
        """
        # Group results by category
        category_results: Dict[EvalCategory, List[TaskResult]] = {}
        
        for task in self.suite.tasks:
            if task.category not in category_results:
                category_results[task.category] = []
            
            # Find the corresponding result
            result = next((r for r in task_results if r.task_id == task.task_id), None)
            if result:
                category_results[task.category].append(result)
        
        # Calculate category scores
        category_scores = {}
        for category, results in category_results.items():
            if results:
                avg_score = sum(r.score for r in results) / len(results)
                category_scores[category.value] = avg_score * 100.0
            else:
                category_scores[category.value] = 0.0
        
        # Calculate weighted total score
        if self.suite.category_weights:
            # Use category weights
            total_score = 0.0
            total_weight = 0.0
            
            for category, weight in self.suite.category_weights.items():
                score = category_scores.get(category.value, 0.0)
                total_score += score * weight
                total_weight += weight
            
            if total_weight > 0:
                total_score = total_score / total_weight
            else:
                total_score = 0.0
        else:
            # Equal weight for all categories
            if category_scores:
                total_score = sum(category_scores.values()) / len(category_scores)
            else:
                total_score = 0.0
        
        return total_score, category_scores
