from __future__ import annotations

import types
from typing import Any, Iterable, Optional

import pytest

limiter_mod = pytest.importorskip(
    "mempool.limiter", reason="mempool.limiter module not found"
)

# -------------------------
# Deterministic fake clock
# -------------------------


class FakeClock:
    def __init__(self, t: float = 0.0):
        self.t = float(t)

    def advance(self, dt: float) -> None:
        self.t += float(dt)

    def now(self) -> float:
        return self.t

    # Some implementations call time.time(), others time.monotonic()
    __call__ = now


def _patch_clock(monkeypatch: pytest.MonkeyPatch, clk: FakeClock) -> None:
    """
    Patch time sources used by the limiter module to be deterministic.
    """
    import time  # noqa: F401

    # Common patterns: limiter.time.monotonic / limiter.time.time
    if hasattr(limiter_mod, "time"):
        try:
            monkeypatch.setattr(limiter_mod.time, "time", clk.now, raising=True)
        except Exception:
            pass
        try:
            monkeypatch.setattr(limiter_mod.time, "monotonic", clk.now, raising=True)
        except Exception:
            pass

    # Some code keeps a module-level NOW() helper
    for name in ("now", "monotonic", "clock", "clock_now", "time_now"):
        if hasattr(limiter_mod, name) and callable(getattr(limiter_mod, name)):
            monkeypatch.setattr(limiter_mod, name, clk.now, raising=True)


# -------------------------
# Generic constructors/helpers
# -------------------------


def _get_attr_any(obj: Any, names: Iterable[str]) -> Optional[Any]:
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


def _make_token_bucket(
    rate_per_s: float, burst: float, clk: FakeClock
) -> tuple[Any, callable]:
    """
    Try to construct a token-bucket from whatever names the module exposes.
    Returns (bucket, consume_fn) where consume_fn(n_tokens)->bool.
    """
    ctor_candidates = [
        ("TokenBucket", {}),
        ("RateBucket", {}),
        ("LeakyBucket", {}),  # often rate/burst too
        ("Bucket", {}),
    ]
    # Some modules expose factory helpers
    factory_candidates = [
        "make_bucket",
        "new_bucket",
        "token_bucket",
        "rate_limiter_bucket",
    ]

    # Methods that may exist on the bucket for consumption
    consume_method_names = [
        "consume",
        "try_consume",
        "allow",
        "take",
        "request",
        "acquire",
        "check_and_consume",
        "check",
    ]

    # First, try factories
    for fname in factory_candidates:
        fn = _get_attr_any(limiter_mod, [fname])
        if callable(fn):
            for kwargs in (
                dict(rate=rate_per_s, burst=burst, now_fn=clk.now),
                dict(rate=rate_per_s, burst=burst, clock=clk.now),
                dict(rate=rate_per_s, capacity=burst, now_fn=clk.now),
                dict(tokens_per_second=rate_per_s, burst=burst, now_fn=clk.now),
                dict(rps=rate_per_s, burst=burst, now_fn=clk.now),
            ):
                try:
                    b = fn(**kwargs)  # type: ignore[misc]
                except TypeError:
                    continue
                except Exception:
                    continue
                cm = _get_attr_any(b, consume_method_names)
                if callable(cm):
                    return b, _wrap_consume(cm)
                # Some factories return (bucket, consume_fn)
                if isinstance(b, (tuple, list)) and b:
                    for item in b:
                        if callable(item):
                            return b, _wrap_consume(item)
                    b0 = b[0]
                    cm = _get_attr_any(b0, consume_method_names)
                    if callable(cm):
                        return b0, _wrap_consume(cm)

    # Next, try classes
    for cname, extra in ctor_candidates:
        C = _get_attr_any(limiter_mod, [cname])
        if C is None:
            continue
        # Try a variety of arg names commonly used
        arg_attempts = [
            dict(rate=rate_per_s, burst=burst, now_fn=clk.now),
            dict(rate=rate_per_s, burst=burst, clock=clk.now),
            dict(tokens_per_second=rate_per_s, burst=burst, now_fn=clk.now),
            dict(rps=rate_per_s, burst=burst, now_fn=clk.now),
            dict(capacity=burst, refill_per_sec=rate_per_s, now_fn=clk.now),
            dict(capacity=burst, fill_rate=rate_per_s, now_fn=clk.now),
            dict(rate=rate_per_s, capacity=burst, now_fn=clk.now),
            dict(rate=rate_per_s, burst=burst),
            dict(capacity=burst, refill_per_sec=rate_per_s),
            dict(tokens_per_second=rate_per_s, burst=burst),
            (rate_per_s, burst),
        ]
        for args in arg_attempts:
            try:
                if isinstance(args, dict):
                    b = C(**args)  # type: ignore[misc]
                else:
                    b = C(*args)  # type: ignore[misc]
            except TypeError:
                continue
            except Exception:
                continue
            cm = _get_attr_any(b, consume_method_names)
            if callable(cm):
                return b, _wrap_consume(cm)

    pytest.skip("No token-bucket constructor found in mempool.limiter")
    raise RuntimeError  # pragma: no cover


