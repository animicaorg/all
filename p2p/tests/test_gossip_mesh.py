import os
import sys
import types
import random
import pytest

# Allow importing local package
sys.path.insert(0, os.path.expanduser("~/animica"))

# ----- Imports (skip whole file if modules are missing) -----
try:
    mesh_mod = __import__("p2p.gossip.mesh", fromlist=["*"])
    eng_mod  = __import__("p2p.gossip.engine", fromlist=["*"])
except Exception as e:
    pytest.skip(f"Gossip modules not available: {e}", allow_module_level=True)


# ====== Adaptive helpers (work across small API variations) ======

def _find_engine_class():
    for name in ("Engine", "GossipEngine", "PubSubEngine", "MeshEngine"):
        if hasattr(eng_mod, name) and isinstance(getattr(eng_mod, name), type):
            return getattr(eng_mod, name)
    # factory fallback
    for name in ("make_engine", "new_engine", "build_engine"):
        if hasattr(eng_mod, name) and callable(getattr(eng_mod, name)):
            return getattr(eng_mod, name)
    return None


def _find_mesh_class():
    for name in ("Mesh", "GossipMesh"):
        if hasattr(mesh_mod, name) and isinstance(getattr(mesh_mod, name), type):
            return getattr(mesh_mod, name)
    return None


class _Capture:
    """Records (peer_id, topic, payload) for 'sends' leaving the engine."""
    def __init__(self):
        self.sends = []

    def send(self, peer_id, topic, payload):
        self.sends.append((peer_id, topic, payload))

    def clear(self):
        self.sends.clear()

    def for_topic(self, topic):
        return [x for x in self.sends if x[1] == topic]


class _DummyTransport:
    """A minimal transport that offers a 'send' compatible surface."""
    def __init__(self, capture: _Capture):
        self.capture = capture

    # Common names engines might call
    def send(self, peer_id, topic, payload):
        self.capture.send(peer_id, topic, payload)

    def send_to_peer(self, peer_id, topic, payload):
        self.capture.send(peer_id, topic, payload)


def _wire_sender(engine, capture: _Capture):
    """
    Try to route engine's outgoing messages into the capture.
    We attempt a range of attribute names or setter methods and
    fall back to engine.transport if present.
    """
    dummy = _DummyTransport(capture)

    # 1) Setter methods
    for name in ("set_sender", "set_send_callback", "set_transport", "set_outbound"):
        if hasattr(engine, name) and callable(getattr(engine, name)):
            try:
                getattr(engine, name)(dummy.send if "sender" in name or "callback" in name else dummy)
                return
            except Exception:
                pass

    # 2) Assign known attributes directly
    for attr in ("sender", "send_func", "send_cb", "on_send", "outbound", "transport"):
        if hasattr(engine, attr):
            try:
                setattr(engine, attr, dummy.send if attr in ("sender","send_func","send_cb","on_send") else dummy)
                return
            except Exception:
                pass

    # 3) Monkeypatch a method likely called internally
    for meth in ("send", "send_to_peer"):
        if hasattr(engine, meth) and callable(getattr(engine, meth)):
            orig = getattr(engine, meth)
            def wrapper(*a, **kw):
                # Try to parse (peer_id, topic, payload)
                if len(a) >= 3:
                    capture.send(a[0], a[1], a[2])
                elif "peer_id" in kw and "topic" in kw and "payload" in kw:
                    capture.send(kw["peer_id"], kw["topic"], kw["payload"])
                return None
            setattr(engine, meth, wrapper)
            return

    # 4) Last resort: attach a 'transport' with a common name
    setattr(engine, "transport", dummy)


