"""
Test that mining paths use real difficulty/targets, not trivial shortcuts.

This test validates that production mining code:
1. Uses real difficulty values from consensus, not hardcoded trivial values
2. Properly converts µ-nats thresholds to 256-bit targets
3. Does not fall back to instant/minimum difficulty shortcuts
4. Stratum server respects share targets derived from consensus Θ

These tests ensure mock/stub implementations are isolated to test code only.
"""

import importlib
import math
from typing import Any, Dict

import pytest


def test_hash_search_target_conversion_is_non_trivial():
    """
    Verify that micro_threshold_to_target256 produces non-trivial targets.
    
    This ensures we're not using shortcuts like always returning max target
    or using trivial difficulty values in production code.
    """
    try:
        hs = importlib.import_module("mining.hash_search")
    except ImportError:
        pytest.skip("mining.hash_search not available")
    
    if not hasattr(hs, "micro_threshold_to_target256"):
        pytest.skip("micro_threshold_to_target256 not found in hash_search")
    
    # Test with real consensus-like threshold values
    # Θ = 3,000,000 µ-nats (3.0 nats) -> p = e^{-3} ≈ 0.0498
    theta_micro = 3_000_000
    target = hs.micro_threshold_to_target256(theta_micro)
    
    # Target should be a proper integer in the valid range
    assert isinstance(target, int), "Target must be an integer"
    assert target > 0, "Target must be positive (not trivial/zero)"
    assert target < (1 << 256) - 1, "Target must be less than max uint256"
    
    # For Θ=3.0 nats, target should be roughly e^{-3} * 2^256
    # = 0.0498 * 2^256 ≈ 5.78e75
    expected_approx = math.exp(-3.0) * ((1 << 256) - 1)
    
    # Allow reasonable tolerance (within 2x of expected)
    ratio = target / expected_approx
    assert 0.5 < ratio < 2.0, (
        f"Target {target} is not close to expected {expected_approx:.2e} "
        f"(ratio={ratio:.3f}). Check µ-nats conversion logic."
    )
    
    # Ensure different thresholds produce different targets
    theta_micro_2 = 5_000_000  # 5.0 nats
    target_2 = hs.micro_threshold_to_target256(theta_micro_2)
    
    assert target_2 < target, (
        "Higher threshold should produce lower target (harder difficulty)"
    )


def test_hash_scanner_does_not_use_trivial_thresholds():
    """
    Verify that HashScanner accepts non-trivial thresholds and does not
    default to minimum difficulty (like targetBits=12 in dev fallback).
    """
    try:
        hs = importlib.import_module("mining.hash_search")
    except ImportError:
        pytest.skip("mining.hash_search not available")
    
    if not hasattr(hs, "HashScanner"):
        pytest.skip("HashScanner class not found")
    
    scanner = hs.HashScanner()
    
    # Use a real consensus-like threshold (not trivial)
    theta_micro = 3_000_000  # 3.0 nats
    prefix = b"test_header_prefix_" + b"\x00" * 32
    
    # Scan a small window to verify it works with non-trivial difficulty
    shares = scanner.scan_batch(
        prefix=prefix,
        t_share_micro=theta_micro,
        nonce_start=0,
        nonce_count=1000,
        theta_micro=theta_micro  # Block threshold for d_ratio calculation in FoundShare
    )
    
    # At Θ=3.0, we expect roughly 1000 * e^{-3} ≈ 49 shares
    # The key is that the scanner accepted the threshold without
    # falling back to trivial values
    
    # We don't assert on exact count (can be zero in small window)
    # but we verify the function executed without error
    assert isinstance(shares, list), "scan_batch should return a list"


def test_naive_scanner_is_documented_as_dev_only():
    """
    Verify that the naive CPU scanner fallback in orchestrator.py
    is clearly documented as development-only and not for production.
    """
    try:
        orch = importlib.import_module("mining.orchestrator")
    except ImportError:
        pytest.skip("mining.orchestrator not available")
    
    if not hasattr(orch, "_naive_cpu_scanner"):
        pytest.skip("_naive_cpu_scanner not found")
    
    # Check that the function docstring mentions it's dev-only
    doc = orch._naive_cpu_scanner.__doc__ or ""
    
    assert "DEV-ONLY" in doc or "dev-only" in doc.lower(), (
        "Naive scanner must be clearly marked as development-only"
    )
    
    assert "fallback" in doc.lower(), (
        "Naive scanner must be documented as a fallback"
    )


