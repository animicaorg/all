"""Tests for transaction submission readiness assessment."""
from __future__ import annotations

import pytest

from animica.sync.readiness import assess_tx_submission_readiness


def test_allows_when_head_slightly_behind_best_header():
    """Node at height 99, network at 100 -> ALLOW (within default lag tolerance)."""
    status = {
        "head_height": 99,
        "best_header_height": 100,
        "in_flight_headers": 0,
        "in_flight_blocks": 0,
        "queued_blocks_count": 0,
    }
    allowed, info = assess_tx_submission_readiness(status)
    assert allowed
    assert info["head_height"] == 99
    assert info["best_header_height"] == 100


def test_allows_when_at_highest_height():
    """Node at height 100, network at 100 -> ALLOW."""
    status = {
        "head_height": 100,
        "best_header_height": 100,
        "synchronized": True,
        "in_flight_headers": 0,
        "in_flight_blocks": 0,
        "queued_blocks_count": 0,
    }
    allowed, info = assess_tx_submission_readiness(status)
    assert allowed
    assert info["head_height"] == 100
    assert info["best_header_height"] == 100


def test_allows_when_ahead_of_network():
    """Node at height 105, network at 100 -> ALLOW."""
    status = {
        "head_height": 105,
        "best_header_height": 100,
        "synchronized": True,
        "in_flight_headers": 0,
        "in_flight_blocks": 0,
        "queued_blocks_count": 0,
    }
    allowed, info = assess_tx_submission_readiness(status)
    assert allowed
    assert info["head_height"] == 105
    assert info["best_header_height"] == 100


def test_blocks_when_significantly_behind():
    """Node at height 50, network at 100 -> BLOCK."""
    status = {
        "head_height": 50,
        "best_header_height": 100,
        "phase": "HEADERS",
        "synchronized": False,
        "in_flight_headers": 0,
        "in_flight_blocks": 0,
        "queued_blocks_count": 0,
    }
    allowed, info = assess_tx_submission_readiness(status)
    assert not allowed
    assert info["head_height"] == 50
    assert info["best_header_height"] == 100


def test_blocks_when_behind_more_than_configured_lag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANIMICA_TX_SUBMIT_MAX_BEHIND", "0")
    status = {
        "head_height": 99,
        "best_header_height": 100,
        "phase": "SYNCED",
        "synchronized": True,
    }
    allowed, info = assess_tx_submission_readiness(status)
    assert not allowed
    assert info["blocks_behind"] == 1
    assert info["max_allowed_behind"] == 0


def test_allows_when_synced_phase():
    """Node with SYNCED phase at same height -> ALLOW."""
    status = {
        "phase": "SYNCED",
        "head_height": 100,
        "best_header_height": 100,
        "synchronized": True,
        "in_flight_headers": 0,
        "in_flight_blocks": 0,
        "queued_blocks_count": 0,
    }
    allowed, info = assess_tx_submission_readiness(status)
    assert allowed


def test_allows_when_synchronized_true():
    """Node with synchronized=True flag at same height -> ALLOW."""
    status = {
        "synchronized": True,
        "head_height": 200,
        "best_header_height": 200,
        "in_flight_headers": 0,
        "in_flight_blocks": 0,
        "queued_blocks_count": 0,
    }
    allowed, info = assess_tx_submission_readiness(status)
    assert allowed


def test_allows_when_one_block_behind_even_if_synced_phase():
    """Even with SYNCED phase, 1 block behind is allowed by default tolerance."""
    status = {
        "phase": "SYNCED",
        "synchronized": True,
        "head_height": 99,
        "best_header_height": 100,
        "in_flight_headers": 0,
        "in_flight_blocks": 0,
        "queued_blocks_count": 0,
    }
    allowed, info = assess_tx_submission_readiness(status)
    assert allowed
    assert info["head_height"] == 99
    assert info["best_header_height"] == 100


def test_allows_when_heights_unknown():
    """When heights are None, fall back to sync status checks."""
    status = {
        "synchronized": True,
        "syncing": False,
    }
    allowed, info = assess_tx_submission_readiness(status)
    assert allowed


def test_blocks_when_heights_unknown_and_not_synced():
    """When heights are None and not synced, block."""
    status = {
        "synchronized": False,
        "syncing": True,
        "phase": "HEADERS",
    }
    allowed, info = assess_tx_submission_readiness(status)
    assert not allowed


def test_allows_at_tip_with_at_tip_error():
    """When at tip with 'at_tip' error, allow."""
    status = {
        "head_height": 100,
        "best_header_height": 100,
        "last_header_error": "at_tip",
        "in_flight_headers": 0,
        "in_flight_blocks": 0,
        "queued_blocks_count": 0,
    }
    allowed, info = assess_tx_submission_readiness(status)
    assert allowed


def test_allows_behind_with_at_tip_error_within_tolerance():
    """When slightly behind with at_tip, allow by default tolerance."""
    status = {
        "head_height": 99,
        "best_header_height": 100,
        "last_header_error": "at_tip",
        "in_flight_headers": 0,
        "in_flight_blocks": 0,
        "queued_blocks_count": 0,
    }
    allowed, info = assess_tx_submission_readiness(status)
    assert allowed


def test_blocks_behind_with_at_tip_error_when_tolerance_zero(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANIMICA_TX_SUBMIT_MAX_BEHIND", "0")
    status = {
        "head_height": 99,
        "best_header_height": 100,
        "last_header_error": "at_tip",
        "in_flight_headers": 0,
        "in_flight_blocks": 0,
        "queued_blocks_count": 0,
    }
    allowed, info = assess_tx_submission_readiness(status)
    assert not allowed
