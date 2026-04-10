"""
Tests for batch signature verification.
"""

import pytest

from animica.security.batch_verify import (
    VerifyItem,
    VerifyResult,
    verify_batch,
    verify_batch_sequential,
)


class TestBatchVerify:
    """Tests for batch verification."""

    def test_empty_batch(self):
        """Test empty batch returns empty results."""
        results = verify_batch([])
        assert results == []

    def test_single_item_batch(self):
        """Test single item batch (should not use multiprocessing)."""
        # Create a dummy item that will fail (invalid signature)
        item = VerifyItem(
            index=0,
            message=b"test message",
            signature=b"invalid" * 100,  # Wrong size/format
            public_key=b"invalid" * 100,
            alg_id=0x2002,  # ML-DSA-65
        )
        
        results = verify_batch([item])
        assert len(results) == 1
        assert results[0].index == 0
        # Should fail (invalid signature/key)
        assert results[0].valid is False

    def test_sequential_verification(self):
        """Test sequential verification."""
        items = [
            VerifyItem(
                index=i,
                message=f"message{i}".encode(),
                signature=b"sig" * 100,
                public_key=b"pk" * 100,
                alg_id=0x2002,
            )
            for i in range(3)
        ]
        
        results = verify_batch_sequential(items)
        assert len(results) == 3
        # All should have results (though verification will fail with dummy data)
        for i, result in enumerate(results):
            assert result.index == i

    def test_result_ordering(self):
        """Test that results are returned in order."""
        items = [
            VerifyItem(
                index=i,
                message=f"message{i}".encode(),
                signature=b"sig" * 100,
                public_key=b"pk" * 100,
                alg_id=0x2002,
            )
            for i in range(5)
        ]
        
        results = verify_batch(items, workers=2)
        assert len(results) == 5
        # Check ordering is preserved
        for i, result in enumerate(results):
            assert result.index == i

    def test_verify_item_creation(self):
        """Test VerifyItem dataclass."""
        item = VerifyItem(
            index=42,
            message=b"test",
            signature=b"sig",
            public_key=b"pk",
            alg_id=0x2002,
            domain=b"test_domain"
        )
        assert item.index == 42
        assert item.message == b"test"
        assert item.signature == b"sig"
        assert item.public_key == b"pk"
        assert item.alg_id == 0x2002
        assert item.domain == b"test_domain"

    def test_verify_item_default_domain(self):
        """Test VerifyItem with default domain."""
        item = VerifyItem(
            index=0,
            message=b"test",
            signature=b"sig",
            public_key=b"pk",
            alg_id=0x2002,
        )
        assert item.domain == b""

    def test_verify_result_creation(self):
        """Test VerifyResult dataclass."""
        result = VerifyResult(index=0, valid=True)
        assert result.index == 0
        assert result.valid is True
        assert result.error is None
        
        result_err = VerifyResult(index=1, valid=False, error="test error")
        assert result_err.index == 1
        assert result_err.valid is False
        assert result_err.error == "test error"


@pytest.mark.slow
class TestBatchVerifyWithRealKeys:
    """Tests with real PQ keys (slow, requires PQ backend)."""

    def test_batch_verify_with_real_signatures(self):
        """Test batch verification with real PQ signatures."""
        try:
            from animica.pq import sig_keygen, sig_sign
        except ImportError:
            pytest.skip("PQ backend not available")
        
        # Generate a test keypair
        pk, sk = sig_keygen()
        
        # Create messages and sign them
        messages = [f"message{i}".encode() for i in range(3)]
        signatures = [sig_sign(sk, msg) for msg in messages]
        
        # Create verify items
        items = [
            VerifyItem(
                index=i,
                message=messages[i],
                signature=signatures[i],
                public_key=pk,
                alg_id=0x2002,  # ML-DSA-65
            )
            for i in range(3)
        ]
        
        # Verify batch
        results = verify_batch(items)
        
        # All should be valid
        assert len(results) == 3
        for result in results:
            assert result.valid is True
            assert result.error is None

    def test_batch_verify_mixed_valid_invalid(self):
        """Test batch with mix of valid and invalid signatures."""
        try:
            from animica.pq import sig_keygen, sig_sign
        except ImportError:
            pytest.skip("PQ backend not available")
        
        # Generate keypairs
        pk, sk = sig_keygen()
        
        # Create messages
        msg1 = b"valid message"
        msg2 = b"invalid message"
        
        # Sign first message
        sig1 = sig_sign(sk, msg1)
        # Create invalid signature for second
        sig2 = b"invalid" * 500  # Wrong signature
        
        items = [
            VerifyItem(0, msg1, sig1, pk, 0x2002),
            VerifyItem(1, msg2, sig2, pk, 0x2002),
        ]
        
        results = verify_batch(items)
        
        assert len(results) == 2
        assert results[0].valid is True  # Valid signature
        assert results[1].valid is False  # Invalid signature


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
