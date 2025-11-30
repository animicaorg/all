from __future__ import annotations

import dataclasses as dc
import typing as t

import pytest

import consensus.fork_choice as fc

# -------- tolerant pick/extract helpers (handle naming & signature drift) --------


def _pick(mod, name: str, alts: list[str]):
    if hasattr(mod, name):
        return getattr(mod, name)
    for a in alts:
        if hasattr(mod, a):
            return getattr(mod, a)
    raise AttributeError(f"Missing callable {name} (tried {alts}) in {mod.__name__}")


# Try a functional entrypoint first
_CHOOSE_FUNC = None
try:
    _CHOOSE_FUNC = _pick(
        fc,
        "choose_head",
        ["select_head", "pick_best", "choose", "pick_head", "fork_choice"],
    )
except AttributeError:
    _CHOOSE_FUNC = None

# Otherwise try a small class API
_FC_CLASS = None
for cls_name in ("ForkChoice", "ForkSelector", "Selector"):
    if hasattr(fc, cls_name):
        _FC_CLASS = getattr(fc, cls_name)
        break


def _extract_head(ret):
    """
    Accept:
      - header object
      - (header, reason) tuple
      - dict with 'head'/'best'/'header'
      - object with .head / .best / .header
    """
    if isinstance(ret, tuple) and ret:
        return ret[0]
    if isinstance(ret, dict):
        for k in ("head", "best", "header"):
            if k in ret:
                return ret[k]
    for k in ("head", "best", "header"):
        if hasattr(ret, k):
            return getattr(ret, k)
    return ret  # assume it's already the header


def _call_choose(prev, candidates, **kwargs):
    """
    Try common signatures:
      1) fn(prev, candidates, **opts)
      2) fn(candidates, prev, **opts)
      3) fn(prev=..., candidates=..., **opts)
      4) class(...).choose(prev, candidates) or .select(...)
    """
    # functional
    if _CHOOSE_FUNC is not None:
        for attempt in (
            lambda: _CHOOSE_FUNC(prev, candidates, **kwargs),
            lambda: _CHOOSE_FUNC(candidates, prev, **kwargs),
            lambda: _CHOOSE_FUNC(prev=prev, candidates=candidates, **kwargs),
            lambda: _CHOOSE_FUNC(candidates=candidates, prev=prev, **kwargs),
        ):
            try:
                return _extract_head(attempt())
            except TypeError:
                pass

    # class based
    if _FC_CLASS is not None:
        try:
            inst = _FC_CLASS(**kwargs)
        except TypeError:
            inst = _FC_CLASS()  # fallback

        for meth_name in ("choose", "select", "pick", "pick_best", "choose_head"):
            if hasattr(inst, meth_name):
                meth = getattr(inst, meth_name)
                for attempt in (
                    lambda: meth(prev, candidates),
                    lambda: meth(candidates, prev),
                    lambda: meth(prev=prev, candidates=candidates),
                    lambda: meth(candidates=candidates, prev=prev),
                ):
                    try:
                        return _extract_head(attempt())
                    except TypeError:
                        pass

    raise RuntimeError(
        "Could not call fork choice: no compatible entrypoint/signature found"
    )


# --------------------------------- header stub ----------------------------------


@dc.dataclass(frozen=True)
class Hdr:
    height: int
    weight: float
    hash: str  # hex string (0x...)
    # Optional hints some implementations may read:
    parent_hash: t.Optional[str] = None
    # If an implementation surfaces reorg-depth logic that consults fork point height:
    fork_point_height: t.Optional[int] = None

    # common aliases some libs use
    @property
    def number(self) -> int:
        return self.height

    @property
    def total_weight(self) -> float:
        return self.weight

    def __repr__(self) -> str:
        return f"Hdr(h={self.height}, w={self.weight:.3f}, {self.hash[:10]}…)"


# ------------------------------------ tests -------------------------------------


