import importlib
import inspect
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

import pytest

# --- Helpers -----------------------------------------------------------------


def _maybe(mod, names: Iterable[str]) -> Optional[Any]:
    """Return the first attribute found on a module/object from a list of names."""
    for n in names:
        if hasattr(mod, n):
            return getattr(mod, n)
    return None


def _call_with_supported(fn: Callable, **kwargs) -> Any:
    """Call a function with only the kwargs it supports (by signature)."""
    sig = inspect.signature(fn)
    supported = {k: v for k, v in kwargs.items() if k in sig.parameters}
    # If it also takes **kwargs, just pass everything.
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        supported = kwargs
    return fn(**supported)


def _as_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {k: getattr(obj, k) for k in dir(obj) if not k.startswith("_")}


def _tpl_id(tpl: Any) -> Tuple:
    """
    Compute a stable identity tuple for a 'template' object/dict,
    tolerant to different implementations.
    """
    d = _as_dict(tpl)
    # Prefer explicit ids first
    for k in ("template_id", "work_id", "id"):
        if k in d:
            return ("id", d[k])
    # Otherwise derive from salient header-ish fields
    header_like = (
        d.get("header_bytes")
        or d.get("header")
        or d.get("raw")
        or d.get("blob")
        or d.get("header_cbor")
        or d.get("preimage")
    )
    parent = (
        d.get("parent_hash")
        or d.get("prev_hash")
        or d.get("parent")
        or d.get("parentHash")
    )
    height = d.get("height") or d.get("number") or d.get("slot") or d.get("epoch")
    mix = (
        d.get("mix_seed")
        or d.get("mixSeed")
        or d.get("nonce_domain")
        or d.get("nonceDomain")
    )
    return ("derived", header_like, parent, height, mix)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _expired(
    tpl: Any, now_ms: Optional[int] = None, now_height: Optional[int] = None
) -> bool:
    """
    Heuristically determine if a template is expired based on common fields.
    Supports either time-based or height-based expiry.
    """
    d = _as_dict(tpl)
    now_ms = _now_ms() if now_ms is None else now_ms

    # time-based
    for k in ("expires_at_ms", "expiresAtMs", "expires_at", "expiry_ms", "expiryAtMs"):
        if k in d:
            try:
                return now_ms >= int(d[k])
            except Exception:
                pass

    # ttl-based
    created = d.get("created_at_ms") or d.get("createdAtMs")
    ttl = d.get("ttl_ms") or d.get("ttlMs") or d.get("valid_ms")
    if created is not None and ttl is not None:
        try:
            return now_ms >= int(created) + int(ttl)
        except Exception:
            pass

    # height-based
    if now_height is not None:
        for hk in (
            "valid_until_height",
            "expiry_height",
            "expires_at_height",
            "validUntilHeight",
        ):
            if hk in d:
                try:
                    return now_height >= int(d[hk])
                except Exception:
                    pass

    # If no signals, assume not expired.
    return False


# --- Module discovery ---------------------------------------------------------

mt = importlib.import_module("mining.templates")

TemplateManager = _maybe(
    mt, ("TemplateManager", "HeaderTemplateManager", "Templates", "Manager")
)

build_template = _maybe(
    mt,
    (
        "build_template",
        "build_header_template",
        "build",
        "make_template",
        "next_template",
    ),
)

refresh_fn = _maybe(mt, ("refresh_template", "refresh", "maybe_refresh", "rollover"))


# --- Fakes for head/time/mempool ---------------------------------------------


class FakeClock:
    def __init__(self, ms: int):
        self.ms = ms

    def now_ms(self) -> int:
        return self.ms

    # Many code paths just call time.time(); we provide a similar function.
    def time(self) -> float:
        return self.ms / 1000.0


class FakeHeadSource:
    def __init__(self, start_height: int = 100, parent_prefix: str = "aa"):
        self.h = start_height
        self.parent_prefix = parent_prefix

    def current(self) -> Dict[str, Any]:
        parent = ("0x" + self.parent_prefix * 32)[:66]
        return {
            "height": self.h,
            "number": self.h,
            "hash": ("0x" + "11" * 32)[:66],
            "parent_hash": parent,
            "parentHash": parent,
            "timestamp": int(time.time()),
        }

    def bump(self, delta: int = 1):
        self.h += delta


def _instantiate_manager() -> Optional[Any]:
    if TemplateManager is None:
        return None

    # Build a kwargs set and down-select by signature.
    clock = FakeClock(ms=_now_ms())
    head_src = FakeHeadSource()
    kwargs = {
        "head_provider": head_src.current,
        "mempool_provider": (lambda: []),
        "clock": clock,
        "template_ttl_ms": 50,  # very short TTL for the test
        "ttl_ms": 50,
        "ttl_seconds": 0.05,
    }
    try:
        return _call_with_supported(TemplateManager, **kwargs)
    except Exception:
        # Try a minimal init
        try:
            return TemplateManager()
        except Exception:
            return None


def _manager_get_template(mgr: Any) -> Any:
    # Try several method names
    meth = _maybe(mgr, ("get_template", "get", "current", "get_work", "template"))
    if callable(meth):
        return meth()
    # Maybe it's a property
    if isinstance(meth, (dict, object)):
        return meth
    # Try module-level refresh/get functions that might use a singleton
    if refresh_fn:
        try:
            return refresh_fn()
        except Exception:
            pass
    if build_template:
        head = FakeHeadSource().current()
        return _call_with_supported(build_template, head=head)
    pytest.skip("Could not obtain a template from manager or builder")


