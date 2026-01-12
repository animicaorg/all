"""Tests for transaction submission readiness assessment."""
from __future__ import annotations

import pytest

from animica.sync.readiness import assess_tx_submission_readiness


def test_blocks_when_head_behind_best_header():
    """Node at height 99, network at 100 -> BLOCK."""
    status = {
        "head_height": 99,
        "best_header_height": 100,
        "in_flight_headers": 0,
        "in_flight_blocks": 0,
        "queued_blocks_count": 0,
    }
    allowed, info = assess_tx_submission_readiness(status)
    assert not allowed
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


def test_blocks_when_one_block_behind_even_if_synced_phase():
    """Even with SYNCED phase, being 1 block behind should block."""
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
    assert not allowed
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


def test_blocks_behind_with_at_tip_error():
    """When behind with 'at_tip' error, still block."""
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


def test_allows_when_at_tip_with_stale_syncing_headers_phase():
    """
    When node is at tip (head == best_header) with no in-flight work,
    allow transactions even if phase is stale 'SYNCING_HEADERS'.
    
    This fixes the issue where node reports SYNCING_HEADERS phase but
    is actually at network tip (headers: 5458, blocks: 5458).
    """
    status = {
        "phase": "SYNCING_HEADERS",
        "head_height": 5458,
        "best_header_height": 5458,
        "best_block_height": 5458,
        "synchronized": False,  # May not be properly set
        "syncing": True,  # May still show as syncing
        "in_flight_headers": 0,
        "in_flight_blocks": 0,
        "queued_blocks_count": 0,
        "pending_header_batches": 0,
    }
    allowed, info = assess_tx_submission_readiness(status)
    assert allowed, "Should allow transactions when at tip with no in-flight work"
    assert info["head_height"] == 5458
    assert info["best_header_height"] == 5458


def test_allows_when_at_tip_with_stale_pending_header_batches():
    """
    When node is at tip with stale pending_header_batches but no active sync work,
    allow transactions. The pending batches may be for heights we already have.
    
    This is the exact scenario from the issue: stuck at SYNCING_HEADERS with
    headers==blocks but pending_header_batches preventing tx submission.
    """
    status = {
        "phase": "SYNCING_HEADERS",
        "head_height": 5458,
        "best_header_height": 5458,
        "best_block_height": 5458,
        "synchronized": False,
        "syncing": True,
        "in_flight_headers": 0,  # No active header requests
        "in_flight_blocks": 0,  # No active block requests
        "queued_blocks_count": 0,  # No queued blocks
        "pending_header_batches": 3,  # Stale pending batches for already-synced heights
    }
    allowed, info = assess_tx_submission_readiness(status)
    assert allowed, (
        "Should allow transactions when at tip even with stale pending_header_batches, "
        "as long as no active sync work is in progress"
    )
    assert info["head_height"] == 5458
    assert info["best_header_height"] == 5458
    assert info["pending_header_batches"] == 3