def test_longest_chain_wins_basic():
    """Higher height should win when weight difference is not decisive."""
    prev = Hdr(100, 100.0, "0xprev")
    a = Hdr(101, 101.0, "0xaaa1")
    b = Hdr(100, 200.0, "0xbbb1")  # shorter, even if heavier
    head = _call_choose(prev, [a, b])
    assert head.height == 101, f"expected longest (height 101) to win, got {head}"


def test_weight_tie_breaker_when_heights_equal():
    """When heights are equal, heavier chain should win."""
    prev = Hdr(200, 1000.0, "0xprev")
    a = Hdr(210, 500.0, "0xaaa2")
    b = Hdr(210, 700.0, "0xbbb2")  # heavier at same height
    head = _call_choose(prev, [a, b])
    assert head == b, f"expected heavier @ equal height to win; got {head}"


def test_deterministic_tie_on_equal_height_and_weight():
    """
    If height and weight are both equal, tie-break must be deterministic
    (usually lexicographic hash or parent/hash mix). Ensure stable pick.
    """
    prev = Hdr(300, 1_000.0, "0xprev")
    a = Hdr(333, 1234.0, "0x0a0a0a")  # equal
    b = Hdr(333, 1234.0, "0x0b0b0b")  # equal

    # run the chooser multiple times; must pick the same one
    picks = {_call_choose(prev, [a, b]) for _ in range(10)}
    assert len(picks) == 1, f"tie-breaker must be deterministic; saw picks={picks}"
    pick = picks.pop()
    assert pick in (a, b), "pick must be one of the tied candidates"
    # sanity: if implementation uses lexicographic min/max, both are acceptable as long as stable


def _supports_reorg_limits() -> bool:
    names = set(dir(fc))
    # Heuristic: any symbol containing 'reorg' or a visible constant/param?
    return any("reorg" in n.lower() for n in names)


@pytest.mark.skipif(
    not _supports_reorg_limits(), reason="fork_choice reorg-depth controls not exposed"
)
def test_reorg_depth_limit_if_supported():
    """
    If implementation exposes a reorg-depth limit, prefer a slightly shorter
    extension over a deep reorg beyond the configured bound.
    """
    # Previous canonical head at height 100
    prev = Hdr(100, 100.0, "0xprev")

    # Candidate that extends the current tip by +1 (no reorg)
    extend = Hdr(101, 150.0, "0xext", parent_hash=prev.hash, fork_point_height=100)

    # Candidate that would require a deep reorg from a fork point at height 80 (depth=20)
    deep_reorg = Hdr(105, 500.0, "0xreorg", parent_hash="0xother", fork_point_height=80)

    # Use a strict bound (e.g., 10) so depth=20 is forbidden
    head = _call_choose(prev, [extend, deep_reorg], max_reorg_depth=10)
    assert (
        head == extend
    ), f"deep reorg beyond limit should be rejected; expected extend, got {head}"

    # With a relaxed bound (e.g., 40), the deeper but taller/weightier candidate may now win
    head2 = _call_choose(prev, [extend, deep_reorg], max_reorg_depth=40)
    assert (
        head2 == deep_reorg or head2.height == 105
    ), "when allowed, higher candidate should be eligible"


def test_idempotence_and_order_independence():
    """
    Fork choice should not depend on the input order of candidates.
    """
    prev = Hdr(500, 5_000.0, "0xprev")
    cands = [
        Hdr(510, 10_000.0, "0xc1"),
        Hdr(509, 20_000.0, "0xc2"),
        Hdr(508, 9_999.0, "0xc3"),
    ]
    pick1 = _call_choose(prev, cands)
    pick2 = _call_choose(prev, list(reversed(cands)))
    pick3 = _call_choose(prev, sorted(cands, key=lambda h: (h.height, h.weight)))
    assert (
        pick1 == pick2 == pick3
    ), f"order independence violated: {pick1}, {pick2}, {pick3}"
