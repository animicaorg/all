import os
import time
from binascii import hexlify
from typing import Any, Callable, Dict, Optional, Tuple

import pytest

# --- Helpers -----------------------------------------------------------------


def hx(b: bytes) -> str:
    return "0x" + hexlify(b).decode()


def make_hex(n: int, byte: int = 0x11) -> str:
    return hx(bytes([byte]) * n)


def _load_gossip_module():
    """
    Try to import the gossip adapter; skip the test module if unavailable.
    """
    try:
        import randomness.adapters.p2p_gossip as gossip  # type: ignore

        return gossip
    except Exception as e:
        pytest.skip(f"randomness.adapters.p2p_gossip not available: {e}")


def _mk_instance(gossip_mod: Any) -> Any:
    """
    Try to construct a gossip adapter/handler instance using common names.
    Falls back to the module itself (expecting function-style APIs).
    """
    for cls_name in ("Gossip", "GossipAdapter", "P2PGossip", "Adapter", "Handler"):
        if hasattr(gossip_mod, cls_name):
            cls = getattr(gossip_mod, cls_name)
            try:
                # Prefer no-arg construction; then try a permissive dict config.
                try:
                    return cls()  # type: ignore[call-arg]
                except TypeError:
                    return cls({})  # type: ignore[call-arg]
            except Exception:
                continue
    return gossip_mod  # function-based API


def _find_handlers(
    obj: Any,
) -> Tuple[Callable[[Dict[str, Any]], Any], Callable[[Dict[str, Any]], Any]]:
    """
    Locate commit/reveal handlers on either an instance or module, trying a list
    of common method/function names. Returns (handle_commit, handle_reveal).
    """
    commit_names = (
        "process_commit",
        "ingest_commit",
        "handle_commit",
        "on_commit",
        "accept_commit",
        "receive_commit",
    )
    reveal_names = (
        "process_reveal",
        "ingest_reveal",
        "handle_reveal",
        "on_reveal",
        "accept_reveal",
        "receive_reveal",
    )

    commit_fn = None
    reveal_fn = None
    for name in commit_names:
        if hasattr(obj, name) and callable(getattr(obj, name)):
            commit_fn = getattr(obj, name)
            break
    for name in reveal_names:
        if hasattr(obj, name) and callable(getattr(obj, name)):
            reveal_fn = getattr(obj, name)
            break

    if commit_fn is None or reveal_fn is None:
        pytest.skip(
            "Could not locate commit/reveal handler functions on p2p_gossip adapter."
        )

    return commit_fn, reveal_fn


def _normalize_result(res: Any) -> Tuple[bool, Optional[str]]:
    """
    Normalize various return styles into (accepted, reason).
    - True / truthy => (True, None)
    - False / falsy => (False, None)
    - dict with keys like accepted/ok/duplicate/error/status...
    """
    if isinstance(res, bool):
        return (res, None)
    if isinstance(res, tuple) and res and isinstance(res[0], bool):
        # e.g., (True, "accepted") or (False, "duplicate")
        return (res[0], res[1] if len(res) > 1 else None)
    if isinstance(res, dict):
        # Common shapes: {"accepted":bool,...}, {"ok":bool}, {"status":"duplicate"}, {"error": "..."}
        if "accepted" in res and isinstance(res["accepted"], bool):
            return (res["accepted"], res.get("reason") or res.get("status"))
        if "ok" in res and isinstance(res["ok"], bool):
            return (res["ok"], res.get("reason") or res.get("status"))
        if "duplicate" in res and isinstance(res["duplicate"], bool):
            return (not res["duplicate"], "duplicate" if res["duplicate"] else None)
        if "status" in res and isinstance(res["status"], str):
            st = res["status"].lower()
            if st in ("accepted", "ok", "success"):
                return (True, st)
            if st in ("duplicate", "dupe", "seen", "ignored"):
                return (False, "duplicate")
            if st in ("invalid", "rejected", "bad"):
                return (False, "invalid")
        if "error" in res and res["error"]:
            return (False, "error")
    # Unknown style — treat as truthy/falsy
    return (bool(res), None)


