"""
Tests for evaluation system.
"""

import pytest
from ena.evals import (
    EvalCategory,
    EvalTask,
    EvalSuite,
    EvalRunner,
    TaskResult,
    ExactMatchGrader,
    RegexGrader,
    get_grader,
)


class TestGraders:
    """Tests for grader implementations."""
    
    def test_exact_match_grader(self):
        """Test exact match grading."""
        grader = ExactMatchGrader()
        
        passed, score, msg = grader.grade("hello", "hello")
        assert passed is True
        assert score == 1.0
        
        passed, score, msg = grader.grade("hello", "Hello")
        assert passed is False
        assert score == 0.0
    
    def test_exact_match_ignore_case(self):
        """Test exact match with case insensitivity."""
        grader = ExactMatchGrader({"ignore_case": True})
        
        passed, score, msg = grader.grade("Hello", "hello")
        assert passed is True
        assert score == 1.0
    
    def test_regex_grader(self):
        """Test regex grading."""
        grader = RegexGrader()
        
        passed, score, msg = grader.grade("hello world", r"hello.*")
        assert passed is True
        assert score == 1.0
        
        passed, score, msg = grader.grade("goodbye", r"hello.*")
        assert passed is False
        assert score == 0.0
    
    def test_get_grader(self):
        """Test grader factory."""
        grader = get_grader("exact_match")
        assert isinstance(grader, ExactMatchGrader)
        
        grader = get_grader("regex")
        assert isinstance(grader, RegexGrader)
        
        with pytest.raises(ValueError):
            get_grader("unknown_grader")


class TestEvalSuite:
    """Tests for eval suite."""
    
    def test_create_suite(self):
        """Test creating an eval suite."""
        suite = EvalSuite(
            name="test_suite",
            version="1.0",
        )
        
        assert suite.name == "test_suite"
        assert suite.version == "1.0"
        assert len(suite.tasks) == 0
    
    def test_add_task(self):
        """Test adding tasks to suite."""
        suite = EvalSuite(name="test", version="1.0")
        
        task = EvalTask(
            task_id="task1",
            category=EvalCategory.CODE_REASONING,
            prompt="What is 2+2?",
            expected="4",
        )
        
        suite.add_task(task)
        assert len(suite.tasks) == 1
        assert suite.tasks[0].task_id == "task1"
    
    def test_get_tasks_by_category(self):
        """Test filtering tasks by category."""
        suite = EvalSuite(name="test", version="1.0")
        
        suite.add_task(EvalTask(
            task_id="code1",
            category=EvalCategory.CODE_REASONING,
            prompt="test",
            expected="test",
        ))
        
        suite.add_task(EvalTask(
            task_id="math1",
            category=EvalCategory.MATH_LOGIC,
            prompt="test",
            expected="test",
        ))
        
        code_tasks = suite.get_tasks_by_category(EvalCategory.CODE_REASONING)
        assert len(code_tasks) == 1
        assert code_tasks[0].task_id == "code1"


class TestEvalRunner:
    """Tests for eval runner."""
    
    def test_run_simple_suite(self):
        """Test running a simple eval suite."""
        # Create suite
        suite = EvalSuite(name="simple", version="1.0")
        
        suite.add_task(EvalTask(
            task_id="task1",
            category=EvalCategory.MATH_LOGIC,
            prompt="What is 2+2?",
            expected="4",
            grader_type="exact_match",
        ))
        
        suite.add_task(EvalTask(
            task_id="task2",
            category=EvalCategory.MATH_LOGIC,
            prompt="What is 3+3?",
            expected="6",
            grader_type="exact_match",
        ))
        
        # Simple model that answers correctly
        def simple_model(prompt: str) -> str:
            if "2+2" in prompt:
                return "4"
            elif "3+3" in prompt:
                return "6"
            return "unknown"
        
        # Run evaluation
        runner = EvalRunner(suite, simple_model, model_hash="test_model")
        result = runner.run()
        
        assert result.suite_name == "simple"
        assert result.num_tasks == 2
        assert result.num_passed == 2
        assert result.pass_rate == 1.0
        assert result.total_score == 100.0
    
    def test_run_with_failures(self):
        """Test running suite with some failures."""
        suite = EvalSuite(name="test", version="1.0")
        
        suite.add_task(EvalTask(
            task_id="task1",
            category=EvalCategory.MATH_LOGIC,
            prompt="What is 2+2?",
            expected="4",
        ))
        
        suite.add_task(EvalTask(
            task_id="task2",
            category=EvalCategory.MATH_LOGIC,
            prompt="What is 3+3?",
            expected="6",
        ))
        
        # Model that gets one wrong
        def faulty_model(prompt: str) -> str:
            if "2+2" in prompt:
                return "5"  # Wrong!
            elif "3+3" in prompt:
                return "6"
            return "unknown"
        
        runner = EvalRunner(suite, faulty_model)
        result = runner.run()
        
        assert result.num_tasks == 2
        assert result.num_passed == 1
        assert result.pass_rate == 0.5
        assert result.total_score < 100.0
    
    def test_category_scores(self):
        """Test category score calculation."""
        suite = EvalSuite(name="test", version="1.0")
        
        # Add tasks in different categories
        suite.add_task(EvalTask(
            task_id="code1",
            category=EvalCategory.CODE_REASONING,
            prompt="test",
            expected="correct",
        ))
        
        suite.add_task(EvalTask(
            task_id="math1",
            category=EvalCategory.MATH_LOGIC,
            prompt="test",
            expected="correct",
        ))
        
        def model(prompt: str) -> str:
            return "correct"
        
        runner = EvalRunner(suite, model)
        result = runner.run()
        
        assert "code_reasoning" in result.category_scores
        assert "math_logic" in result.category_scores
        assert result.category_scores["code_reasoning"] == 100.0
        assert result.category_scores["math_logic"] == 100.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