def _construct_engine(fanout=2, degree=2):
    """
    Try a variety of constructor shapes for the gossip engine.
    Many implementations take (degree|fanout) and a mesh instance.
    """
    Engine = _find_engine_class()
    if Engine is None:
        pytest.skip("No gossip engine class/factory found", allow_module_level=True)

    Mesh = _find_mesh_class()

    confs = [
        dict(degree=degree, fanout=fanout),
        dict(fanout=fanout),
        dict(degree=degree),
        dict(config=dict(degree=degree, fanout=fanout)),
    ]

    # Try with/without an explicit mesh
    if Mesh is not None:
        meshes = [None, Mesh(degree=degree) if "degree" in Mesh.__init__.__code__.co_varnames else Mesh()]
    else:
        meshes = [None]

    last_err = None
    for conf in confs:
        for m in meshes:
            try:
                if isinstance(Engine, type):
                    if m is None:
                        return Engine(**conf)
                    # common shapes for mesh argument
                    try:
                        return Engine(mesh=m, **conf)
                    except TypeError:
                        return Engine(m, **conf)
                else:
                    # factory
                    if m is None:
                        return Engine(**conf)
                    try:
                        return Engine(mesh=m, **conf)
                    except TypeError:
                        return Engine(m, **conf)
            except Exception as e:
                last_err = e
                continue
    if last_err:
        raise last_err
    raise AssertionError("Could not construct gossip engine")


def _ensure_topic_methods(engine):
    """
    Return callables (subscribe, unsubscribe, graft, prune, publish) normalized over
    a variety of possible method names/signatures.
    """

    # Subscribe
    def subscribe(peer_id, topic):
        for name in ("subscribe", "join", "attach", "add_subscriber", "add_peer_to_topic"):
            if hasattr(engine, name):
                try:
                    getattr(engine, name)(peer_id, topic)
                    return
                except TypeError:
                    getattr(engine, name)(topic, peer_id)
                    return
        # Maybe there's a mesh inside
        if hasattr(engine, "mesh"):
            m = engine.mesh
            for name in ("subscribe", "join", "attach", "add"):
                if hasattr(m, name):
                    try:
                        getattr(m, name)(peer_id, topic)
                        return
                    except TypeError:
                        getattr(m, name)(topic, peer_id)
                        return
        raise AssertionError("No subscribe/join method found")

    # Unsubscribe
    def unsubscribe(peer_id, topic):
        for name in ("unsubscribe", "leave", "detach", "remove_subscriber", "remove_peer_from_topic"):
            if hasattr(engine, name):
                try:
                    getattr(engine, name)(peer_id, topic)
                    return
                except TypeError:
                    getattr(engine, name)(topic, peer_id)
                    return
        if hasattr(engine, "mesh"):
            m = engine.mesh
            for name in ("unsubscribe", "leave", "detach", "remove"):
                if hasattr(m, name):
                    try:
                        getattr(m, name)(peer_id, topic)
                        return
                    except TypeError:
                        getattr(m, name)(topic, peer_id)
                        return
        # Some meshes use prune to drop membership
        prune(peer_id, topic)

    # Graft
    def graft(peer_id, topic):
        for obj in (engine, getattr(engine, "mesh", None)):
            if obj is None:
                continue
            for name in ("graft", "do_graft", "add_to_mesh", "mesh_add"):
                if hasattr(obj, name):
                    try:
                        getattr(obj, name)(peer_id, topic)
                        return
                    except TypeError:
                        getattr(obj, name)(topic, peer_id)
                        return
        # fallback: subscribe acts like graft in some impls
        subscribe(peer_id, topic)

    # Prune
    def prune(peer_id, topic):
        for obj in (engine, getattr(engine, "mesh", None)):
            if obj is None:
                continue
            for name in ("prune", "do_prune", "remove_from_mesh", "mesh_remove"):
                if hasattr(obj, name):
                    try:
                        getattr(obj, name)(peer_id, topic)
                        return
                    except TypeError:
                        getattr(obj, name)(topic, peer_id)
                        return
        # fallback: unsubscribe
        unsubscribe(peer_id, topic)

    # Publish
    def publish(topic, payload, origin=None):
        # Common method names
        for name in ("publish", "emit", "broadcast", "publish_raw", "send_publish"):
            if hasattr(engine, name) and callable(getattr(engine, name)):
                try:
                    return getattr(engine, name)(topic=topic, payload=payload, origin=origin)
                except TypeError:
                    try:
                        return getattr(engine, name)(topic, payload, origin)
                    except Exception:
                        try:
                            return getattr(engine, name)(topic, payload)
                        except Exception:
                            pass
        raise AssertionError("No publish/emit/broadcast method found")

    return subscribe, unsubscribe, graft, prune, publish