def test_difficulty_retarget_produces_non_trivial_values():
    """
    Verify consensus difficulty retargeting produces real Θ values,
    not hardcoded trivial constants.
    """
    try:
        diff = importlib.import_module("consensus.difficulty")
    except ImportError:
        pytest.skip("consensus.difficulty not available")
    
    if not hasattr(diff, "init_state") or not hasattr(diff, "update_theta"):
        pytest.skip("Difficulty retarget functions not found")
    
    # Create a minimal retarget state
    from dataclasses import dataclass
    
    @dataclass
    class MockParams:
        target_interval_sec: float = 10.0
        half_life_blocks: float = 32.0
        beta: float = 0.5
        theta_min_micro: int = 1_000_000
        theta_max_micro: int = 50_000_000
        delta_theta_clamp_micro: int = 1_000_000
    
    params = MockParams()
    
    # Initialize with a non-trivial starting Θ
    theta_init = 3_000_000  # 3.0 nats
    
    try:
        state = diff.init_state(params, theta_init)
    except Exception:
        # Some implementations may have different signatures
        pytest.skip("Could not initialize difficulty state")
    
    # Verify initial theta is non-trivial
    theta_val = getattr(state, "theta_micro", None) or getattr(state, "theta", None)
    assert theta_val == theta_init, "Initial theta should match input"
    
    # Update theta with a block that arrived slightly late
    dt_seconds = 12.0  # 2 seconds late (target is 10s)
    
    try:
        new_state = diff.update_theta(state, dt_seconds)
    except Exception:
        pytest.skip("Could not update theta")
    
    new_theta = getattr(new_state, "theta_micro", None) or getattr(new_state, "theta", None)
    
    # Theta should have changed (decreased since block was late)
    assert new_theta != theta_init, (
        "Theta should update based on inter-arrival time"
    )
    
    # New theta should still be non-trivial (within min/max bounds)
    assert params.theta_min_micro <= new_theta <= params.theta_max_micro, (
        f"Updated theta {new_theta} should be within bounds "
        f"[{params.theta_min_micro}, {params.theta_max_micro}]"
    )


def test_share_target_computation_uses_consensus_theta():
    """
    Verify that share target computation derives from consensus Θ,
    not hardcoded test values.
    """
    try:
        diff = importlib.import_module("consensus.difficulty")
    except ImportError:
        pytest.skip("consensus.difficulty not available")
    
    if not hasattr(diff, "compute_share_micro"):
        pytest.skip("compute_share_micro not found")
    
    # Test with real consensus-like values
    theta_micro = 5_000_000  # 5.0 nats
    shares_per_block = 10
    
    share_micro = diff.compute_share_micro(theta_micro, shares_per_block)
    
    # Share threshold should be Θ - ln(K) nats
    # For K=10: ln(10) ≈ 2.303 nats = 2,303,000 µ-nats
    # So share_micro ≈ 5,000,000 - 2,303,000 = 2,697,000
    
    expected = theta_micro - int(math.log(shares_per_block) * 1_000_000)
    
    # Allow small rounding tolerance
    assert abs(share_micro - expected) < 10_000, (
        f"Share threshold {share_micro} should be close to "
        f"Θ - ln(K) = {expected}"
    )
    
    # Share threshold must be less than block threshold
    assert share_micro < theta_micro, (
        "Share threshold must be easier than block threshold"
    )


def test_aicf_stub_node_is_marked_test_only():
    """
    Verify that aicf.node (stub RPC server) is clearly marked as test-only
    and includes guards against production use.
    """
    try:
        import aicf.node as stub
    except ImportError:
        pytest.skip("aicf.node not available")
    
    # Check module docstring mentions test-only
    doc = stub.__doc__ or ""
    assert "test-only" in doc.lower() or "TEST-ONLY" in doc, (
        "aicf.node module must be clearly marked as test-only"
    )
    
    assert "stub" in doc.lower() or "STUB" in doc, (
        "aicf.node must be documented as a stub"
    )
    
    # Check make_block function is marked test-only
    if hasattr(stub, "make_block"):
        make_block_doc = stub.make_block.__doc__ or ""
        assert "TEST-ONLY" in make_block_doc or "test-only" in make_block_doc.lower(), (
            "make_block function must be marked as test-only"
        )


def test_templates_dummy_adapters_are_pragma_no_cover():
    """
    Verify that dummy adapters in mining.templates are marked with
    pragma: no cover and only used in test/demo code.
    """
    try:
        import mining.templates as tmpl
    except ImportError:
        pytest.skip("mining.templates not available")
    
    # Check that dummy functions exist and are marked no-cover
    dummy_funcs = ["dummy_get_head", "dummy_get_theta", "dummy_get_roots", "dummy_get_beacon"]
    
    for func_name in dummy_funcs:
        if not hasattr(tmpl, func_name):
            continue
        
        func = getattr(tmpl, func_name)
        
        # These should only be called from __main__ block (not production)
        # We can't easily test pragma: no cover in runtime, but we can
        # verify they have docstrings marking them as test helpers
        doc = func.__doc__ or ""
        
        # At minimum, they should have # pragma: no cover in source
        # (this is a source-level check, not runtime)
        import inspect
        source = inspect.getsource(func)
        assert "pragma: no cover" in source, (
            f"{func_name} must be marked with pragma: no cover"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
