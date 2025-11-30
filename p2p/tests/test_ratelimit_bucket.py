import pytest


def test_ratebucket_consumption_and_refill():
    from p2p.peer.ratelimit import RateBucket

    b = RateBucket.fresh(capacity=2.0, fill_rate=1.0, now=0.0)

    assert b.try_consume(1.0, now=0.0) is True
    assert b.tokens == pytest.approx(1.0)
    assert b.try_consume(2.0, now=0.0) is False

    # After ~1s we should have refilled by ~1 token (capped by capacity)
    b.refill(now=1.1)
    assert b.tokens == pytest.approx(2.0)
    assert b.try_consume(1.5, now=1.1) is True
    assert b.tokens == pytest.approx(0.5)


def test_ratebucket_zero_cost_is_free():
    from p2p.peer.ratelimit import RateBucket

    b = RateBucket.fresh(capacity=1.0, fill_rate=1.0, now=5.0)
    assert b.try_consume(0.0, now=5.0) is True
    assert b.tokens == pytest.approx(1.0)
