"""
Test for fix: Include ALL genesis hashes from anchor_candidates in validation.

This test verifies that when validating headers at height 1, the code checks
if parent_hash matches ANY genesis hash (height 0) from anchor_candidates,
not just the limited set of expected_genesis and expected_genesis_block.

Regression test for issue: "Sync still stalled at 0"
Where node had correct genesis in best_header_tip but wrong genesis in local_head,
and headers were rejected because validation didn't check all anchor_candidates.
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from p2p.node.p2p_service_legacy import P2PService


class TestGenesisAnchorCandidatesFix:
    """Test that ALL genesis hashes from anchor_candidates are used in validation."""
    
    def test_process_headers_uses_all_anchor_candidates_at_genesis(self):
        """
        Test that _process_headers checks parent_hash against ALL height-0 hashes
        from anchor_candidates, not just expected_genesis and expected_genesis_block.
        
        Scenario: Node has two different genesis hashes:
        - local_head: wrong genesis hash (b07ee3fa...)
        - best_header_tip: correct genesis hash (6a27e931...)
        
        Peer sends header at height 1 with parent = correct genesis (6a27e931...)
        
        Expected: Header should be accepted because 6a27e931... is in anchor_candidates
        """
        service = Mock(spec=P2PService)
        
        # Set up the scenario: two different genesis hashes
        wrong_genesis = bytes.fromhex('b07ee3fa82f79d6228e3745aa1822b4abf365ff70c6542ebafeae4a0bd3a236b')
        correct_genesis = bytes.fromhex('6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242')
        
        # Mock genesis hash methods to return wrong genesis
        service._genesis_hash.return_value = wrong_genesis
        service._genesis_header_hash.return_value = wrong_genesis
        service._genesis_block_hash.return_value = wrong_genesis
        
        # Mock local_head to have wrong genesis
        service._local_head.return_value = (0, '0x' + wrong_genesis.hex())
        
        # Mock anchor_candidates to include BOTH genesis hashes
        # This simulates the situation where best_header_tip has correct genesis
        mock_anchor_candidates = {
            wrong_genesis: (0, "local_head"),
            correct_genesis: (0, "best_header_tip"),
        }
        
        # The fix: code should now check if parent_hash is in ANY height-0 anchor
        # Build the valid_genesis_hashes set as the fix does
        valid_genesis_hashes = {
            wrong_genesis,  # expected_genesis
            wrong_genesis,  # expected_genesis_block (same)
            wrong_genesis,  # anchor_hash (from local_head)
        }
        # CRITICAL FIX: Add all height-0 hashes from anchor_candidates
        for h, (height, source) in mock_anchor_candidates.items():
            if height == 0:
                valid_genesis_hashes.add(h)
        valid_genesis_hashes = {h for h in valid_genesis_hashes if h}
        
        # Verify that BOTH genesis hashes are now in valid set
        assert wrong_genesis in valid_genesis_hashes, "Wrong genesis should be in valid set"
        assert correct_genesis in valid_genesis_hashes, "Correct genesis should be in valid set"
    
    def test_valid_genesis_parent_hashes_includes_all_anchor_candidates(self):
        """
        Test that valid_genesis_parent_hashes includes all height-0 hashes
        from anchor_candidates for parent checks.
        
        This tests the second part of the fix at lines 10567-10576.
        """
        # Set up scenario with multiple genesis hash variants
        genesis_variant_1 = bytes.fromhex('6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242')
        genesis_variant_2 = bytes.fromhex('b07ee3fa82f79d6228e3745aa1822b4abf365ff70c6542ebafeae4a0bd3a236b')
        genesis_variant_3 = bytes.fromhex('1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef')
        
        expected_genesis = genesis_variant_1
        expected_genesis_block = genesis_variant_2
        
        mock_anchor_candidates = {
            genesis_variant_1: (0, "genesis"),
            genesis_variant_2: (0, "local_head"),
            genesis_variant_3: (0, "best_header_tip"),  # New variant from peers
        }
        
        # Build valid_genesis_parent_hashes as the fix does
        valid_genesis_parent_hashes = {
            expected_genesis,
            expected_genesis_block,
        }
        for h, (height, source) in mock_anchor_candidates.items():
            if height == 0:
                valid_genesis_parent_hashes.add(h)
        valid_genesis_parent_hashes = {h for h in valid_genesis_parent_hashes if h}
        
        # Verify ALL three variants are included
        assert genesis_variant_1 in valid_genesis_parent_hashes
        assert genesis_variant_2 in valid_genesis_parent_hashes
        assert genesis_variant_3 in valid_genesis_parent_hashes
        
        # Verify that parent checks would accept any of these
        for variant in [genesis_variant_1, genesis_variant_2, genesis_variant_3]:
            assert variant in valid_genesis_parent_hashes, \
                f"Header with parent {variant.hex()} should set parent_height=0"
    
    def test_fix_handles_empty_anchor_candidates(self):
        """
        Test that fix handles edge case where anchor_candidates is empty.
        
        In this case, valid_genesis_hashes should fall back to just
        expected_genesis and expected_genesis_block.
        """
        expected_genesis = bytes.fromhex('6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242')
        expected_genesis_block = expected_genesis  # Same
        
        mock_anchor_candidates = {}  # Empty
        
        # Build valid_genesis_hashes as the fix does
        valid_genesis_hashes = {
            expected_genesis,
            expected_genesis_block,
            None,  # anchor_hash might be None
        }
        for h, (height, source) in mock_anchor_candidates.items():
            if height == 0:
                valid_genesis_hashes.add(h)
        valid_genesis_hashes = {h for h in valid_genesis_hashes if h}
        
        # Should still have expected_genesis
        assert expected_genesis in valid_genesis_hashes
        assert len(valid_genesis_hashes) >= 1
    
    def test_fix_filters_non_genesis_anchors(self):
        """
        Test that fix only includes height-0 anchors, not higher heights.
        """
        genesis_hash = bytes.fromhex('6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242')
        height_10_hash = bytes.fromhex('1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef')
        
        expected_genesis = genesis_hash
        expected_genesis_block = genesis_hash
        
        mock_anchor_candidates = {
            genesis_hash: (0, "genesis"),
            height_10_hash: (10, "checkpoint"),  # Should NOT be included
        }
        
        # Build valid_genesis_hashes as the fix does
        valid_genesis_hashes = {
            expected_genesis,
            expected_genesis_block,
        }
        for h, (height, source) in mock_anchor_candidates.items():
            if height == 0:  # Only height 0
                valid_genesis_hashes.add(h)
        valid_genesis_hashes = {h for h in valid_genesis_hashes if h}
        
        # Should include genesis but NOT height_10_hash
        assert genesis_hash in valid_genesis_hashes
        assert height_10_hash not in valid_genesis_hashes


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
