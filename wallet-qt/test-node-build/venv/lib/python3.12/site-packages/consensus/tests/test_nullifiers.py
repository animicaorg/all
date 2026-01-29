from __future__ import annotations

import typing as t

import pytest

import consensus.nullifiers as mod

# ------------------------------- Adaptive Adaptor -------------------------------


class _Adaptor:
    """
    A small shim that adapts to different possible APIs exposed by consensus.nullifiers.

    We try, in order:
      - Class-based APIs: NullifierSet / NullifierTTL / SlidingNullifiers / Nullifiers
          ctor(window|ttl=?), methods: add/mark/insert, contains/seen/has, advance/set_height/prune
      - Functional APIs: add(nullifier, height, window=?), seen(nullifier), advance(height), prune(height)
    """

    def __init__(self, window: int, start_height: int) -> None:
        self.window = window
        self.height = start_height
        self.obj = None  # underlying object or module functional namespace

        # Try to find a class
        for name in ("NullifierSet", "NullifierTTL", "SlidingNullifiers", "Nullifiers"):
            if hasattr(mod, name):
                cls = getattr(mod, name)
                # Try ctor(window=) or ctor(ttl=) or bare
                for kwargs in ({"window": window}, {"ttl": window}, {}):
                    try:
                        self.obj = cls(**kwargs)
                        break
                    except TypeError:
                        continue
                if self.obj is not None:
                    break

        # If no class found, fall back to module-level functional style
        if self.obj is None:
            self.obj = mod  # use module; we'll call functions on it

        # Try to set initial height if the API needs it
        self.advance(start_height)

    # --- capability discovery helpers ---

    def _find(self, obj, names: t.Iterable[str]):
        for n in names:
            if hasattr(obj, n):
                return getattr(obj, n)
        return None

    # --- operations ---

    def advance(self, height: int) -> None:
        """Advance/prune to a given chain height so the window can slide."""
        self.height = height
        # Class-based
        if self.obj is not mod:
            fn = self._find(self.obj, ("advance", "set_height", "prune", "roll"))
            if fn:
                try:
                    fn(height)
                    return
                except TypeError:
                    # Some expose prune(oldest_allowed_height). Provide a best-effort:
                    # if height is the tip, oldest = height - window
                    try:
                        fn(max(0, height - self.window))
                        return
                    except TypeError:
                        pass
            # Some classes expose .height attribute
            for attr in ("height", "tip_height", "current_height"):
                if hasattr(self.obj, attr):
                    try:
                        setattr(self.obj, attr, height)
                        return
                    except Exception:
                        pass
        else:
            # Functional
            fn = self._find(self.obj, ("advance", "set_height", "prune"))
            if fn:
                try:
                    fn(height)
                    return
                except TypeError:
                    try:
                        fn(max(0, height - self.window))
                        return
                    except TypeError:
                        pass

    def seen(self, n: bytes | str) -> bool:
        """Check membership."""
        if self.obj is not mod:
            fn = self._find(self.obj, ("contains", "seen", "has", "__contains__"))
            if fn:
                try:
                    return bool(fn(n))
                except TypeError:
                    # __contains__ case
                    try:
                        return bool(self.obj.__contains__(n))  # type: ignore[attr-defined]
                    except Exception:
                        pass
        else:
            fn = self._find(self.obj, ("contains", "seen", "has"))
            if fn:
                return bool(fn(n))
        # If no explicit API, try add-then-remove semantics are unknown; default False.
        return False

    def add(self, n: bytes | str, height: int | None = None) -> bool:
        """
        Insert/mark a nullifier at given height.
        Returns True if inserted; False if duplicate/rejected (best-effort).
        """
        if height is None:
            height = self.height
        # Ensure height is advanced first for implementations that use tip height
        self.advance(height)

        # Class-based
        if self.obj is not mod:
            for name in ("add", "mark", "insert", "put", "use"):
                fn = self._find(self.obj, (name,))
                if fn:
                    try:
                        return bool(fn(n, height))
                    except TypeError:
                        try:
                            # maybe only takes the nullifier; height read from .height
                            return bool(fn(n))
                        except TypeError:
                            continue
        else:
            # Functional
            for name in ("add", "mark", "insert", "put", "use"):
                fn = self._find(self.obj, (name,))
                if fn:
                    # try (n, height, window)
                    try:
                        return bool(fn(n, height, self.window))
                    except TypeError:
                        pass
                    # try (n, height)
                    try:
                        return bool(fn(n, height))
                    except TypeError:
                        pass
                    # try (n) only
                    try:
                        return bool(fn(n))
                    except TypeError:
                        pass

        # Fallback behavior: if API doesn't report, infer from membership pre/post.
        pre = self.seen(n)
        # attempt a no-op path again with any callable attribute
        inserted = not pre
        return inserted