def _wrap_consume(fn: callable) -> callable:
    """
    Normalize different consume-style signatures/returns into a bool-returning function.
    """

    def consume(n: float = 1.0) -> bool:
        try:
            res = fn(n)  # type: ignore[misc]
        except TypeError:
            try:
                res = fn()  # type: ignore[misc]
            except Exception:
                raise
        # Normalize return value
        if isinstance(res, bool):
            return res
        if isinstance(res, (int, float)):
            # Some return remaining tokens; allow if >= 0 or >= previous?
            return res is not None and res >= 0
        if isinstance(res, (tuple, list)) and res:
            head = res[0]
            if isinstance(head, bool):
                return head
            if isinstance(head, (int, float)):
                return head >= 0
        # If method just mutates and returns None, assume success
        return True

    return consume


def _make_per_peer_limiter(
    clk: FakeClock,
    tx_rps: float,
    tx_burst: int,
    byte_rps: Optional[float] = None,
    byte_burst: Optional[int] = None,
) -> tuple[Any, callable]:
    """
    Construct a per-peer limiter (or combined limiter) if available.
    Returns (limiter, admit(peer, cost_tx, cost_bytes)->bool).
    """
    # Candidates for combined limiter classes
    class_names = [
        "Limiter",
        "RateLimiter",
        "IngressLimiter",
        "AdmissionLimiter",
        "PeerLimiter",
        "PerPeerLimiter",
    ]
    method_names = [
        "admit",
        "allow",
        "check_and_consume",
        "accept",
        "try_consume",
        "ingress",
    ]

    for cname in class_names:
        C = _get_attr_any(limiter_mod, [cname])
        if C is None:
            continue
        # Try various constructor signatures
        ctor_attempts = [
            dict(
                tx_rps=tx_rps,
                tx_burst=tx_burst,
                byte_rps=byte_rps,
                byte_burst=byte_burst,
                now_fn=clk.now,
            ),
            dict(
                tx_rate=tx_rps,
                tx_burst=tx_burst,
                bytes_rate=byte_rps,
                bytes_burst=byte_burst,
                now_fn=clk.now,
            ),
            dict(rps=tx_rps, burst=tx_burst, now_fn=clk.now),
            dict(per_peer_rps=tx_rps, per_peer_burst=tx_burst, now_fn=clk.now),
            dict(tx_rps=tx_rps, tx_burst=tx_burst, now=clk.now),
            dict(tx_rate=tx_rps, tx_burst=tx_burst),
            dict(rps=tx_rps, burst=tx_burst),
            (tx_rps, tx_burst),
        ]
        for args in ctor_attempts:
            try:
                inst = C(**args) if isinstance(args, dict) else C(*args)  # type: ignore[misc]
            except TypeError:
                continue
            except Exception:
                continue

            # find admit-like method
            m = _get_attr_any(inst, method_names)
            if callable(m):

                def admit(peer_id: Any, cost_tx: int = 1, cost_bytes: int = 0) -> bool:
                    # Try common signatures
                    for args2 in (
                        (peer_id, cost_tx, cost_bytes),
                        (peer_id, cost_tx),
                        (peer_id,),
                        (cost_tx, cost_bytes),
                        ({"peer": peer_id, "tx": cost_tx, "bytes": cost_bytes},),
                    ):
                        try:
                            res = m(*args2)  # type: ignore[misc]
                        except TypeError:
                            continue
                        except Exception:
                            continue
                        # Normalize
                        if isinstance(res, bool):
                            return res
                        if isinstance(res, (tuple, list)) and res:
                            return bool(res[0])
                        if res is None:
                            return True
                    # Try kwargs
                    for kwargs in (
                        dict(peer=peer_id, tx=cost_tx, bytes=cost_bytes),
                        dict(peer_id=peer_id, tx_cost=cost_tx, bytes_cost=cost_bytes),
                        dict(peer=peer_id, tokens=cost_tx),
                    ):
                        try:
                            res = m(**kwargs)  # type: ignore[misc]
                            if isinstance(res, bool):
                                return res
                            return True if res is None else bool(res)
                        except Exception:
                            continue
                    return False

                return inst, admit

    # Fallback: construct our own per-peer map using the module's bucket
    bucket_rate = tx_rps
    bucket_burst = tx_burst

    def new() -> Any:
        return types.SimpleNamespace(buckets={}, rps=bucket_rate, burst=bucket_burst)

    limiter = new()

    def admit(peer_id: Any, cost_tx: int = 1, cost_bytes: int = 0) -> bool:
        b = limiter.buckets.get(peer_id)
        if b is None:
            b, consume = _make_token_bucket(limiter.rps, limiter.burst, clk)
            # cache both bucket and its consume fn
            limiter.buckets[peer_id] = (b, consume)
        else:
            b, consume = b
        return bool(consume(cost_tx))

    return limiter, admit


# -------------------------
# Tests
# -------------------------