# --- Test Vectors ------------------------------------------------------------

GOOD_COMMIT = make_hex(32, 0xAB)  # 32-byte hex commitment
BAD_COMMIT = "0x1234"  # too short, should be rejected by validators

GOOD_SALT = make_hex(16, 0xCD)  # 16 bytes
GOOD_PAYLOAD = make_hex(32, 0xEF)  # 32 bytes
BAD_SALT = "0xZZ"  # malformed hex
BAD_PAYLOAD = "0x"  # empty/malformed

PEER_A = "peerA"
PEER_B = "peerB"

# --- Tests -------------------------------------------------------------------


def test_commit_dedupe_and_validation():
    gossip_mod = _load_gossip_module()
    inst = _mk_instance(gossip_mod)
    handle_commit, _ = _find_handlers(inst)

    # 1) Valid commit should be accepted the first time
    msg = {
        "kind": "commit",
        "commitment": GOOD_COMMIT,
        "from": PEER_A,
        "ts": int(time.time()),
    }
    accepted, reason = _normalize_result(handle_commit(msg))  # type: ignore[misc]
    assert accepted, f"valid commit not accepted (reason={reason})"

    # 2) The same commit (same id) sent again from the same peer should be deduped
    accepted2, reason2 = _normalize_result(handle_commit(msg))  # type: ignore[misc]
    assert not accepted2, "duplicate commit from same peer was not deduped"

    # 3) The same commit sent from a different peer should also be deduped
    msg2 = dict(msg)
    msg2["from"] = PEER_B
    accepted3, reason3 = _normalize_result(handle_commit(msg2))  # type: ignore[misc]
    assert not accepted3, "duplicate commit from different peer was not deduped"

    # 4) Invalid commit payload should be rejected (length/format)
    bad = {
        "kind": "commit",
        "commitment": BAD_COMMIT,
        "from": PEER_A,
        "ts": int(time.time()),
    }
    ok_bad, why_bad = _normalize_result(handle_commit(bad))  # type: ignore[misc]
    assert not ok_bad, "invalid commit was not rejected"


def test_reveal_dedupe_and_validation():
    gossip_mod = _load_gossip_module()
    inst = _mk_instance(gossip_mod)
    _, handle_reveal = _find_handlers(inst)

    # 1) Valid reveal should be accepted once
    msg = {
        "kind": "reveal",
        # Include either salt/payload or a direct 'reveal' field — adapters differ.
        "salt": GOOD_SALT,
        "payload": GOOD_PAYLOAD,
        # Some implementations also want the associated commitment for fast validation:
        "commitment": GOOD_COMMIT,
        "from": PEER_A,
        "ts": int(time.time()),
    }
    accepted, reason = _normalize_result(handle_reveal(msg))  # type: ignore[misc]
    assert accepted, f"valid reveal not accepted (reason={reason})"

    # 2) Duplicate from same peer should be deduped
    accepted2, reason2 = _normalize_result(handle_reveal(msg))  # type: ignore[misc]
    assert not accepted2, "duplicate reveal from same peer was not deduped"

    # 3) Duplicate from different peer should also be deduped
    msg2 = dict(msg)
    msg2["from"] = PEER_B
    accepted3, reason3 = _normalize_result(handle_reveal(msg2))  # type: ignore[misc]
    assert not accepted3, "duplicate reveal from different peer was not deduped"

    # 4) Invalid reveal fields should be rejected
    bad = {
        "kind": "reveal",
        "salt": BAD_SALT,  # malformed hex
        "payload": BAD_PAYLOAD,  # malformed/empty
        "commitment": GOOD_COMMIT,
        "from": PEER_A,
        "ts": int(time.time()),
    }
    ok_bad, why_bad = _normalize_result(handle_reveal(bad))  # type: ignore[misc]
    assert not ok_bad, "invalid reveal was not rejected"
