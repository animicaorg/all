"""
ENA Evaluation System

This module provides the evaluation framework for ENA models with:
- Structured eval suites with multiple categories
- CPU-friendly deterministic graders
- Evaluation runners and reporters
- Integration with AICF job submission
"""

from .suite import (
    EvalCategory,
    EvalTask,
    EvalSuite,
    EvalResult,
    TaskResult,
)
from .runner import EvalRunner
from .grader import (
    Grader,
    ExactMatchGrader,
    RegexGrader,
    CodeTestGrader,
    SchemaGrader,
)

__all__ = [
    "EvalCategory",
    "EvalTask",
    "EvalSuite",
    "EvalResult",
    "TaskResult",
    "EvalRunner",
    "Grader",
    "ExactMatchGrader",
    "RegexGrader",
    "CodeTestGrader",
    "SchemaGrader",
]
