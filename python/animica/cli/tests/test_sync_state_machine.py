"""
Tests for CLI sync state machine logic.

Verifies that _compute_sync_state correctly handles:
1. UNKNOWN state when no fresh peer tips
2. SYNCHRONIZED only with fresh peer confirmation
3. BEHIND/SYNCING states with proper conditions
4. STALLED state detection
"""
from __future__ import annotations

import pytest
from python.animica.cli.sync import _compute_sync_state


class TestSyncStateMachine:
    """Test the CLI sync state machine logic."""

    def test_unknown_when_no_best_remote(self):
        """State must be UNKNOWN when best_remote_height is None."""
        metrics = {
            "best_remote_height": None,  # No fresh peer tips
            "best_remote_age_sec": None,
            "behind_by": None,
            "sync_status_reason": "no_fresh_peer_tips",
            "best_header_height": 1000,
            "best_block_height": 1000,
            "phase": "IDLE",
            "syncing": False,
            "synchronized": False,
            "target_height": None,
        }
        
        state = _compute_sync_state(
            head_height=1000,
            network_height=None,
            metrics=metrics,
        )
        
        assert state == "UNKNOWN", "Must be UNKNOWN when best_remote_height is None"

    def test_unknown_when_head_height_none(self):
        """State must be UNKNOWN when head_height is None."""
        metrics = {
            "best_remote_height": 2000,
            "best_remote_age_sec": 5.0,
            "behind_by": None,
            "sync_status_reason": None,
            "best_header_height": 0,
            "best_block_height": 0,
            "phase": "IDLE",
            "syncing": False,
            "synchronized": False,
            "target_height": None,
        }
        
        state = _compute_sync_state(
            head_height=None,
            network_height=2000,
            metrics=metrics,
        )
        
        assert state == "UNKNOWN", "Must be UNKNOWN when head_height is None"

    def test_synchronized_requires_fresh_peer_confirmation(self):
        """SYNCHRONIZED state requires best_remote_height to be known."""
        # Case 1: With fresh peer confirmation - should be SYNCHRONIZED
        metrics = {
            "best_remote_height": 1000,  # Fresh peer tip
            "best_remote_age_sec": 5.0,
            "behind_by": 0,  # At tip
            "sync_status_reason": None,
            "best_header_height": 1000,
            "best_block_height": 1000,
            "phase": "SYNCED",
            "syncing": False,
            "synchronized": True,
            "target_height": 1000,
        }
        
        state = _compute_sync_state(
            head_height=1000,
            network_height=1000,
            metrics=metrics,
        )
        
        assert state == "SYNCHRONIZED", "Should be SYNCHRONIZED with fresh peer confirmation"
        
        # Case 2: Without fresh peer confirmation - should be UNKNOWN
        metrics["best_remote_height"] = None
        metrics["best_remote_age_sec"] = None
        metrics["behind_by"] = None
        
        state = _compute_sync_state(
            head_height=1000,
            network_height=1000,
            metrics=metrics,
        )
        
        assert state == "UNKNOWN", "Must be UNKNOWN without fresh peer confirmation"

    def test_behind_state_with_fresh_peer_tips(self):
        """BEHIND state when best_remote_height is higher than local."""
        metrics = {
            "best_remote_height": 2000,  # Fresh peer tip ahead
            "best_remote_age_sec": 10.0,
            "behind_by": 1000,  # 1000 blocks behind
            "sync_status_reason": None,
            "best_header_height": 1000,
            "best_block_height": 1000,
            "phase": "IDLE",
            "syncing": False,
            "synchronized": False,
            "target_height": 2000,
        }
        
        state = _compute_sync_state(
            head_height=1000,
            network_height=2000,
            metrics=metrics,
        )
        
        assert state == "BEHIND", "Should be BEHIND when peer is ahead"

    def test_stalled_state_detection(self):
        """STALLED state when phase explicitly says stalled."""
        metrics = {
            "best_remote_height": 2000,
            "best_remote_age_sec": 10.0,
            "behind_by": 1000,
            "sync_status_reason": None,
            "best_header_height": 1000,
            "best_block_height": 1000,
            "phase": "STALLED",  # Explicit stalled phase
            "syncing": False,
            "synchronized": False,
            "target_height": 2000,
        }
        
        state = _compute_sync_state(
            head_height=1000,
            network_height=2000,
            metrics=metrics,
        )
        
        assert state == "STALLED", "Should be STALLED when phase says stalled"

    def test_syncing_headers_state(self):
        """SYNCING_HEADERS state during header sync phase."""
        metrics = {
            "best_remote_height": 2000,
            "best_remote_age_sec": 5.0,
            "behind_by": 1000,
            "sync_status_reason": None,
            "best_header_height": 1500,
            "best_block_height": 1000,
            "phase": "HEADERS",
            "syncing": True,
            "synchronized": False,
            "target_height": 2000,
        }
        
        state = _compute_sync_state(
            head_height=1000,
            network_height=2000,
            metrics=metrics,
        )
        
        assert state == "SYNCING_HEADERS", "Should be SYNCING_HEADERS during header phase"

    def test_syncing_blocks_state(self):
        """SYNCING_BLOCKS state when headers > blocks."""
        metrics = {
            "best_remote_height": 2000,
            "best_remote_age_sec": 5.0,
            "behind_by": 500,
            "sync_status_reason": None,
            "best_header_height": 2000,  # Headers ahead of blocks
            "best_block_height": 1500,
            "phase": "BLOCKS",
            "syncing": True,
            "synchronized": False,
            "target_height": 2000,
        }
        
        state = _compute_sync_state(
            head_height=1500,
            network_height=2000,
            metrics=metrics,
        )
        
        assert state == "SYNCING_BLOCKS", "Should be SYNCING_BLOCKS when headers > blocks"

    def test_near_tip_state(self):
        """NEAR_TIP state when within 10 blocks of best_remote."""
        metrics = {
            "best_remote_height": 1005,  # 5 blocks ahead
            "best_remote_age_sec": 2.0,
            "behind_by": 5,
            "sync_status_reason": None,
            "best_header_height": 1000,
            "best_block_height": 1000,
            "phase": "IDLE",
            "syncing": False,
            "synchronized": False,
            "target_height": 1005,
        }
        
        state = _compute_sync_state(
            head_height=1000,
            network_height=1005,
            metrics=metrics,
            near_tip_blocks=10,
        )
        
        assert state == "NEAR_TIP", "Should be NEAR_TIP when within 10 blocks"

    def test_genesis_behind_state(self):
        """BEHIND state at genesis when peer has blocks."""
        metrics = {
            "best_remote_height": 1000,
            "best_remote_age_sec": 5.0,
            "behind_by": 1000,
            "sync_status_reason": None,
            "best_header_height": 0,
            "best_block_height": 0,
            "phase": "IDLE",
            "syncing": False,
            "synchronized": False,
            "target_height": None,
        }
        
        state = _compute_sync_state(
            head_height=0,
            network_height=1000,
            metrics=metrics,
        )
        
        assert state == "BEHIND", "Should be BEHIND at genesis with peer ahead"

    def test_genesis_idle_state(self):
        """IDLE state at genesis when no peer info."""
        metrics = {
            "best_remote_height": None,  # No peer info
            "best_remote_age_sec": None,
            "behind_by": None,
            "sync_status_reason": "no_fresh_peer_tips",
            "best_header_height": 0,
            "best_block_height": 0,
            "phase": "IDLE",
            "syncing": False,
            "synchronized": False,
            "target_height": None,
        }
        
        state = _compute_sync_state(
            head_height=0,
            network_height=None,
            metrics=metrics,
        )
        
        # At genesis with no peer tips = UNKNOWN (can't determine state)
        assert state == "UNKNOWN", "Should be UNKNOWN at genesis without peer tips"

    def test_synced_phase_without_peer_confirmation_downgraded(self):
        """Phase says SYNCED but no peer confirmation - must downgrade to UNKNOWN."""
        metrics = {
            "best_remote_height": None,  # No fresh peer confirmation
            "best_remote_age_sec": None,
            "behind_by": None,
            "sync_status_reason": "no_fresh_peer_tips",
            "best_header_height": 1000,
            "best_block_height": 1000,
            "phase": "SYNCED",  # Phase claims synced
            "syncing": False,
            "synchronized": True,  # Flag claims synced
            "target_height": 1000,
        }
        
        state = _compute_sync_state(
            head_height=1000,
            network_height=1000,
            metrics=metrics,
        )
        
        assert state == "UNKNOWN", "Must downgrade SYNCED to UNKNOWN without peer confirmation"

    def test_synchronized_flag_without_peer_info_rejected(self):
        """Synchronized flag without peer info must be rejected."""
        metrics = {
            "best_remote_height": None,
            "best_remote_age_sec": None,
            "behind_by": None,
            "sync_status_reason": "no_fresh_peer_tips",
            "best_header_height": 1000,
            "best_block_height": 1000,
            "phase": "IDLE",
            "syncing": False,
            "synchronized": True,  # Claims synced but no peer info
            "target_height": None,
        }
        
        state = _compute_sync_state(
            head_height=1000,
            network_height=None,
            metrics=metrics,
        )
        
        assert state == "UNKNOWN", "Must reject synchronized flag without peer info"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
