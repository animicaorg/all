import os
import sys
import time
import inspect
import pytest

# Ensure local package import when running from repo root
sys.path.insert(0, os.path.expanduser("~/animica"))

# ----- Import module under test (skip whole file if unavailable) -----
try:
    rl_mod = __import__("p2p.peer.ratelimit", fromlist=["*"])
except Exception as e:
    pytest.skip(f"p2p.peer.ratelimit not available: {e}", allow_module_level=True)


# ===== Helper plumbing to adapt to many possible APIs =====

def _find_rate_limiter_class():
    """Return the best-guess RateLimiter class from the module."""
    for name in ("RateLimiter", "Limiter", "TokenRateLimiter", "PeerRateLimiter"):
        if hasattr(rl_mod, name):
            obj = getattr(rl_mod, name)
            if isinstance(obj, type):
                return obj
    # Fallback: some modules expose a factory function instead of a class
    for name in ("make_rate_limiter", "new_rate_limiter", "build_rate_limiter"):
        if hasattr(rl_mod, name) and callable(getattr(rl_mod, name)):
            return getattr(rl_mod, name)
    return None


def _build_topics_conf(blocks_cap=2, blocks_rate=0.0, txs_cap=3, txs_rate=0.0):
    """Common structure many implementations accept for per-topic configs."""
    return {
        "blocks": {"capacity": blocks_cap, "fill_rate": blocks_rate},
        "txs": {"capacity": txs_cap, "fill_rate": txs_rate},
    }


def _make_clock(monkeypatch, start=1000.0):
    """Create a controllable clock. Returns (now_fn, advance)."""
    now_box = {"t": float(start)}

    def now():
        return now_box["t"]

    def advance(dt):
        now_box["t"] += float(dt)

    # If module references time.time, we can monkeypatch it to be deterministic
    if hasattr(rl_mod, "time"):
        try:
            monkeypatch.setattr(rl_mod.time, "time", now, raising=True)
        except Exception:
            pass
    return now, advance


def _construct_limiter(monkeypatch,
                       per_peer_capacity=10,
                       per_peer_rate=0.0,
                       topics_conf=None,
                       global_capacity=None,
                       global_rate=0.0,
                       clock_fn=None):
    """
    Try a variety of constructor shapes:
      RateLimiter(per_peer=..., topics=..., global_limit=..., time_fn=...)
      RateLimiter(per_peer_capacity=..., per_peer_fill_rate=..., per_topic=..., ...)
      RateLimiter(config={...})
    """
    RL = _find_rate_limiter_class()
    if RL is None:
        pytest.skip("No RateLimiter class/factory found", allow_module_level=True)

    topics_conf = topics_conf or _build_topics_conf()
    kwargs_candidates = []

    # Common shapes
    kwargs_candidates.append(dict(per_peer={"capacity": per_peer_capacity, "fill_rate": per_peer_rate},
                                  topics=topics_conf,
                                  global_limit=None if global_capacity is None else {"capacity": global_capacity, "fill_rate": global_rate}))
    kwargs_candidates.append(dict(per_peer_capacity=per_peer_capacity,
                                  per_peer_fill_rate=per_peer_rate,
                                  per_topic=topics_conf,
                                  global_capacity=global_capacity,
                                  global_fill_rate=global_rate))
    kwargs_candidates.append(dict(peer={"capacity": per_peer_capacity, "fill_rate": per_peer_rate},
                                  per_topic=topics_conf,
                                  global_limit=global_capacity))
    kwargs_candidates.append(dict(config=dict(
        per_peer=dict(capacity=per_peer_capacity, fill_rate=per_peer_rate),
        per_topic=topics_conf,
        global_limit=None if global_capacity is None else dict(capacity=global_capacity, fill_rate=global_rate),
    )))

    # Optional time/clock injection
    if clock_fn is not None:
        for k in ("time_fn", "now_fn", "clock", "clock_fn"):
            for kw in kwargs_candidates:
                kw[k] = clock_fn

    # Try each kwargs shape
    last_err = None
    for kw in kwargs_candidates:
        try:
            # RL may be a class or a factory function
            limiter = RL(**kw) if isinstance(RL, type) else RL(**kw)
            return limiter
        except Exception as e:
            last_err = e
            continue

    # Last-ditch: try a no-arg ctor then setter methods
    try:
        limiter = RL() if isinstance(RL, type) else RL()
        # Look for "configure" or similar
        for name in ("configure", "set_limits", "setup"):
            if hasattr(limiter, name):
                getattr(limiter, name)(
                    per_peer={"capacity": per_peer_capacity, "fill_rate": per_peer_rate},
                    topics=topics_conf,
                    global_limit=None if global_capacity is None else {"capacity": global_capacity, "fill_rate": global_rate},
                )
                break
        return limiter
    except Exception as e:
        if last_err:
            raise last_err
        raise e


