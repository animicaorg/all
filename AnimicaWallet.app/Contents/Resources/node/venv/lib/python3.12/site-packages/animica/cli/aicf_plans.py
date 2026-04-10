"""
AICF Built-in Job Plans
========================

Provides built-in training/testing job plans for AICF marketplace.
Each plan includes configuration, budget estimates, and alert thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class JobPlan:
    """Represents a built-in AICF job plan."""
    
    name: str
    description: str
    category: str  # "testing", "maintenance", "training", "qa"
    min_budget: int  # Minimum AICF credits required
    estimated_duration: str  # Human-readable duration estimate
    default_params: Dict[str, Any] = field(default_factory=dict)
    required_capabilities: List[str] = field(default_factory=list)
    alert_thresholds: Dict[str, Any] = field(default_factory=dict)
    example_output: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert plan to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "min_budget": self.min_budget,
            "estimated_duration": self.estimated_duration,
            "default_params": self.default_params,
            "required_capabilities": self.required_capabilities,
            "alert_thresholds": self.alert_thresholds,
            "example_output": self.example_output,
        }


# Built-in plans focused on ENA + chain quality
BUILTIN_PLANS: Dict[str, JobPlan] = {
    "ena_smoke": JobPlan(
        name="ena_smoke",
        description="Quick smoke test for ENA inference API (single prompt, fast response)",
        category="testing",
        min_budget=100,
        estimated_duration="30 seconds",
        default_params={
            "prompt": "Hello, world!",
            "max_tokens": 50,
            "model": "default",
            "timeout": 60,
        },
        required_capabilities=["ena_inference"],
        alert_thresholds={
            "max_duration_seconds": 120,
            "min_response_length": 1,
        },
        example_output="Single inference response with latency metrics",
    ),
    
    "ena_regression": JobPlan(
        name="ena_regression",
        description="ENA regression test suite (multiple prompts, quality checks)",
        category="testing",
        min_budget=5000,
        estimated_duration="5-10 minutes",
        default_params={
            "prompt_suite": "standard",  # Can reference a test suite
            "num_prompts": 20,
            "model": "default",
            "quality_threshold": 0.8,
        },
        required_capabilities=["ena_inference"],
        alert_thresholds={
            "max_duration_seconds": 900,  # 15 minutes
            "min_success_rate": 0.95,
            "max_failure_rate": 0.05,
        },
        example_output="Test results with pass/fail counts, latency stats, quality scores",
    ),
    
    "repo_index_refresh": JobPlan(
        name="repo_index_refresh",
        description="Refresh repository embeddings and index for ENA context",
        category="maintenance",
        min_budget=10000,
        estimated_duration="10-30 minutes",
        default_params={
            "repo_url": "",  # Must be provided
            "branch": "main",
            "include_patterns": ["*.py", "*.md", "*.rs"],
            "exclude_patterns": ["node_modules", ".git", "__pycache__"],
            "chunk_size": 1000,
            "overlap": 200,
        },
        required_capabilities=["embedding_generation", "vector_storage"],
        alert_thresholds={
            "max_duration_seconds": 3600,  # 1 hour
            "min_files_processed": 1,
        },
        example_output="Index statistics: files processed, chunks generated, vector store updated",
    ),
    
    "tx_mempool_fuzz": JobPlan(
        name="tx_mempool_fuzz",
        description="Fuzz test transaction decoding and mempool admission logic",
        category="qa",
        min_budget=2000,
        estimated_duration="2-5 minutes",
        default_params={
            "num_transactions": 1000,
            "mutation_types": ["invalid_signature", "malformed_cbor", "missing_fields", "boundary_values"],
            "target": "mempool_admission",
        },
        required_capabilities=["tx_fuzzing"],
        alert_thresholds={
            "max_duration_seconds": 600,
            "max_crash_count": 0,  # No crashes allowed
            "min_coverage": 0.7,
        },
        example_output="Fuzzing results: mutations tested, crashes found, coverage achieved",
    ),
    
    "rpc_conformance": JobPlan(
        name="rpc_conformance",
        description="OpenRPC conformance testing with negative test cases",
        category="qa",
        min_budget=3000,
        estimated_duration="5-10 minutes",
        default_params={
            "spec_url": "/openrpc.json",
            "test_modes": ["positive", "negative", "boundary"],
            "parallel_requests": 10,
        },
        required_capabilities=["rpc_testing"],
        alert_thresholds={
            "max_duration_seconds": 900,
            "min_pass_rate": 0.98,
            "max_timeout_rate": 0.01,
        },
        example_output="Conformance report: methods tested, pass/fail/timeout counts, spec violations",
    ),
    
    "wallet_e2e": JobPlan(
        name="wallet_e2e",
        description="End-to-end wallet test suite (balance, send, receive flows)",
        category="testing",
        min_budget=2500,
        estimated_duration="3-7 minutes",
        default_params={
            "num_wallets": 5,
            "operations": ["create", "fund", "transfer", "check_balance"],
            "network": "devnet",
        },
        required_capabilities=["wallet_operations", "rpc_access"],
        alert_thresholds={
            "max_duration_seconds": 600,
            "min_success_rate": 0.95,
            "max_balance_mismatch": 0,
        },
        example_output="Test results: wallets created, transfers successful, final balances verified",
    ),
    
    "consensus_sanity": JobPlan(
        name="consensus_sanity",
        description="Consensus health check: block production, stale template detection",
        category="testing",
        min_budget=1500,
        estimated_duration="2-5 minutes",
        default_params={
            "observation_window": 300,  # 5 minutes
            "min_blocks_expected": 3,
            "check_stale_templates": True,
            "check_fork_choice": True,
        },
        required_capabilities=["chain_monitoring"],
        alert_thresholds={
            "max_duration_seconds": 400,
            "min_blocks_produced": 3,
            "max_stale_template_rate": 0.1,
        },
        example_output="Health report: blocks produced, stale templates, fork choice consistency",
    ),
    
    "p2p_gossip_health": JobPlan(
        name="p2p_gossip_health",
        description="P2P network health: peer connectivity, transaction relay, block propagation",
        category="testing",
        min_budget=2000,
        estimated_duration="3-8 minutes",
        default_params={
            "num_test_peers": 5,
            "test_tx_relay": True,
            "test_block_relay": True,
            "observation_window": 300,
        },
        required_capabilities=["p2p_testing"],
        alert_thresholds={
            "max_duration_seconds": 600,
            "min_connected_peers": 3,
            "min_relay_success_rate": 0.9,
        },
        example_output="Network health: peers connected, tx/block relay success rates, latency stats",
    ),
}


def get_plan(name: str) -> Optional[JobPlan]:
    """Get a built-in plan by name."""
    return BUILTIN_PLANS.get(name)


def list_plans(category: Optional[str] = None) -> List[JobPlan]:
    """
    List all built-in plans, optionally filtered by category.
    
    Args:
        category: Filter by category (testing, maintenance, training, qa)
        
    Returns:
        List of matching plans
    """
    plans = list(BUILTIN_PLANS.values())
    if category:
        plans = [p for p in plans if p.category == category]
    return sorted(plans, key=lambda p: p.name)


def validate_plan_params(plan: JobPlan, user_params: Dict[str, Any]) -> List[str]:
    """
    Validate user-provided parameters against plan requirements.
    
    Args:
        plan: Job plan
        user_params: User-provided parameters
        
    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []
    
    # Check for required params (those without defaults)
    for key, default_val in plan.default_params.items():
        if default_val == "" and key not in user_params:
            errors.append(f"Missing required parameter: {key}")
    
    return errors


__all__ = [
    "JobPlan",
    "BUILTIN_PLANS",
    "get_plan",
    "list_plans",
    "validate_plan_params",
]