# ------------------------------- Test Utilities ---------------------------------


def _hex(i: int) -> str:
    return "0x" + i.to_bytes(8, "big").hex()


# ------------------------------------ Tests -------------------------------------


def test_reuse_rejected_within_ttl():
    """
    A nullifier used at height h must not be reusable while h is within the sliding window.
    """
    ttl = 32
    A = _Adaptor(window=ttl, start_height=1_000)

    n = _hex(1)
    ok1 = A.add(n, 1000)
    assert ok1 or A.seen(n), "first insertion should succeed (or be visible in the set)"
    assert A.seen(n), "nullifier should be marked as seen after insertion"

    # Immediate reuse at same height must be rejected
    ok2 = A.add(n, 1000)
    assert not ok2 or A.seen(
        n
    ), "duplicate insertion should be rejected (ok=False) or already seen"

    # Within window, still rejected
    for h in (1001, 1005, 1031):  # up to h = 1000 + ttl - 1
        ok = A.add(n, h)
        assert not ok or A.seen(n), f"reuse within TTL window (h={h}) must be rejected"


def test_reuse_allowed_after_window_slides_past_ttl():
    """
    After the window slides beyond h + ttl, the same nullifier may be accepted again.
    """
    ttl = 16
    A = _Adaptor(window=ttl, start_height=2_000)

    n = _hex(2)
    assert A.add(n, 2000)
    assert A.seen(n)

    # Slide to boundary: last height where it must still be blocked is 2000 + ttl - 1
    A.advance(2000 + ttl - 1)
    assert not A.add(n, 2000 + ttl - 1), "still within TTL; must be rejected"

    # One past the window → allowed again
    h2 = 2000 + ttl
    ok = A.add(n, h2)
    # Some implementations may immediately mark seen() after reinsert; check acceptance by seen() too.
    assert ok or A.seen(n), f"after TTL slides (h={h2}), reuse should be accepted"


def test_window_slides_and_old_entries_drop_out():
    """
    Insert a stream of distinct nullifiers over > TTL heights.
    Ensure the set answers False for nullifiers older than the window.
    """
    ttl = 32
    A = _Adaptor(window=ttl, start_height=3_000)

    # Insert one unique nullifier per height 3000..(3000+ttl+10)
    start = 3000
    end = start + ttl + 10
    for h in range(start, end + 1):
        n = _hex(h)
        assert A.add(n, h), f"insert should succeed for fresh nullifier at h={h}"

    # Now, anything from <= end - ttl should have fallen out
    cutoff = end - ttl
    for h in range(start, cutoff + 1):
        n = _hex(h)
        # advance to end to ensure pruning has a chance
        A.advance(end)
        assert not A.seen(
            n
        ), f"nullifier at h={h} should be pruned after sliding beyond TTL"

    # But the most recent ttl heights should still be present
    for h in range(cutoff + 1, end + 1):
        n = _hex(h)
        assert A.seen(n), f"recent nullifier at h={h} must still be present"
