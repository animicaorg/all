"""
Graders for evaluation tasks.

Provides deterministic grading functions for different types of tasks.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Dict

__all__ = [
    "Grader",
    "ExactMatchGrader",
    "RegexGrader",
    "CodeTestGrader",
    "SchemaGrader",
    "get_grader",
]


class Grader(ABC):
    """Base class for graders."""
    
    def __init__(self, config: Dict[str, Any] = None):
        """Initialize grader with configuration."""
        self.config = config or {}
    
    @abstractmethod
    def grade(self, output: Any, expected: Any) -> tuple[bool, float, str]:
        """
        Grade an output against expected result.
        
        Returns:
            (passed, score, message) where:
            - passed: bool indicating if task passed
            - score: float from 0.0 to 1.0
            - message: explanation of the result
        """
        pass


class ExactMatchGrader(Grader):
    """Grader that checks for exact string match."""
    
    def grade(self, output: Any, expected: Any) -> tuple[bool, float, str]:
        """Grade by exact match."""
        ignore_case = self.config.get("ignore_case", False)
        ignore_whitespace = self.config.get("ignore_whitespace", False)
        
        output_str = str(output)
        expected_str = str(expected)
        
        if ignore_case:
            output_str = output_str.lower()
            expected_str = expected_str.lower()
        
        if ignore_whitespace:
            output_str = " ".join(output_str.split())
            expected_str = " ".join(expected_str.split())
        
        passed = output_str == expected_str
        score = 1.0 if passed else 0.0
        message = "Exact match" if passed else f"Expected '{expected_str}', got '{output_str}'"
        
        return passed, score, message


class RegexGrader(Grader):
    """Grader that checks if output matches a regex pattern."""
    
    def grade(self, output: Any, expected: Any) -> tuple[bool, float, str]:
        """Grade by regex match."""
        output_str = str(output)
        pattern = str(expected)
        
        ignore_case = self.config.get("ignore_case", False)
        flags = re.IGNORECASE if ignore_case else 0
        
        try:
            match = re.search(pattern, output_str, flags=flags)
            passed = match is not None
            score = 1.0 if passed else 0.0
            message = f"Matches pattern '{pattern}'" if passed else f"Does not match pattern '{pattern}'"
            
            return passed, score, message
        except re.error as e:
            return False, 0.0, f"Invalid regex pattern: {e}"


class CodeTestGrader(Grader):
    """Grader for code tasks using test functions."""
    
    def grade(self, output: Any, expected: Any) -> tuple[bool, float, str]:
        """
        Grade code output.
        
        Expected format:
        {
            "test_function": "def test(output): return output == 42",
            "description": "Should return 42"
        }
        """
        if not isinstance(expected, dict):
            return False, 0.0, "Invalid expected format for code test"
        
        test_func_str = expected.get("test_function", "")
        description = expected.get("description", "Code test")
        
        if not test_func_str:
            return False, 0.0, "No test function provided"
        
        try:
            # Create a safe namespace for execution
            namespace = {}
            exec(test_func_str, namespace)
            
            # Get the test function
            test_func = namespace.get("test")
            if test_func is None:
                return False, 0.0, "Test function not found in code"
            
            # Run the test
            result = test_func(output)
            passed = bool(result)
            score = 1.0 if passed else 0.0
            message = f"{description}: {'passed' if passed else 'failed'}"
            
            return passed, score, message
            
        except Exception as e:
            return False, 0.0, f"Test execution error: {str(e)}"


class SchemaGrader(Grader):
    """Grader that validates output against a schema."""
    
    def grade(self, output: Any, expected: Any) -> tuple[bool, float, str]:
        """
        Grade by schema validation.
        
        Expected format:
        {
            "type": "dict",
            "required_keys": ["key1", "key2"],
            "optional_keys": ["key3"]
        }
        """
        if not isinstance(expected, dict):
            return False, 0.0, "Invalid expected format for schema validation"
        
        schema_type = expected.get("type", "any")
        required_keys = expected.get("required_keys", [])
        optional_keys = expected.get("optional_keys", [])
        
        # Check type
        if schema_type == "dict":
            if not isinstance(output, dict):
                return False, 0.0, f"Expected dict, got {type(output).__name__}"
            
            # Check required keys
            missing_keys = set(required_keys) - set(output.keys())
            if missing_keys:
                return False, 0.0, f"Missing required keys: {missing_keys}"
            
            # Check for extra keys
            all_allowed = set(required_keys + optional_keys)
            extra_keys = set(output.keys()) - all_allowed
            if extra_keys and not expected.get("allow_extra", False):
                return False, 0.5, f"Extra keys present: {extra_keys}"
            
            return True, 1.0, "Schema validation passed"
        
        elif schema_type == "list":
            if not isinstance(output, list):
                return False, 0.0, f"Expected list, got {type(output).__name__}"
            
            min_length = expected.get("min_length", 0)
            max_length = expected.get("max_length", float('inf'))
            
            if len(output) < min_length:
                return False, 0.0, f"List too short: {len(output)} < {min_length}"
            
            if len(output) > max_length:
                return False, 0.0, f"List too long: {len(output)} > {max_length}"
            
            return True, 1.0, "Schema validation passed"
        
        elif schema_type == "string":
            if not isinstance(output, str):
                return False, 0.0, f"Expected string, got {type(output).__name__}"
            return True, 1.0, "Schema validation passed"
        
        elif schema_type == "number":
            if not isinstance(output, (int, float)):
                return False, 0.0, f"Expected number, got {type(output).__name__}"
            return True, 1.0, "Schema validation passed"
        
        else:
            return True, 1.0, "No specific type check"


def get_grader(grader_type: str, config: Dict[str, Any] = None) -> Grader:
    """
    Get a grader instance by type.
    
    Args:
        grader_type: Type of grader (exact_match, regex, code_test, schema)
        config: Grader configuration
    
    Returns:
        Grader instance
    
    Raises:
        ValueError: If grader type is unknown
    """
    graders = {
        "exact_match": ExactMatchGrader,
        "regex": RegexGrader,
        "code_test": CodeTestGrader,
        "schema": SchemaGrader,
    }
    
    grader_class = graders.get(grader_type)
    if grader_class is None:
        raise ValueError(f"Unknown grader type: {grader_type}")
    
    return grader_class(config or {})