# --- Tests --------------------------------------------------------------------


def test_rollover_on_head_change():
    """
    Template should change when the head changes (height/parent), ensuring miners
    do not continue working on stale work.
    """
    # Prefer manager path; fall back to direct builder
    mgr = _instantiate_manager()
    if mgr is not None:
        # First template
        t0 = _manager_get_template(mgr)
        id0 = _tpl_id(t0)

        # Advance "head" by nudging the provider (if exposed) or by calling a refresh with hints.
        head_src = None
        for attr in ("head_provider", "head", "head_src", "heads"):
            if hasattr(mgr, attr):
                head_src = getattr(mgr, attr)
        # If we can bump the fake head, do so; else, call possible refresh methods.
        bumped = False
        if isinstance(head_src, FakeHeadSource):
            head_src.bump(1)
            bumped = True

        # Try explicit refresh calls
        for name in ("refresh", "maybe_refresh", "rollover", "update", "tick"):
            if hasattr(mgr, name):
                fn = getattr(mgr, name)
                try:
                    _call_with_supported(fn, force=True)
                except Exception:
                    try:
                        fn()
                    except Exception:
                        pass

        # Second template
        t1 = _manager_get_template(mgr)
        id1 = _tpl_id(t1)

        # In a healthy pipeline, a head change or explicit refresh yields a new template identity.
        assert (
            id1 != id0
        ), f"Template did not change across head rollover (id0={id0}, id1={id1})"

    else:
        # Builder fallback: construct two templates with distinct heads.
        if build_template is None:
            pytest.skip("No TemplateManager and no build_template() found")
        head0 = FakeHeadSource(start_height=100, parent_prefix="aa").current()
        head1 = FakeHeadSource(start_height=101, parent_prefix="bb").current()
        t0 = _call_with_supported(build_template, head=head0, prev=None)
        t1 = _call_with_supported(build_template, head=head1, prev=t0)
        assert _tpl_id(t0) != _tpl_id(
            t1
        ), "Builder produced identical templates for different heads"


def test_work_expiry_handling():
    """
    Work (templates) should expire after a short TTL or when a height threshold is
    crossed; a subsequent retrieval/refresh must return fresh work.
    """
    mgr = _instantiate_manager()
    if mgr is not None:
        # Acquire a template and capture any expiry hints
        t0 = _manager_get_template(mgr)
        id0 = _tpl_id(t0)
        d0 = _as_dict(t0)

        # Determine expiry mode and drive time/height accordingly.
        # Try time-based: if TTL/expires_at present, advance the clock.
        advanced = False
        # Try to find an attached clock
        clock = getattr(mgr, "clock", None)
        if isinstance(clock, FakeClock):
            # If template exposes explicit expiry, jump just beyond it; else add fixed delta.
            exp = d0.get("expires_at_ms") or d0.get("expiry_ms")
            if exp is None:
                start = d0.get("created_at_ms") or _now_ms()
                ttl = d0.get("ttl_ms") or 50
                exp = int(start) + int(ttl)
            clock.ms = int(exp) + 5
            advanced = True

        # If height-based signals are present, bump height via provider if possible
        head_src = None
        for attr in ("head_provider", "head", "head_src", "heads"):
            if hasattr(mgr, attr):
                head_src = getattr(mgr, attr)
        if isinstance(head_src, FakeHeadSource):
            target_h = (
                d0.get("valid_until_height") or d0.get("expiry_height") or head_src.h
            ) + 1
            while head_src.h < int(target_h):
                head_src.bump(1)
            advanced = True

        # Encourage a refresh
        for name in ("refresh", "maybe_refresh", "rollover", "update", "tick"):
            if hasattr(mgr, name):
                fn = getattr(mgr, name)
                try:
                    _call_with_supported(fn, force=True)
                except Exception:
                    try:
                        fn()
                    except Exception:
                        pass

        # Fetch next template; it should be different (expired → new)
        t1 = _manager_get_template(mgr)
        id1 = _tpl_id(t1)

        # If we can evaluate expiry, assert expired; otherwise, just require a change.
        now_ms = getattr(getattr(mgr, "clock", None), "ms", _now_ms())
        expired = _expired(t0, now_ms=now_ms, now_height=getattr(head_src, "h", None))
        if expired:
            assert id1 != id0, "Template expired but manager returned identical work"
        else:
            # Even if we couldn't definitively detect expiry, a second fetch after advancement
            # should produce a different template in a well-behaved implementation.
            assert id1 != id0, "Manager did not rotate work after advancement"

    else:
        # Builder fallback path with explicit TTL fields if supported.
        if build_template is None:
            pytest.skip("No TemplateManager and no build_template() found")
        head = FakeHeadSource(start_height=200, parent_prefix="cc").current()
        # Try to request a short TTL if the builder supports it.
        t0 = _call_with_supported(
            build_template, head=head, ttl_ms=25, ttlSeconds=0.025
        )
        id0 = _tpl_id(t0)

        # Simulate 'expiry' by calling builder again for the same head with a flag that forces rollover
        t1 = _call_with_supported(build_template, head=head, force_new=True, prev=t0)
        id1 = _tpl_id(t1)

        assert (
            id1 != id0
        ), "Builder produced identical work when forced rollover was requested"