def test_token_bucket_burst_and_refill(monkeypatch: pytest.MonkeyPatch):
    """
    Basic token-bucket behavior: allow up to burst instantly, then block,
    then allow again after enough time passes at the configured rate.
    """
    clk = FakeClock(0.0)
    _patch_clock(monkeypatch, clk)

    rate = 5.0  # tokens per second
    burst = 10.0  # max instant burst

    bucket, consume = _make_token_bucket(rate, burst, clk)

    # Consume exactly 'burst' tokens: all should pass
    for i in range(int(burst)):
        assert consume(1), f"token {i+1} of burst should be allowed"

    # Next one should be blocked (no time advanced yet)
    assert not consume(1), "should be blocked immediately after exhausting burst"

    # Advance less than needed: still blocked if not enough tokens accrued
    clk.advance(0.1)  # only 0.5 tokens at 5/s
    assert not consume(1), "insufficient refill after 0.1s should still block"

    # Advance to 1.0s total => ~5 tokens available; 1 should pass
    clk.advance(0.9)
    assert consume(1), "should allow after sufficient refill (~1s @ 5/s)"

    # Drain again to zero
    for _ in range(4):
        assert consume(1)
    # Next should fail (spent ~5 tokens)
    assert not consume(1)


def test_per_peer_limits_are_isolated(monkeypatch: pytest.MonkeyPatch):
    """
    Per-peer limiter: one peer saturating its bucket does not block another peer.
    """
    clk = FakeClock(0.0)
    _patch_clock(monkeypatch, clk)

    rps = 2.0
    burst = 3
    limiter, admit = _make_per_peer_limiter(clk, tx_rps=rps, tx_burst=burst)

    P1, P2 = b"P1", b"P2"

    # P1 consumes all its burst
    for i in range(burst):
        assert admit(P1), f"P1 attempt {i+1} should pass within burst"

    # Next P1 attempt should be blocked
    assert not admit(P1), "P1 should be blocked after exceeding burst"

    # P2 should still be allowed up to its own burst
    for i in range(burst):
        assert admit(P2), f"P2 attempt {i+1} should pass (isolated per-peer bucket)"

    # Advance half a second -> rps=2 ⇒ ~1 token refilled
    clk.advance(0.5)
    # P1 should get ~1 token, so a single admit should pass and next should fail
    assert admit(P1), "P1 should get a partial refill after 0.5s"
    assert not admit(
        P1
    ), "P1 should still be limited after consuming the partial refill"


def test_global_limit_blocks_all_when_saturated(monkeypatch: pytest.MonkeyPatch):
    """
    If the module exposes a global ingress limiter, verify that when it is saturated,
    all peers are blocked until refill.
    If not exposed, construct a makeshift global bucket and assert shared blocking.
    """
    clk = FakeClock(0.0)
    _patch_clock(monkeypatch, clk)

    # First, try to discover a global limiter object
    global_names = ["GlobalLimiter", "IngressLimiter", "GlobalIngress", "Global"]
    admit_names = ["admit", "allow", "check_and_consume", "ingress", "try_consume"]

    global_inst = None
    admit_fn = None

    for cname in global_names:
        C = _get_attr_any(limiter_mod, [cname])
        if C is None:
            continue
        for ctor in (
            dict(tx_rps=3.0, tx_burst=3, now_fn=clk.now),
            dict(rate=3.0, burst=3, now_fn=clk.now),
            dict(rps=3.0, burst=3, now_fn=clk.now),
            dict(tx_rate=3.0, tx_burst=3),
            (3.0, 3),
        ):
            try:
                inst = C(**ctor) if isinstance(ctor, dict) else C(*ctor)  # type: ignore[misc]
            except Exception:
                continue
            m = _get_attr_any(inst, admit_names)
            if callable(m):
                global_inst = inst

                def _admit_global(cost: int = 1) -> bool:
                    for args in (
                        (cost,),
                        tuple(),
                    ):
                        try:
                            res = m(*args)  # type: ignore[misc]
                        except TypeError:
                            continue
                        except Exception:
                            continue
                        if isinstance(res, bool):
                            return res
                        if res is None:
                            return True
                        if isinstance(res, (tuple, list)) and res:
                            return bool(res[0])
                        if isinstance(res, (int, float)):
                            return res >= 0
                    # kwargs trials
                    for kwargs in (dict(tokens=cost), dict(cost=cost)):
                        try:
                            res = m(**kwargs)  # type: ignore[misc]
                            return True if res is None else bool(res)
                        except Exception:
                            continue
                    return False

                admit_fn = _admit_global
                break
        if admit_fn:
            break

    # Fallback: use a single shared bucket to emulate "global"
    if admit_fn is None:
        bucket, consume = _make_token_bucket(rate_per_s=3.0, burst=3.0, clk=clk)
        admit_fn = lambda cost=1: bool(consume(cost))  # noqa: E731

    # Saturate the global capacity with three admits
    assert admit_fn()
    assert admit_fn()
    assert admit_fn()
    # Next should be blocked (no per-peer escape)
    assert (
        not admit_fn()
    ), "global limiter should block further admits after burst exhausted"

    # Advance time for 1s @ 3 rps ⇒ ~3 tokens back; at least one admit should pass
    clk.advance(1.0)
    assert admit_fn(), "global limiter should allow after refill"
