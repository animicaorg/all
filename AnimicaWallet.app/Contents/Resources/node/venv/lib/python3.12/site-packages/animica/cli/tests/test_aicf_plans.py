"""
Unit tests for AICF job plans.
"""

from __future__ import annotations

import pytest

from animica.cli import aicf_plans


class TestJobPlan:
    """Tests for JobPlan dataclass."""
    
    def test_to_dict(self):
        """Should convert plan to dictionary."""
        plan = aicf_plans.JobPlan(
            name="test_plan",
            description="Test plan",
            category="testing",
            min_budget=100,
            estimated_duration="1 minute",
            default_params={"key": "value"},
            required_capabilities=["test_cap"],
            alert_thresholds={"max_duration": 60},
            example_output="Test output",
        )
        
        result = plan.to_dict()
        assert result["name"] == "test_plan"
        assert result["description"] == "Test plan"
        assert result["category"] == "testing"
        assert result["min_budget"] == 100
        assert result["default_params"] == {"key": "value"}


class TestBuiltinPlans:
    """Tests for built-in plans."""
    
    def test_all_plans_exist(self):
        """Should have all 8 required plans."""
        expected_plans = [
            "ena_smoke",
            "ena_regression",
            "repo_index_refresh",
            "tx_mempool_fuzz",
            "rpc_conformance",
            "wallet_e2e",
            "consensus_sanity",
            "p2p_gossip_health",
        ]
        
        for plan_name in expected_plans:
            assert plan_name in aicf_plans.BUILTIN_PLANS
            assert aicf_plans.get_plan(plan_name) is not None
    
    def test_plan_structure(self):
        """Each plan should have required fields."""
        for plan_name, plan in aicf_plans.BUILTIN_PLANS.items():
            assert plan.name == plan_name
            assert plan.description
            assert plan.category in ["testing", "maintenance", "training", "qa"]
            assert plan.min_budget > 0
            assert plan.estimated_duration
            assert isinstance(plan.default_params, dict)
            assert isinstance(plan.required_capabilities, list)
            assert isinstance(plan.alert_thresholds, dict)


class TestGetPlan:
    """Tests for get_plan function."""
    
    def test_returns_existing_plan(self):
        """Should return plan for valid name."""
        plan = aicf_plans.get_plan("ena_smoke")
        assert plan is not None
        assert plan.name == "ena_smoke"
    
    def test_returns_none_for_invalid_name(self):
        """Should return None for non-existent plan."""
        plan = aicf_plans.get_plan("nonexistent_plan")
        assert plan is None


class TestListPlans:
    """Tests for list_plans function."""
    
    def test_lists_all_plans(self):
        """Should return all plans when no filter."""
        plans = aicf_plans.list_plans()
        assert len(plans) == len(aicf_plans.BUILTIN_PLANS)
    
    def test_filters_by_category(self):
        """Should filter plans by category."""
        testing_plans = aicf_plans.list_plans(category="testing")
        assert all(p.category == "testing" for p in testing_plans)
        assert len(testing_plans) > 0
        
        qa_plans = aicf_plans.list_plans(category="qa")
        assert all(p.category == "qa" for p in qa_plans)
    
    def test_sorts_by_name(self):
        """Should return plans sorted by name."""
        plans = aicf_plans.list_plans()
        names = [p.name for p in plans]
        assert names == sorted(names)


class TestValidatePlanParams:
    """Tests for validate_plan_params function."""
    
    def test_passes_with_all_params(self):
        """Should pass when all required params provided."""
        plan = aicf_plans.JobPlan(
            name="test",
            description="test",
            category="testing",
            min_budget=100,
            estimated_duration="1m",
            default_params={"required": "", "optional": "default"},
        )
        
        user_params = {"required": "value", "optional": "override"}
        errors = aicf_plans.validate_plan_params(plan, user_params)
        assert errors == []
    
    def test_fails_with_missing_required(self):
        """Should fail when required params missing."""
        plan = aicf_plans.JobPlan(
            name="test",
            description="test",
            category="testing",
            min_budget=100,
            estimated_duration="1m",
            default_params={"required": ""},  # Empty string = required
        )
        
        user_params = {}
        errors = aicf_plans.validate_plan_params(plan, user_params)
        assert len(errors) > 0
        assert "required" in errors[0].lower()
    
    def test_passes_with_defaults(self):
        """Should pass when optional params have defaults."""
        plan = aicf_plans.JobPlan(
            name="test",
            description="test",
            category="testing",
            min_budget=100,
            estimated_duration="1m",
            default_params={"optional": "default_value"},
        )
        
        user_params = {}
        errors = aicf_plans.validate_plan_params(plan, user_params)
        assert errors == []


class TestSpecificPlans:
    """Tests for specific plan configurations."""
    
    def test_ena_smoke_configuration(self):
        """Smoke test plan should be quick and cheap."""
        plan = aicf_plans.get_plan("ena_smoke")
        assert plan is not None
        assert plan.min_budget <= 1000  # Should be very cheap
        assert "ena_inference" in plan.required_capabilities
    
    def test_repo_index_refresh_has_required_params(self):
        """Repo index plan should require repo_url."""
        plan = aicf_plans.get_plan("repo_index_refresh")
        assert plan is not None
        assert "repo_url" in plan.default_params
        assert plan.default_params["repo_url"] == ""  # Required param
    
    def test_tx_mempool_fuzz_has_alert_thresholds(self):
        """Fuzz test should have crash alert."""
        plan = aicf_plans.get_plan("tx_mempool_fuzz")
        assert plan is not None
        assert "max_crash_count" in plan.alert_thresholds
        assert plan.alert_thresholds["max_crash_count"] == 0