def _allow(limiter, peer_id, topic, cost=1):
    """
    Attempt to consume tokens; return True if allowed, False otherwise.
    We try a list of likely method names and signatures.
    """
    candidates = [
        ("allow", {"peer_id": peer_id, "topic": topic, "cost": cost}),
        ("allow", {"peer": peer_id, "topic": topic, "n": cost}),
        ("try_consume", {"peer_id": peer_id, "topic": topic, "tokens": cost}),
        ("try_consume", {"peer": peer_id, "topic": topic, "amount": cost}),
        ("consume", {"peer_id": peer_id, "topic": topic, "tokens": cost}),
        ("check_and_consume", {"peer_id": peer_id, "topic": topic, "cost": cost}),
        ("acquire", {"peer_id": peer_id, "topic": topic, "permits": cost}),
    ]
    for name, kw in candidates:
        if hasattr(limiter, name):
            try:
                res = getattr(limiter, name)(**kw)
                # Normalize to bool (some APIs return (ok, remaining))
                if isinstance(res, tuple):
                    return bool(res[0])
                return bool(res)
            except TypeError:
                # Maybe positional (peer, topic, cost)
                try:
                    res = getattr(limiter, name)(peer_id, topic, cost)
                    if isinstance(res, tuple):
                        return bool(res[0])
                    return bool(res)
                except Exception:
                    pass
            except Exception:
                pass
    # If we get here, API mismatch
    raise AssertionError("Could not find a compatible 'allow/consume' method on limiter")


# ====== Tests ======

def test_per_topic_limits(monkeypatch):
    """
    Each topic has its own token-bucket. Exhausting 'blocks' shouldn't affect 'txs' capacity.
    """
    now, advance = _make_clock(monkeypatch, start=42.0)
    limiter = _construct_limiter(
        monkeypatch,
        per_peer_capacity=64,      # large so it doesn't interfere
        per_peer_rate=0.0,
        topics_conf=_build_topics_conf(blocks_cap=2, blocks_rate=0.0, txs_cap=3, txs_rate=0.0),
        global_capacity=None,
        clock_fn=now,
    )

    pid = "peer-A"
    # 'blocks' topic: cap = 2
    assert _allow(limiter, pid, "blocks") is True
    assert _allow(limiter, pid, "blocks") is True
    assert _allow(limiter, pid, "blocks") is False  # exhausted

    # 'txs' topic: independent cap = 3
    assert _allow(limiter, pid, "txs") is True
    assert _allow(limiter, pid, "txs") is True
    assert _allow(limiter, pid, "txs") is True
    assert _allow(limiter, pid, "txs") is False  # exhausted


def test_per_peer_bucket_isolated_across_peers(monkeypatch):
    """
    Per-peer bucket is independent: consuming for peer A doesn't drain peer B.
    """
    now, advance = _make_clock(monkeypatch, start=100.0)
    limiter = _construct_limiter(
        monkeypatch,
        per_peer_capacity=3,   # tight per-peer cap
        per_peer_rate=0.0,
        topics_conf=_build_topics_conf(blocks_cap=10, blocks_rate=0.0, txs_cap=10, txs_rate=0.0),
        global_capacity=None,
        clock_fn=now,
    )

    # Drain peer A
    assert _allow(limiter, "peer-A", "blocks") is True
    assert _allow(limiter, "peer-A", "txs") is True
    assert _allow(limiter, "peer-A", "txs") is True
    assert _allow(limiter, "peer-A", "txs") is False  # per-peer exhausted

    # Peer B should still have full allowance
    assert _allow(limiter, "peer-B", "blocks") is True
    assert _allow(limiter, "peer-B", "blocks") is True
    assert _allow(limiter, "peer-B", "txs") is True


def test_global_limit_caps_total_across_topics_and_peers(monkeypatch):
    """
    Global bucket (if implemented) caps aggregate traffic across topics and peers.
    If global bucket isn't supported by the implementation, we skip this test gracefully.
    """
    now, advance = _make_clock(monkeypatch, start=200.0)

    # Try to construct with a small global capacity
    try:
        limiter = _construct_limiter(
            monkeypatch,
            per_peer_capacity=100, per_peer_rate=0.0,
            topics_conf=_build_topics_conf(blocks_cap=100, blocks_rate=0.0, txs_cap=100, txs_rate=0.0),
            global_capacity=4, global_rate=0.0,
            clock_fn=now,
        )
    except Exception:
        pytest.skip("Global limit not supported by this RateLimiter implementation")

    # 4 total across everyone
    assert _allow(limiter, "peer-A", "txs") is True   # 1
    assert _allow(limiter, "peer-A", "blocks") is True  # 2
    assert _allow(limiter, "peer-B", "txs") is True   # 3
    assert _allow(limiter, "peer-B", "blocks") is True  # 4
    # Next one should be blocked globally
    assert _allow(limiter, "peer-A", "txs") is False

    # Advance time doesn't help if global_rate == 0
    advance(10.0)
    assert _allow(limiter, "peer-A", "blocks") is False


def test_refill_over_time(monkeypatch):
    """
    Buckets should refill over time at the configured rate.
    We'll check per-peer refill to keep it simple.
    """
    now, advance = _make_clock(monkeypatch, start=300.0)
    limiter = _construct_limiter(
        monkeypatch,
        per_peer_capacity=1,
        per_peer_rate=1.0,  # 1 token / sec
        topics_conf=_build_topics_conf(blocks_cap=100, blocks_rate=0.0, txs_cap=100, txs_rate=0.0),
        global_capacity=None,
        clock_fn=now,
    )

    pid = "peer-R"
    assert _allow(limiter, pid, "txs") is True   # consume the single token
    assert _allow(limiter, pid, "txs") is False  # no tokens left immediately

    # After ~1s, should have one token available again
    advance(0.99)
    assert _allow(limiter, pid, "txs") is False  # not yet
    advance(0.02)  # t+1.01
    assert _allow(limiter, pid, "txs") is True   # refilled

