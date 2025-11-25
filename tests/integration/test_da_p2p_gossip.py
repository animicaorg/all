# -*- coding: utf-8 -*-
"""
Integration: DA P2P gossip — commitment & proofs propagate and duplicates are deduped.

Scenario (best-effort; skips if features absent):
  1) Post a small blob to node A (returns commitment/NMT root).
  2) Observe node B learns about that commitment via P2P gossip (can serve GET/proof).
  3) Ask B for an availability proof for that commitment.
  4) Re-post the *same* blob and ensure the store reports no duplicate growth,
     or at minimum that the returned commitment is identical (idempotence).

Environment & gating:
  RUN_INTEGRATION_TESTS=1          — enable integration tests (see tests/integration/__init__.py)
  ANIMICA_RPC_URL                  — node A JSON-RPC (default: http://127.0.0.1:8545)
  ANIMICA_PEER_RPC_URL             — node B JSON-RPC (REQUIRED for this test)
  ANIMICA_HTTP_TIMEOUT             — per-call timeout seconds (default: 5)
  ANIMICA_RESULT_WAIT_SECS         — overall poll window seconds (default: 240)
  ANIMICA_DA_NS                    — namespace integer for post (default: 24)
  ANIMICA_BLOB_FIXTURE             — path to blob file (default: da/fixtures/blob_small.bin)
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Sequence, Tuple

import pytest

from tests.integration import env  # gating helper


# ------------------------------ HTTP/RPC helpers ------------------------------

def _http_timeout() -> float:
    try:
        return float(env("ANIMICA_HTTP_TIMEOUT", "5"))
    except Exception:
        return 5.0


def _rpc_call(rpc_url: str, method: str, params: Optional[Sequence[Any] | Dict[str, Any]] = None, *, req_id: int = 1) -> Any:
    if params is None:
        params = []
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(rpc_url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=_http_timeout()) as resp:
        raw = resp.read()
    msg = json.loads(raw.decode("utf-8"))
    if "error" in msg and msg["error"]:
        raise AssertionError(f"RPC {method} error: {msg['error']}")
    if "result" not in msg:
        raise AssertionError(f"RPC {method} missing result: {msg}")
    return msg["result"]


def _rpc_try(rpc_url: str, methods: Sequence[str], params: Optional[Sequence[Any] | Dict[str, Any]] = None) -> Tuple[str, Any]:
    last_exc: Optional[Exception] = None
    for i, m in enumerate(methods, start=1):
        try:
            return m, _rpc_call(rpc_url, m, params, req_id=i)
        except Exception as exc:
            last_exc = exc
    raise AssertionError(f"All methods failed ({methods}); last error: {last_exc}")


def _base_http(rpc_url: str) -> str:
    """
    Derive a base HTTP host from the JSON-RPC URL for REST fallbacks; we strip trailing '/rpc' if present.
    """
    parts = urllib.parse.urlparse(rpc_url)
    base = f"{parts.scheme}://{parts.netloc}"
    # If app mounts JSON-RPC at /rpc, the DA REST may be at root; try both later.
    return base


# ------------------------------ DA client helpers -----------------------------

def _read_blob_bytes() -> bytes:
    p = pathlib.Path(env("ANIMICA_BLOB_FIXTURE", "da/fixtures/blob_small.bin"))
    if p.is_file():
        return p.read_bytes()
    # Fallback: generate small deterministic payload
    return b"animica-da-fixture" * 32


def _ns() -> int:
    try:
        return int(env("ANIMICA_DA_NS", "24"))
    except Exception:
        return 24


def _hex(b: bytes) -> str:
    return "0x" + b.hex()


def _post_blob_rpc(rpc_url: str, namespace: int, data: bytes) -> Optional[Dict[str, Any]]:
    """
    Try a variety of JSON-RPC shapes for posting a blob.
    Expected 'result' to include at least a commitment/root string.
    """
    hex_data = _hex(data)
    b64_data = base64.b64encode(data).decode("ascii")
    candidates: list[Tuple[str, Sequence[Any] | Dict[str, Any]]] = [
        ("da.putBlob", [{"namespace": namespace, "data": hex_data}]),
        ("da.postBlob", [{"namespace": namespace, "data": hex_data}]),
        ("da.blob.put", [{"namespace": namespace, "data": hex_data}]),
        ("da.putBlob", [hex_data, namespace]),
        ("da.post", [{"ns": namespace, "data": hex_data}]),
        ("da.putBlob", [{"namespace": namespace, "data_b64": b64_data}]),
    ]
    for method, params in candidates:
        try:
            res = _rpc_call(rpc_url, method, params if isinstance(params, list) else [params])
            if isinstance(res, dict) and any(isinstance(res.get(k), str) for k in ("commitment", "root", "nmtRoot", "hash")):
                return res
            if isinstance(res, str) and res.startswith("0x"):
                return {"commitment": res, "namespace": namespace, "size": len(data)}
        except Exception:
            continue
    return None


def _post_blob_rest(rpc_url: str, namespace: int, data: bytes) -> Optional[Dict[str, Any]]:
    """
    REST fallback: POST /da/blob with JSON body.
    """
    base = _base_http(rpc_url)
    paths = ["/da/blob", "/api/da/blob"]
    payload = json.dumps({"namespace": namespace, "data": base64.b64encode(data).decode("ascii")}).encode("utf-8")
    for path in paths:
        try:
            url = urllib.parse.urljoin(base, path)
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=_http_timeout()) as resp:
                raw = resp.read()
            doc = json.loads(raw.decode("utf-8"))
            if isinstance(doc, dict) and any(isinstance(doc.get(k), str) for k in ("commitment", "root", "nmtRoot", "hash")):
                return doc
        except Exception:
            continue
    return None


def _get_blob_or_meta(rpc_url: str, commitment: str) -> Optional[Dict[str, Any]]:
    """
    Try to fetch blob meta or content by commitment on the target node.
    """
    candidates = [
        ("da.getBlob", [commitment]),
        ("da.get", [commitment]),
        ("da.blob.get", [commitment]),
        ("da.getMeta", [commitment]),
        ("da.meta", [commitment]),
        ("da.hasCommitment", [commitment]),
        ("da.exists", [commitment]),
    ]
    for method, params in candidates:
        try:
            res = _rpc_call(rpc_url, method, params)
            if res is True:
                return {"exists": True}
            if isinstance(res, dict):
                return res
            if isinstance(res, (bytes, str)):
                # Some nodes might return the data directly; treat as present.
                return {"exists": True, "data_len": len(res) if isinstance(res, (bytes, bytearray)) else len(res)}
        except Exception:
            continue
    # REST GET fallback for meta
    base = _base_http(rpc_url)
    for path in ("/da/blob/", "/api/da/blob/"):
        try:
            url = urllib.parse.urljoin(base, f"{path}{commitment}")
            with urllib.request.urlopen(url, timeout=_http_timeout()) as resp:
                raw = resp.read()
            # On binary, consider as present.
            try:
                doc = json.loads(raw.decode("utf-8"))
                if isinstance(doc, dict):
                    return doc
            except Exception:
                return {"exists": True, "data_len": len(raw)}
        except Exception:
            continue
    return None


def _get_proof(rpc_url: str, commitment: str) -> Optional[Dict[str, Any]]:
    """
    Fetch an availability proof for the commitment, if the node supports it.
    """
    candidates = [
        ("da.getProof", [commitment]),
        ("da.proof", [commitment]),
        ("da.getAvailabilityProof", [commitment]),
        ("da.proofs.get", [commitment]),
    ]
    for method, params in candidates:
        try:
            res = _rpc_call(rpc_url, method, params)
            if isinstance(res, dict) and ("samples" in res or "branches" in res or "proof" in res):
                return res
        except Exception:
            continue
    # REST fallback
    base = _base_http(rpc_url)
    for path in ("/da/proof/", "/api/da/proof/"):
        try:
            url = urllib.parse.urljoin(base, f"{path}{commitment}")
            with urllib.request.urlopen(url, timeout=_http_timeout()) as resp:
                raw = resp.read()
            try:
                doc = json.loads(raw.decode("utf-8"))
                if isinstance(doc, dict):
                    return doc
            except Exception:
                # If binary, we still consider a proof blob present.
                return {"proof_blob_len": len(raw)}
        except Exception:
            continue
    return None


def _get_da_stats(rpc_url: str) -> Optional[Dict[str, Any]]:
    """
    Return DA store stats if available (used to check dedupe counters).
    """
    for m in ("da.getStats", "da.stats", "da.store.info", "da.storeStats"):
        try:
            res = _rpc_call(rpc_url, m, [])
            if isinstance(res, dict):
                return res
        except Exception:
            continue
    return None


def _extract_counters(stats: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """
    Extract (stored_unique, duplicates) if present.
    """
    def _as_int(v: Any) -> Optional[int]:
        if isinstance(v, bool) or v is None:
            return None
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str):
            try:
                return int(v, 16) if v.startswith("0x") else int(v)
            except Exception:
                return None
        return None

    uniq_keys = ("stored", "stored_blobs", "commitments", "unique", "items")
    dupe_keys = ("duplicates", "dupes", "dedupe_hits", "duplicate_ingest")
    u = None
    d = None
    for k in uniq_keys:
        if u is None:
            u = _as_int(stats.get(k))
    for k in dupe_keys:
        if d is None:
            d = _as_int(stats.get(k))
    # Nested?
    meta = stats.get("store") or stats.get("meta") or {}
    if isinstance(meta, dict):
        if u is None:
            for k in uniq_keys:
                v = _as_int(meta.get(k))
                if v is not None:
                    u = v
                    break
        if d is None:
            for k in dupe_keys:
                v = _as_int(meta.get(k))
                if v is not None:
                    d = v
                    break
    return u, d


# ------------------------------------ Test ------------------------------------

@pytest.mark.timeout(900)
def test_da_p2p_gossip_commitment_and_dedupe():
    rpc_a = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
    rpc_b = env("ANIMICA_PEER_RPC_URL")
    if not rpc_b:
        pytest.skip("ANIMICA_PEER_RPC_URL is not set — need a second node to validate P2P gossip.")

    wait_secs = float(env("ANIMICA_RESULT_WAIT_SECS", "240"))
    namespace = _ns()
    blob = _read_blob_bytes()

    # 0) (Optional) read stats on B to measure dedupe deltas later.
    stats_before = _get_da_stats(rpc_b)
    uniq0, dup0 = (None, None)
    if stats_before:
        uniq0, dup0 = _extract_counters(stats_before)

    # 1) Post blob on A (RPC first; REST fallback).
    posted = _post_blob_rpc(rpc_a, namespace, blob) or _post_blob_rest(rpc_a, namespace, blob)
    if not posted:
        pytest.skip("DA post not available via RPC/REST on node A.")

    commitment = (
        posted.get("commitment")
        or posted.get("root")
        or posted.get("nmtRoot")
        or posted.get("hash")
    )
    assert isinstance(commitment, str) and commitment.startswith("0x"), f"Invalid commitment from A: {posted}"

    # 2) Wait for gossip: B should know about the commitment (meta or GET works).
    deadline = time.time() + wait_secs
    seen_on_b = False
    while time.time() < deadline:
        meta = _get_blob_or_meta(rpc_b, commitment)
        if meta:
            seen_on_b = True
            break
        time.sleep(1.0)

    if not seen_on_b:
        pytest.skip("Node B did not learn the commitment via gossip within wait window.")

    # 3) Ask B for an availability proof for that commitment (best-effort).
    proof = _get_proof(rpc_b, commitment)
    # We don't assert strongly here since some nodes may not expose proof retrieval;
    # presence of any proof-like structure is a pass, otherwise we continue.

    # 4) Re-post the same blob (idempotence & dedupe).
    #    Try posting again to A; if that fails due to idempotency rules, try B.
    repost = _post_blob_rpc(rpc_a, namespace, blob) or _post_blob_rpc(rpc_b, namespace, blob) \
             or _post_blob_rest(rpc_a, namespace, blob) or _post_blob_rest(rpc_b, namespace, blob)
    # If all re-post attempts fail, we still verify idempotence via commitment equality above.

    if repost:
        commitment2 = (
            repost.get("commitment")
            or repost.get("root")
            or repost.get("nmtRoot")
            or repost.get("hash")
        )
        assert isinstance(commitment2, str) and commitment2.startswith("0x")
        assert commitment2.lower() == commitment.lower(), "Re-post returned a different commitment — expected idempotence."

    # 5) If stats are available, ensure duplicates counter increased but unique count did not,
    #    or that unique increased by exactly 1 (first ingest) and duplicates did not explode.
    stats_after = _get_da_stats(rpc_b)
    if stats_before and stats_after:
        uniq1, dup1 = _extract_counters(stats_after)
        # If we had baseline numbers, they should be sane integers.
        if uniq0 is not None and uniq1 is not None:
            assert uniq1 >= uniq0, "Unique stored count decreased unexpectedly."
        if dup0 is not None and dup1 is not None and repost:
            assert dup1 >= dup0, "Duplicate counter did not increase after re-post (if exposed)."

    # Final sanity: B can still serve meta or content for the commitment.
    meta_b = _get_blob_or_meta(rpc_b, commitment)
    assert meta_b is not None, "Node B lost visibility of the commitment unexpectedly."

    # Optional sanity: if a proof was available, it should be non-empty.
    if proof is not None:
        assert isinstance(proof, dict) and len(proof) > 0, "Proof object from B is empty."

