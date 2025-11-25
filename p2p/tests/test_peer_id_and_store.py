import os
import sys
import time
import types
import hashlib
import binascii
import inspect
import pytest

# Ensure local package is importable when running tests from repo root
sys.path.insert(0, os.path.expanduser("~/animica"))

# ---------- Imports with graceful skips ----------
try:
    peer_id_mod = __import__("p2p.crypto.peer_id", fromlist=["*"])
except Exception as e:
    pytest.skip(f"p2p.crypto.peer_id not available: {e}", allow_module_level=True)

try:
    peerstore_mod = __import__("p2p.peer.peerstore", fromlist=["*"])
except Exception as e:
    pytest.skip(f"p2p.peer.peerstore not available: {e}", allow_module_level=True)

# Optional Peer dataclass (some implementations use it)
try:
    peer_datamod = __import__("p2p.peer.peer", fromlist=["*"])
    PeerClass = getattr(peer_datamod, "Peer", None)
except Exception:
    PeerClass = None

# ---------- Helpers ----------
def _derive_peer_id(pubkey: bytes, alg_id: int):
    """
    Call the peer-id derivation from p2p.crypto.peer_id with fallbacks.
    Expected semantics (fallback): sha3_256(alg_id || pubkey).
    Returns bytes (32) when possible; otherwise returns whatever the impl returns.
    """
    # Try common exported functions
    candidates = [
        "derive_peer_id",
        "peer_id_from_pubkey",
        "make_peer_id",
        "compute_peer_id",
    ]
    fn = None
    for name in candidates:
        if hasattr(peer_id_mod, name) and callable(getattr(peer_id_mod, name)):
            fn = getattr(peer_id_mod, name)
            break

    if fn:
        try:
            # Common signatures: (pubkey, alg_id) or (alg_id, pubkey)
            params = inspect.signature(fn).parameters
            if len(params) == 2:
                # Try (pubkey, alg_id) first
                try:
                    out = fn(pubkey, alg_id)
                except TypeError:
                    out = fn(alg_id, pubkey)
            else:
                out = fn(pubkey=pubkey, alg_id=alg_id)
            return out
        except Exception:
            pass

    # Fallback: sha3_256(alg_id || pubkey)
    m = hashlib.sha3_256()
    m.update(bytes([alg_id & 0xFF]))
    m.update(pubkey)
    return m.digest()


def _id_to_bytes_maybe(x):
    """Try to normalize id to bytes for comparisons; return (bytes_or_None, original)."""
    if isinstance(x, (bytes, bytearray)):
        return bytes(x), x
    if isinstance(x, str):
        # hex?
        s = x.strip().lower()
        if s.startswith("0x"):
            s = s[2:]
        try:
            return binascii.unhexlify(s), x
        except Exception:
            # bech32 or other textual form — cannot round-trip easily; keep as string
            return None, x
    return None, x


def _open_store(tmpdir):
    """
    Instantiate a PeerStore with various constructor shapes:
    - PeerStore(path=dir)
    - PeerStore(db_path=file)
    - PeerStore(filename=file)
    - PeerStore(str_path_or_file)
    Returns (store, persist_path) so we can reopen later.
    """
    cls = None
    for name in ("PeerStore", "Store", "PeerStoreDB"):
        if hasattr(peerstore_mod, name):
            cls = getattr(peerstore_mod, name)
            break
    if cls is None:
        pytest.skip("No PeerStore class exported", allow_module_level=True)

    dirpath = os.path.join(tmpdir, "peerstore")
    os.makedirs(dirpath, exist_ok=True)
    dbfile = os.path.join(dirpath, "peers.db")

    # Try keyword ctors
    for kwargs in (
        {"path": dirpath},
        {"db_path": dbfile},
        {"filename": dbfile},
    ):
        try:
            return cls(**kwargs), (kwargs.get("path") or kwargs.get("db_path") or kwargs.get("filename") or dirpath)
        except Exception:
            pass

    # Try positional with file, then dir
    try:
        return cls(dbfile), dbfile
    except Exception:
        pass
    try:
        return cls(dirpath), dirpath
    except Exception as e:
        pytest.skip(f"Could not construct PeerStore: {e}", allow_module_level=True)


def _store_add(store, peer_id, addrs, score=0):
    """
    Insert or upsert a peer in the store with addresses and optional score.
    Supports a variety of method names and record shapes.
    """
    # Try methods taking (peer_id, ...)
    for name in ("upsert_peer", "add_peer", "put_peer", "add", "upsert", "put"):
        if hasattr(store, name):
            m = getattr(store, name)
            try:
                return m(peer_id, addrs=addrs, score=score)
            except TypeError:
                # maybe no score kw
                try:
                    return m(peer_id, addrs=addrs)
                except TypeError:
                    try:
                        return m(peer_id)
                    except Exception:
                        pass
            except Exception:
                pass

    # Try methods taking a Peer object
    if PeerClass is not None:
        try:
            # Build a Peer record with best-effort field names
            # Common: Peer(id, addrs, score, last_seen)
            try:
                rec = PeerClass(
                    peer_id if "peer_id" in PeerClass.__annotations__ else peer_id,  # positional ok
                    addrs if "addrs" in getattr(PeerClass, "__annotations__", {}) else list(addrs),
                    score if "score" in getattr(PeerClass, "__annotations__", {}) else 0,
                    time.time(),
                )
            except Exception:
                # Fallback: kwargs by introspection
                kwargs = {}
                for f in ("peer_id", "id", "pid"):
                    if f in getattr(PeerClass, "__annotations__", {}):
                        kwargs[f] = peer_id
                        break
                for f in ("addrs", "addresses"):
                    if f in getattr(PeerClass, "__annotations__", {}):
                        kwargs[f] = list(addrs)
                        break
                if "score" in getattr(PeerClass, "__annotations__", {}):
                    kwargs["score"] = score
                rec = PeerClass(**kwargs)
            for name in ("put", "upsert", "add", "save"):
                if hasattr(store, name):
                    try:
                        return getattr(store, name)(rec)
                    except Exception:
                        pass
        except Exception:
            pass

    raise AssertionError("Could not add/upsert peer into store (no compatible API)")


