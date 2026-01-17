"""
Unit tests for PPLNS calculation.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from uuid import uuid4

import pytest

from animica.pool.db import PoolDatabase
from animica.pool.models import Miner
from animica.pool.pplns import PPLNSCalculator, PPLNSWindow


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    db = PoolDatabase(db_path)
    db.connect()
    yield db
    db.close()


def test_pplns_payout_calculation(temp_db):
    """Test payout distribution calculation."""
    # Create test miners
    miner1_id = str(uuid4())
    miner2_id = str(uuid4())
    
    temp_db.execute(
        "INSERT INTO miners (id, payout_address, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
        (miner1_id, "anim1miner1", datetime.utcnow(), datetime.utcnow()),
    )
    temp_db.execute(
        "INSERT INTO miners (id, payout_address, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
        (miner2_id, "anim1miner2", datetime.utcnow(), datetime.utcnow()),
    )
    temp_db.commit()
    
    # Create calculator
    calculator = PPLNSCalculator(temp_db, window_work_multiplier=2)
    
    # Simulate PPLNS window with equal work from two miners
    window = PPLNSWindow(
        start_share_id=1,
        end_share_id=10,
        total_work=10_000_000,
        miner_work={
            "anim1miner1": 6_000_000,  # 60% of work
            "anim1miner2": 4_000_000,  # 40% of work
        },
    )
    
    # Calculate payouts
    block_reward = 50_000_000  # 50 ANM in base units
    pool_fee_percent = 1.0  # 1%
    
    distribution = calculator.calculate_payouts(
        window=window,
        block_reward=block_reward,
        pool_fee_percent=pool_fee_percent,
    )
    
    # Verify pool fee
    expected_fee = int(block_reward * 0.01)  # 500_000
    assert distribution.pool_fee == expected_fee
    
    # Verify distributable amount
    expected_distributable = block_reward - expected_fee  # 49_500_000
    assert distribution.distributable == expected_distributable
    
    # Verify payouts (using integer division for determinism)
    assert distribution.payouts["anim1miner1"] == 29_700_000
    assert distribution.payouts["anim1miner2"] == 19_800_000


def test_pplns_dust_rounding(temp_db):
    """Test deterministic rounding and dust handling."""
    # Create test miners
    miner_ids = [str(uuid4()) for _ in range(3)]
    
    for i, miner_id in enumerate(miner_ids):
        temp_db.execute(
            "INSERT INTO miners (id, payout_address, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
            (miner_id, f"anim1miner{i}", datetime.utcnow(), datetime.utcnow()),
        )
    temp_db.commit()
    
    calculator = PPLNSCalculator(temp_db)
    
    # Create window with work that doesn't divide evenly
    window = PPLNSWindow(
        start_share_id=1,
        end_share_id=10,
        total_work=7_000_000,
        miner_work={
            "anim1miner0": 2_333_333,
            "anim1miner1": 2_333_333,
            "anim1miner2": 2_333_334,
        },
    )
    
    # Calculate payouts
    block_reward = 100_000_000  # 100 ANM
    pool_fee_percent = 2.0  # 2%
    
    distribution = calculator.calculate_payouts(
        window=window,
        block_reward=block_reward,
        pool_fee_percent=pool_fee_percent,
    )
    
    # Verify total distributed + dust equals distributable
    total_paid = sum(distribution.payouts.values())
    assert total_paid + distribution.dust == distribution.distributable
    
    # Verify dust is minimal
    assert distribution.dust < len(distribution.payouts)
    
    # Verify all payouts are positive integers
    for address, amount in distribution.payouts.items():
        assert isinstance(amount, int)
        assert amount > 0
