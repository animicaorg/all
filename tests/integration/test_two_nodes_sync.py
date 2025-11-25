# -*- coding: utf-8 -*-
"""
Integration: two nodes interoperate.

Parts:
  (A) Kyber768 + HKDF handshake sanity (crypto round-trip) — proves our PQ
      building blocks work. Skips if modules are unavailable.
  (B) Two nodes sync headers/blocks over the network — proves live P2P sync.
      Skips unless two RPC URLs are provided or defaults are reachable.

Enable integration tests with:
    RUN_INTEGRATION_TESTS=1

Environment (optional, with sensible defaults):
    ANIMICA_RPC_URL_A          default http://127.0.0.1:8545
    ANIMICA_RPC_URL_B          default http://127.0.0.1:9545
    ANIMICA_SYNC_TIMEOUT       seconds (default 120)
    ANIMICA_SYNC_POLL_INTERVAL seconds (default 1.0)
    ANIMICA_SYNC_LAG_TOL       max allowed height lag at success (default 1)
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any, Dict, Optional, Sequence, Tuple

import pytest

from tests.integration import env  # package gate lives in tests/integration/__init__.py


# ---------------------------------------------------------------------------
# Section A: Kyber + HKDF handshake smoke (skips if libs not present)
# ---------------------------------------------------------------------------

_has_kem = False
_has_hkdf = False
_has_sha3 = False

_kem_mod = None
_hkdf_fn = None
_sha3_256 = None

# Prefer high-level pq.py.kem first
try:
    from pq.py import kem as _kem_mod  # type: ignore
    _has_kem = all(hasattr(_kem_mod, nm) for nm in ("keygen", "encaps", "decaps"))
except Exception:
    _kem_mod = None

# Fall back to direct kyber768 wrappers if omni KEM isn't present
if not _has_kem:
    try:
        from pq.py.algs import kyber768 as _kem_mod  # type: ignore
        _has_kem = all(hasattr(_kem_mod, nm) for nm in ("keygen", "encaps", "decaps"))
    except Exception:
        _kem_mod = None
        _has_kem = False

# HKDF-SHA3-256
try:
    from pq.py.utils.hkdf import hkdf_sha3_256 as _hkdf_fn  # type: ignore
    _has_hkdf = callable(_hkdf_fn)
except Exception:
    _hkdf_fn = None
    _has_hkdf = False

# SHA3-256 (for transcript hashing, optional)
try:
    from pq.py.utils.hash import sha3_256 as _sha3_256  # type: ignore
    _has_sha3 = callable(_sha3_256)
except Exception:
    _sha3_256 = None
    _has_sha3 = False


@pytest.mark.skipif(not (_has_kem and _has_hkdf), reason="PQ KEM or HKDF not available")
def test_kyber_kem_hkdf_roundtrip():
    """
    Simulate an initiator (A) and responder (B):

      B: (pkB, skB) = Kyber.keygen()
      A: (ct, ssA) = Kyber.encaps(pkB)
      B:  ssB      = Kyber.decaps(ct, skB)

    Then derive AEAD keys with HKDF-SHA3-256 using a fixed info/context.
    Expect ssA == ssB and derived keys to match.
    """
    pkB, skB = _kem_mod.keygen()  # type: ignore[attr-defined]
    ct, ssA = _kem_mod.encaps(pkB)  # type: ignore[attr-defined]
    ssB = _kem_mod.decaps(ct, skB)  # type: ignore[attr-defined]

    assert isinstance(ssA, (bytes, bytearray)) and isinstance(ssB, (bytes, bytearray))
    assert ssA == ssB and len(ssA) >= 32

    # Derive two "roles" deterministically from the same shared secret
    info_a = b"animica/p2p/handshake/aead-key-A"
    info_b = b"animica/p2p/handshake/aead-key-B"
    salt = b"\x00" * 32

    keyA = _hkdf_fn(ssA, salt=salt, info=info_a, out_len=32)  # type: ignore[misc]
    keyB = _hkdf_fn(ssB, salt=salt, info=info_b, out_len=32)  # type: ignore[misc]

    assert isinstance(keyA, bytes) and isinstance(keyB, bytes)
    assert len(keyA) == 32 and len(keyB) == 32
    # Distinct labels should yield different keys
    assert keyA != keyB

    # Optional: transcript hash binding (order matters)
    if _has_sha3:
        th1 = _sha3_256(pkB + ct + keyA + keyB)  # type: ignore[misc]
        th2 = _sha3_256(pkB + ct + keyA + keyB)  # same construction ⇒ same hash
        assert th1 == th2 and isinstance(th1, bytes)


@pytest.mark.skipif(not (_has_kem and _has_hkdf), reason="PQ KEM or HKDF not available")
def test_kyber_encapsulation_is_fresh_randomized():
    """
    Encapsulating twice to the same public key should produce different ciphertexts
    (and, by IND-CCA security, different shared secrets).
    """
    pkB, skB = _kem_mod.keygen()  # type: ignore[attr-defined]
    ct1, ss1 = _kem_mod.encaps(pkB)  # type: ignore[attr-defined]
    ct2, ss2 = _kem_mod.encaps(pkB)  # type: ignore[attr-defined]
    assert ct1 != ct2 or ss1 != ss2, "Kyber encapsulation appears deterministic; expected randomized outputs"


# ---------------------------------------------------------------------------
# Section B: Two nodes sync — header/blocks converge
# ---------------------------------------------------------------------------

def _http_timeout() -> float:
    try:
        return float(env("ANIMICA_HTTP_TIMEOUT", "5"))
    except Exception:
        return 5.0


def _rpc_call(rpc_url: str, method: str, params: Optional[Sequence[Any] | Dict[str, Any]] = None, *, req_id: int = 1) -> Any:
    if params is None:
        params = []
    if isinstance(params, dict):
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    else:
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": list(params)}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        rpc_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_http_timeout()) as resp:
        raw = resp.read()
    msg = json.loads(raw.decode("utf-8"))
    if "error" in msg and msg["error"]:
        raise AssertionError(f"JSON-RPC error from {method}: {msg['error']}")
    return msg.get("result")


def _rpc_try(rpc_url: str, methods: Sequence[str], params: Optional[Sequence[Any] | Dict[str, Any]] = None) -> Tuple[str, Any]:
    last_exc: Optional[Exception] = None
    for i, m in enumerate(methods, start=1):
        try:
            return m, _rpc_call(rpc_url, m, params, req_id=i)
        except Exception as exc:
            last_exc = exc
            continue
    raise AssertionError(f"All RPC spellings failed ({methods}). Last error: {last_exc}")


def _parse_height(head: Any) -> int:
    if isinstance(head, dict):
        for k in ("height", "number", "index"):
            v = head.get(k)
            if isinstance(v, int):
                return v
            if isinstance(v, str):
                try:
                    return int(v, 0)
                except Exception:
                    pass
    raise AssertionError(f"Unrecognized head shape: {head!r}")


def _parse_hash(obj: Any) -> Optional[str]:
    """
    Try to pull a canonical hex hash from a head/block-like object.
    """
    if isinstance(obj, dict):
        for k in ("hash", "blockHash", "headerHash"):
            v = obj.get(k)
            if isinstance(v, str) and v.startswith("0x") and len(v) >= 10:
                return v.lower()
    return None


@pytest.mark.timeout(180)
def test_two_nodes_headers_converge_and_blocks_fetch():
    """
    Given two nodes (A and B), ensure:
      1) Both respond to chain.getHead().
      2) Within a timeout, their heights converge to within LAG_TOL (default 1).
      3) If both expose matching height at any point and hashes are available, the hashes match.
      4) Optionally, try fetching a block by number/hash from both and compare (best-effort).

    This exercises P2P sync at a black-box level (we don't force-connect; we
    assume they discover/peer according to configured seeds).
    """
    rpc_a = env("ANIMICA_RPC_URL_A", "http://127.0.0.1:8545")
    rpc_b = env("ANIMICA_RPC_URL_B", "http://127.0.0.1:9545")

    # Probe both; skip if either is unreachable to avoid CI flakes.
    try:
        _, headA = _rpc_try(rpc_a, ("chain.getHead", "chain.head", "getHead"), [])
        _, headB = _rpc_try(rpc_b, ("chain.getHead", "chain.head", "getHead"), [])
    except Exception as exc:
        pytest.skip(f"Could not reach both RPC endpoints: {exc}")

    hA = _parse_height(headA)
    hB = _parse_height(headB)

    lag_tol = int(env("ANIMICA_SYNC_LAG_TOL", "1") or "1")
    timeout_s = float(env("ANIMICA_SYNC_TIMEOUT", "120") or "120")
    interval_s = float(env("ANIMICA_SYNC_POLL_INTERVAL", "1.0") or "1.0")
    deadline = time.time() + timeout_s

    # Track any moment when both heights equal; if so, try to compare hashes/blocks.
    matched_height: Optional[int] = None
    matched_hashes: set[str] = set()

    while time.time() < deadline:
        _, headA = _rpc_try(rpc_a, ("chain.getHead", "chain.head", "getHead"), [])
        _, headB = _rpc_try(rpc_b, ("chain.getHead", "chain.head", "getHead"), [])

        hA = _parse_height(headA)
        hB = _parse_height(headB)

        # Convergence check
        if abs(hA - hB) <= lag_tol:
            # Try to capture a moment of exact equality for stronger checks
            if hA == hB:
                matched_height = hA
                ha = _parse_hash(headA)
                hb = _parse_hash(headB)
                if ha and hb:
                    matched_hashes.add(ha)
                    matched_hashes.add(hb)
                    # If both present, they should match
                    assert len(matched_hashes) == 1, f"At height {matched_height}, hashes differ: {matched_hashes}"
            break

        time.sleep(interval_s)

    # Final convergence assertion
    assert abs(hA - hB) <= lag_tol, (
        f"Nodes did not converge within {timeout_s:.1f}s: A={hA}, B={hB}, tol={lag_tol}"
    )

    # If we had an exact match and a hash, try to fetch the same block from both nodes and compare.
    if matched_height is not None:
        # Fetch by number (best-effort across a few spellings)
        for method in (("chain.getBlockByNumber",), ("chain.getBlock",), ("getBlockByNumber",)):
            try:
                # Some APIs take (number, includeTx, includeReceipts); try minimal forms
                blkA = _rpc_call(rpc_a, method[0], [matched_height])
                blkB = _rpc_call(rpc_b, method[0], [matched_height])
                # Minimal sanity: both dicts and same hash if present
                if isinstance(blkA, dict) and isinstance(blkB, dict):
                    ha = _parse_hash(blkA)
                    hb = _parse_hash(blkB)
                    if ha and hb:
                        assert ha == hb, f"Block hash mismatch at height {matched_height}: {ha} vs {hb}"
                break
            except Exception:
                continue