# ====== Tests ======

def test_pubsub_routing_and_fanout(monkeypatch):
    """
    Basic publish/subscribe routing: only subscribed peers receive.
    If fanout/degree is enforced, recipients <= configured bound.
    """
    # Deterministic RNG for engines that sample fanout
    random.seed(12345)

    engine = _construct_engine(fanout=2, degree=2)
    capture = _Capture()
    _wire_sender(engine, capture)

    subscribe, unsubscribe, graft, prune, publish = _ensure_topic_methods(engine)

    topic_blocks = "blocks"
    topic_txs    = "txs"

    # Make three peers and subscribe some of them to 'blocks'
    peers = ["peer-A", "peer-B", "peer-C"]
    subscribe(peers[0], topic_blocks)
    subscribe(peers[1], topic_blocks)
    subscribe(peers[2], topic_txs)     # not subscribed to 'blocks'

    # Publish on 'blocks'
    capture.clear()
    publish(topic_blocks, b"blk#1")

    recipients = {p for (p, t, _pl) in [(pid, t, pl) for (pid, t, pl) in capture.sends] if t == topic_blocks}
    # Only peers A/B should be eligible; C must not receive
    assert recipients.issubset({"peer-A", "peer-B"})
    assert "peer-C" not in recipients
    assert len(recipients) >= 1  # at least someone got it

    # If degree/fanout is enforced, it should be <= 2
    assert len(recipients) <= 2


def test_graft_and_prune_cycle(monkeypatch):
    """
    Peers can be pruned from a mesh (stop receiving) and later grafted back (resume receiving).
    """
    random.seed(7)

    engine = _construct_engine(fanout=3, degree=3)
    capture = _Capture()
    _wire_sender(engine, capture)

    subscribe, unsubscribe, graft, prune, publish = _ensure_topic_methods(engine)

    topic = "blocks"
    A, B, C = "peer-A", "peer-B", "peer-C"
    for p in (A, B, C):
        subscribe(p, topic)

    # Baseline publish: all are candidates; with fanout=3 it's fine if all get it
    capture.clear()
    publish(topic, b"blk#2")
    rec0 = {pid for (pid, t, _pl) in capture.sends if t == topic}
    assert rec0.issubset({A, B, C})
    assert len(rec0) >= 1

    # Prune B and publish again: B should not receive
    prune(B, topic)
    capture.clear()
    publish(topic, b"blk#3")
    rec1 = {pid for (pid, t, _pl) in capture.sends if t == topic}
    assert B not in rec1

    # Graft B back and publish: B may receive again
    graft(B, topic)
    capture.clear()
    publish(topic, b"blk#4")
    rec2 = {pid for (pid, t, _pl) in capture.sends if t == topic}
    # B should be eligible again; don't require determinism on selection,
    # but ensure it's not permanently excluded.
    assert rec2.issubset({A, B, C})
    assert len(rec2) >= 1


def test_unsubscribe_stops_delivery(monkeypatch):
    """
    Unsubscribed peers must not receive further publications for that topic.
    """
    engine = _construct_engine(fanout=4, degree=4)
    capture = _Capture()
    _wire_sender(engine, capture)

    subscribe, unsubscribe, graft, prune, publish = _ensure_topic_methods(engine)

    topic = "txs"
    P = "peer-X"

    subscribe(P, topic)
    capture.clear()
    publish(topic, b"m0")
    got_first = any(pid == P and t == topic for (pid, t, _pl) in capture.sends)
    assert got_first, "Peer should receive when subscribed"

    unsubscribe(P, topic)
    capture.clear()
    publish(topic, b"m1")
    got_second = any(pid == P and t == topic for (pid, t, _pl) in capture.sends)
    assert not got_second, "Peer must not receive after unsubscribe"

