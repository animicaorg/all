"""
DA P2P gossip — message relay & (if supported) de-duplication.

This test is intentionally adapter-agnostic. It will:
  1) Build an in-memory gossip bus from da.adapters.p2p_gossip (if available)
  2) Subscribe a "node B" to the DA commitment topic
  3) Publish a commitment message from "node A" and assert delivery to B
  4) Re-publish the same message and assert de-duplication if the bus exposes
     any dedupe capability; otherwise mark the dedupe sub-test as xfail.

We do not require a concrete message schema; the adapter should treat an
opaque dict with a hex "commitment" (or "root") as a valid payload for the
topic. Topic names are resolved from da.adapters.p2p_topics with several
common fallbacks.
"""

import importlib
import inspect
import time
from typing import Any, Callable, Optional

import pytest


def _import(name: str):
    return importlib.import_module(name)


def _resolve_topic(topics_mod) -> str:
    # Common candidates for the DA commitment gossip topic
    candidates = [
        "DA_COMMITMENTS",
        "DA_COMMITMENT",
        "TOPIC_DA_COMMITMENTS",
        "TOPIC_DA_COMMITMENT",
        "DA_COMMIT_TOPIC",
        "TOPIC_COMMITMENTS",
        "topic_commitments",
        "DA_COMMIT",  # last resort
    ]
    for c in candidates:
        if hasattr(topics_mod, c):
            return getattr(topics_mod, c)
    # Fallback to a sane string if nothing is exported
    return "da/commitments"


def _get_bus_and_api():
    gossip_mod = pytest.importorskip("da.adapters.p2p_gossip")
    # Try to locate a bus class or factory
    ctor: Optional[Callable[[], Any]] = None
    for name in ("InMemoryGossip", "GossipBus", "Bus", "InMemoryBus"):
        if hasattr(gossip_mod, name):
            C = getattr(gossip_mod, name)
            ctor = lambda: C()  # noqa: E731
            break
    if ctor is None:
        for fn in ("create_in_memory_bus", "make_bus", "new_bus", "get_bus"):
            if hasattr(gossip_mod, fn):
                ctor = getattr(gossip_mod, fn)
                break
    if ctor is None:
        pytest.skip("No in-memory gossip bus available in da.adapters.p2p_gossip")

    bus = ctor()

    # Resolve publish/subscribe API names
    pub = None
    sub = None
    for name in ("publish", "pub", "emit", "send"):
        if hasattr(bus, name):
            pub = getattr(bus, name)
            break
    for name in ("subscribe", "sub", "on"):
        if hasattr(bus, name):
            sub = getattr(bus, name)
            break
    if not pub or not sub:
        pytest.skip("Gossip bus does not expose publish/subscribe methods")

    # Heuristics for dedupe capability
    supports_dedupe = False
    for attr in (
        "supports_dedupe",
        "dedupe",
        "seen",
        "seen_ids",
        "recent_ids",
        "max_seen",
    ):
        if hasattr(bus, attr):
            supports_dedupe = True
            break

    return bus, pub, sub, supports_dedupe


def _call_maybe_await(fn: Callable, *args, **kwargs):
    res = fn(*args, **kwargs)
    if inspect.isawaitable(res):
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(res)
        finally:
            loop.close()
    return res


def _wait_until(cond: Callable[[], bool], timeout_s: float = 1.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def _make_msg(
    commitment_hex: str, ns: int = 24, size: int = 4096, msg_id: Optional[str] = None
) -> dict:
    msg = {
        "commitment": commitment_hex,
        "namespace": ns,
        "size": size,
    }
    # Provide several id-style fields to maximize adapter compatibility
    if msg_id:
        msg["id"] = msg_id
        msg["msg_id"] = msg_id
        msg["mid"] = msg_id
    return msg


def test_da_gossip_relay_and_dedupe():
    topics_mod = pytest.importorskip("da.adapters.p2p_topics")
    topic = _resolve_topic(topics_mod)

    bus, publish, subscribe, supports_dedupe = _get_bus_and_api()

    # Node B subscription (capture received messages)
    received: list[dict] = []

    def on_msg(*args, **kwargs):
        # Accept (topic, msg) or (msg) or kwargs forms
        if args and isinstance(args[0], dict):
            received.append(args[0])
        elif len(args) >= 2 and isinstance(args[1], dict):
            received.append(args[1])
        elif "message" in kwargs and isinstance(kwargs["message"], dict):
            received.append(kwargs["message"])
        elif "msg" in kwargs and isinstance(kwargs["msg"], dict):
            received.append(kwargs["msg"])
        else:
            # Best effort: append raw args for debugging
            received.append({"_raw": args, "_kw": kwargs})

    _call_maybe_await(subscribe, topic, on_msg)

    # Node A publishes a commitment
    commitment = "0x" + "ab" * 32
    msg_id = "da-" + "cd" * 16
    payload = _make_msg(commitment, ns=24, size=4096, msg_id=msg_id)

    _call_maybe_await(publish, topic, payload)
    assert _wait_until(lambda: len(received) >= 1), "First publish should be delivered"
    assert received[0].get("commitment", commitment) == commitment

    # Publish the exact same payload again (simulate duplicate relay)
    _call_maybe_await(publish, topic, payload)

    # If bus supports dedupe, ensure we still have exactly one
    if supports_dedupe:
        time.sleep(0.05)  # allow any async delivery to settle
        assert (
            len(received) == 1
        ), f"Expected dedupe to drop duplicate, got {len(received)} deliveries"
    else:
        # If the adapter doesn't expose a dedupe hint, allow either behavior but xfail if duplicate not dropped
        if _wait_until(lambda: len(received) > 1, timeout_s=0.1):
            pytest.xfail(
                "Gossip adapter did not advertise dedupe capability; duplicate was delivered"
            )

    # Publishing a *different* msg_id should always deliver another event
    payload2 = _make_msg(commitment, ns=24, size=4096, msg_id="da-" + "ef" * 16)
    _call_maybe_await(publish, topic, payload2)
    assert _wait_until(
        lambda: len(received) >= 2
    ), "Second, distinct message should be delivered"
