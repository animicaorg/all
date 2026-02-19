"""
Tests for credit calculation system.
"""

import pytest
from datetime import datetime, timezone, timedelta
from ena.credits import (
    CreditCalculator,
    QualityBonus,
    CreditResult,
    calculate_credits,
    ClaimManager,
)


class TestCreditCalculator:
    """Tests for credit calculator."""
    
    def test_base_credits(self):
        """Test base credit calculation."""
        calc = CreditCalculator()
        
        result = calc.calculate("DATA_CURATION", "worker1")
        assert result.base_credits == 100
        assert result.total_credits == 100  # No bonuses or penalties
    
    def test_verification_bonus(self):
        """Test verification passed bonus."""
        calc = CreditCalculator()
        
        result = calc.calculate(
            "DATA_CURATION",
            "worker1",
            verification_passed=True,
        )
        
        assert result.quality_bonus == 20
        assert result.total_credits == 120  # 100 base + 20 bonus
        assert QualityBonus.VERIFICATION_PASSED.value in result.bonuses_applied
    
    def test_eval_improvement_bonus(self):
        """Test eval improvement bonus."""
        calc = CreditCalculator()
        
        result = calc.calculate(
            "EVAL_RUN",
            "worker1",
            verification_passed=True,
            eval_improvement=0.1,  # 10% improvement
        )
        
        assert result.quality_bonus > 20  # Verification + improvement
        assert "eval_improvement" in result.bonuses_applied[1]
    
    def test_spam_penalty(self):
        """Test spam penalty."""
        calc = CreditCalculator()
        
        result = calc.calculate(
            "DATA_CURATION",
            "worker1",
            is_spam=True,
        )
        
        assert result.penalty == 100
        assert "spam" in result.penalties_applied
        assert result.total_credits == 0  # 100 base - 100 penalty
    
    def test_duplicate_penalty(self):
        """Test duplicate submission penalty."""
        calc = CreditCalculator()
        
        result = calc.calculate(
            "DATA_CURATION",
            "worker1",
            is_duplicate=True,
        )
        
        assert result.penalty == 80
        assert "duplicate" in result.penalties_applied
    
    def test_low_quality_penalty(self):
        """Test low quality penalty."""
        calc = CreditCalculator()
        
        result = calc.calculate(
            "DATA_CURATION",
            "worker1",
            quality_score=0.3,  # Low quality
        )
        
        assert result.penalty >= 30
        assert "low_quality" in result.penalties_applied
    
    def test_diminishing_returns(self):
        """Test diminishing returns per worker per day."""
        calc = CreditCalculator()
        
        # First submissions get full credits
        result1 = calc.calculate("EVAL_RUN", "worker1")
        assert result1.total_credits == 150
        
        # Keep submitting until we hit threshold
        for i in range(10):
            calc.calculate("EVAL_RUN", "worker1")
        
        # Now should have diminishing returns
        result_diminished = calc.calculate("EVAL_RUN", "worker1")
        
        # Should still get some credits, but reduced
        assert result_diminished.total_credits > 0
        assert "diminishing" in result_diminished.reason.lower() or \
               "limit" in result_diminished.reason.lower()
    
    def test_daily_limit(self):
        """Test daily credit limit per worker."""
        calc = CreditCalculator()
        
        # Submit many high-value jobs
        total_earned = 0
        for i in range(20):
            result = calc.calculate("SFT_TRAIN", "worker1")  # 500 base credits
            total_earned += result.total_credits
        
        # Should not exceed daily max (2000)
        assert total_earned <= calc.MAX_CREDITS_PER_DAY
    
    def test_different_days_reset(self):
        """Test that different days reset the limit."""
        calc = CreditCalculator()
        
        # Day 1
        day1 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result1 = calc.calculate("SFT_TRAIN", "worker1", timestamp=day1)
        credits_day1 = result1.total_credits
        
        # Day 2 - should get full credits again
        day2 = datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        result2 = calc.calculate("SFT_TRAIN", "worker1", timestamp=day2)
        credits_day2 = result2.total_credits
        
        assert credits_day1 == credits_day2 == 500  # Both get full base
    
    def test_multiple_workers_independent(self):
        """Test that different workers have independent limits."""
        calc = CreditCalculator()
        
        # Worker 1 submits many jobs
        for i in range(10):
            calc.calculate("SFT_TRAIN", "worker1")
        
        # Worker 2 should still get full credits
        result_w2 = calc.calculate("SFT_TRAIN", "worker2")
        assert result_w2.total_credits == 500  # Full base credits
    
    def test_calculate_credits_function(self):
        """Test convenience function."""
        result = calculate_credits(
            "DATA_CURATION",
            "worker1",
            verification_passed=True,
        )
        
        assert isinstance(result, CreditResult)
        assert result.total_credits > 100  # Has bonus


class TestClaimManager:
    """Tests for claim manager."""
    
    def test_add_credits(self):
        """Test adding credits to balance."""
        manager = ClaimManager()
        
        manager.add_credits("worker1", 100)
        assert manager.get_balance("worker1") == 100
        
        manager.add_credits("worker1", 50)
        assert manager.get_balance("worker1") == 150
    
    def test_full_claim(self):
        """Test full balance claim."""
        manager = ClaimManager()
        manager.add_credits("worker1", 100)
        
        result = manager.claim("worker1")
        
        assert result.success is True
        assert result.amount_claimed == 100
        assert result.remaining_balance == 0
        assert manager.get_balance("worker1") == 0
    
    def test_partial_claim(self):
        """Test partial balance claim."""
        manager = ClaimManager()
        manager.add_credits("worker1", 100)
        
        result = manager.claim("worker1", amount=30)
        
        assert result.success is True
        assert result.amount_claimed == 30
        assert result.remaining_balance == 70
        assert manager.get_balance("worker1") == 70
    
    def test_insufficient_balance(self):
        """Test claim with insufficient balance."""
        manager = ClaimManager()
        manager.add_credits("worker1", 50)
        
        result = manager.claim("worker1", amount=100)
        
        assert result.success is False
        assert result.amount_claimed == 0
        assert manager.get_balance("worker1") == 50  # Unchanged
    
    def test_invalid_amount(self):
        """Test claim with invalid amount."""
        manager = ClaimManager()
        manager.add_credits("worker1", 100)
        
        result = manager.claim("worker1", amount=0)
        assert result.success is False
        
        result = manager.claim("worker1", amount=-10)
        assert result.success is False
    
    def test_multiple_workers(self):
        """Test multiple workers with independent balances."""
        manager = ClaimManager()
        
        manager.add_credits("worker1", 100)
        manager.add_credits("worker2", 200)
        
        result1 = manager.claim("worker1", amount=50)
        assert result1.success is True
        
        # Worker 2's balance should be unchanged
        assert manager.get_balance("worker2") == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
