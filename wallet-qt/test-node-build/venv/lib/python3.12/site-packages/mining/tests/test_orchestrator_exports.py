"""
Test that the mining.orchestrator module exports the expected classes and functions.

This test ensures backward compatibility with the mining CLI which expects
to find an 'Orchestrator' class in the module.
"""

import pytest
from mining import orchestrator as miner_orchestrator


def test_orchestrator_exports_orchestrator_class():
    """Test that mining.orchestrator exports an 'Orchestrator' class."""
    assert hasattr(miner_orchestrator, "Orchestrator"), (
        "mining.orchestrator module must export 'Orchestrator' class "
        "for CLI compatibility"
    )


def test_orchestrator_is_alias_for_miner_orchestrator():
    """Test that Orchestrator is an alias for MinerOrchestrator."""
    assert hasattr(miner_orchestrator, "MinerOrchestrator")
    assert hasattr(miner_orchestrator, "Orchestrator")
    assert miner_orchestrator.Orchestrator is miner_orchestrator.MinerOrchestrator, (
        "Orchestrator should be an alias for MinerOrchestrator"
    )


def test_orchestrator_has_run_forever_method():
    """Test that Orchestrator class has the run_forever method expected by CLI."""
    Orchestrator = miner_orchestrator.Orchestrator
    assert hasattr(Orchestrator, "run_forever"), (
        "Orchestrator class must have 'run_forever' method for CLI compatibility"
    )


def test_orchestrator_exports_config_class():
    """Test that mining.orchestrator exports OrchestratorConfig."""
    assert hasattr(miner_orchestrator, "OrchestratorConfig")


def test_orchestrator_exports_run_function():
    """Test that mining.orchestrator exports run_orchestrator function."""
    assert hasattr(miner_orchestrator, "run_orchestrator")
    assert callable(miner_orchestrator.run_orchestrator)


def test_orchestrator_exports_cli_main():
    """Test that mining.orchestrator exports cli_main function."""
    assert hasattr(miner_orchestrator, "cli_main")
    assert callable(miner_orchestrator.cli_main)


def test_all_exports():
    """Test that __all__ includes all expected exports."""
    expected_exports = [
        "OrchestratorConfig",
        "MinerOrchestrator", 
        "Orchestrator",
        "run_orchestrator",
        "cli_main",
    ]
    
    for export in expected_exports:
        assert export in miner_orchestrator.__all__, (
            f"{export} should be in __all__"
        )