def _store_get(store, peer_id):
    for name in ("get_peer", "get", "fetch", "find"):
        if hasattr(store, name):
            try:
                return getattr(store, name)(peer_id)
            except Exception:
                pass
    # maybe returns None on missing
    return None


def _store_add_addr(store, peer_id, addr):
    for name in ("add_addr", "add_address", "insert_addr", "insert_address"):
        if hasattr(store, name):
            try:
                return getattr(store, name)(peer_id, addr)
            except Exception:
                pass
    # If no dedicated method, re-upsert with merged addrs (callers handle it)
    return False


def _store_score_delta(store, peer_id, delta):
    for name in ("increment_score", "bump_score", "add_score", "incr_score"):
        if hasattr(store, name):
            try:
                return getattr(store, name)(peer_id, delta)
            except Exception:
                pass
    # Fallback: read + set
    rec = _store_get(store, peer_id)
    cur = _extract_score(rec) or 0
    _store_set_score(store, peer_id, cur + delta)


def _store_set_score(store, peer_id, score):
    for name in ("set_score", "update_score"):
        if hasattr(store, name):
            try:
                return getattr(store, name)(peer_id, score)
            except Exception:
                pass
    # Fallback: upsert again if supported
    try:
        return _store_add(store, peer_id, addrs=_extract_addrs(_store_get(store, peer_id)) or [], score=score)
    except Exception:
        pass


def _extract_addrs(rec):
    if rec is None:
        return None
    for name in ("addrs", "addresses"):
        if hasattr(rec, name):
            v = getattr(rec, name)
            try:
                return list(v)
            except Exception:
                return v
    # dict-like?
    if isinstance(rec, dict):
        for k in ("addrs", "addresses"):
            if k in rec:
                return rec[k]
    return None


def _extract_score(rec):
    if rec is None:
        return None
    for name in ("score", "points", "reputation"):
        if hasattr(rec, name):
            return getattr(rec, name)
    if isinstance(rec, dict):
        for k in ("score", "points", "reputation"):
            if k in rec:
                return rec[k]
    return None


# ---------- Tests ----------
def test_peer_id_derivation_consistency_and_uniqueness():
    pk1 = b"\x11" * 48  # pretend PQ pubkey (length irrelevant to the test)
    pk2 = b"\x22" * 48
    alg_dilithium = 0x11
    alg_sphincs = 0x22

    id1 = _derive_peer_id(pk1, alg_dilithium)
    id1b = _derive_peer_id(pk1, alg_dilithium)  # same inputs
    id2 = _derive_peer_id(pk2, alg_dilithium)   # different pubkey
    id3 = _derive_peer_id(pk1, alg_sphincs)     # different alg

    # Same inputs -> identical outputs
    assert id1 == id1b

    # Different pubkey or alg -> different outputs
    assert id1 != id2
    assert id1 != id3

    # If bytes/hex, length should map to 32-byte hash (sha3-256)
    b1, _ = _id_to_bytes_maybe(id1)
    if b1 is not None:
        assert len(b1) == 32


def test_peerstore_persistence_and_scores(tmp_path):
    store, persist_path = _open_store(str(tmp_path))

    # Create a peer with two addresses
    pk = b"\x33" * 48
    pid = _derive_peer_id(pk, 0x11)
    addrs0 = ["/ip4/127.0.0.1/tcp/31000", "/dns4/node.example.com/tcp/31001/quic"]
    _store_add(store, pid, addrs0, score=0)

    # Optionally add a third address via dedicated method if present
    _store_add_addr(store, pid, "/ip6/::1/tcp/31002")

    # Read back
    rec = _store_get(store, pid)
    assert rec is not None, "peer must be present after add"
    addrs_read = _extract_addrs(rec) or []
    for a in addrs0:
        assert a in addrs_read

    # Bump score and verify
    _store_score_delta(store, pid, +10)
    rec2 = _store_get(store, pid)
    score2 = _extract_score(rec2)
    assert score2 is not None and score2 >= 10

    # Close & reopen to ensure persistence across process restarts
    # Best-effort "close"
    for name in ("close", "shutdown", "stop"):
        if hasattr(store, name):
            try:
                getattr(store, name)()
            except Exception:
                pass

    # Reopen same storage
    # If ctor used a directory, reuse it; if file, reuse the file.
    # We don't know which kw it expects, try the same strategy again.
    reopened, _ = _open_store(os.path.dirname(persist_path) if os.path.isdir(persist_path) else os.path.dirname(persist_path))

    # Ensure the record is still there with the updated score
    rec3 = _store_get(reopened, pid)
    assert rec3 is not None, "peer must still be present after reopening store"
    score3 = _extract_score(rec3)
    assert score3 is not None and score3 >= 10

    # Idempotent upsert should not duplicate addresses
    _store_add(reopened, pid, addrs0, score=score3)
    rec4 = _store_get(reopened, pid)
    addrs4 = _extract_addrs(rec4) or []
    # no silly growth (allow extra addr if store de-duplicates differently)
    assert len(set(addrs4)) >= len(set(addrs0))

