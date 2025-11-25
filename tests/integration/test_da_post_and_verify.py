# -*- coding: utf-8 -*-
"""
Integration: DA post → commitment → light verify → fetch

This test exercises the DA HTTP endpoints mounted into the main FastAPI app:
  1) POST a small blob to /da/blob (trying a couple of common payload shapes).
  2) Read the returned commitment (NMT root) and *locally* recompute the same
     commitment using the library (da.blob.commitment.commit) — "light verify".
  3) GET the blob back by commitment and ensure a byte-for-byte match.
  4) (Best-effort) fetch a proof and sanity-check the reported root matches.

Enabled only when RUN_INTEGRATION_TESTS=1 (package gate in tests/integration/__init__.py).

Environment (optional unless noted):
  ANIMICA_DA_BASE_URL   — Base HTTP origin for the node app (default derived from ANIMICA_RPC_URL
                          by stripping a trailing '/rpc', or http://127.0.0.1:8545 if unset)
  ANIMICA_RPC_URL       — Used only to derive the default base URL (see above)
  ANIMICA_DA_NS         — Namespace id (integer) to use for the blob (default: 24)
  ANIMICA_BLOB_PATH     — If set, post the exact bytes from this file; otherwise a deterministic
                          4 KiB blob will be generated.
  ANIMICA_HTTP_TIMEOUT  — Per-request timeout in seconds (default: 5)
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytest

from tests.integration import env  # RUN_INTEGRATION_TESTS gate lives here

# Local "light verify" uses the in-repo DA commitment helper
try:
    from da.blob.commitment import commit as da_commit  # (data: bytes, ns: int) -> (root: bytes, size: int, ns_out: int)
except Exception:  # pragma: no cover - if the package layout isn't importable in this environment
    da_commit = None  # type: ignore[assignment]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _http_timeout() -> float:
    try:
        return float(env("ANIMICA_HTTP_TIMEOUT", "5"))
    except Exception:
        return 5.0


def _derive_base_url() -> str:
    base = env("ANIMICA_DA_BASE_URL")
    if base:
        return base.rstrip("/")
    rpc = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
    # strip a trailing /rpc if present
    if rpc.endswith("/rpc"):
        rpc = rpc[: -len("/rpc")]
    return rpc.rstrip("/")


def _hex(b: bytes) -> str:
    return "0x" + b.hex()


def _is_hex(s: Any) -> bool:
    return isinstance(s, str) and s.startswith("0x") and len(s) >= 10


def _extract_commitment(obj: Any) -> Optional[str]:
    # Accept common shapes: {"commitment": "0x.."}, {"root": "0x.."}, {"nmt_root": "0x.."}, {"daRoot": "0x.."}
    if isinstance(obj, dict):
        for k in ("commitment", "root", "nmt_root", "daRoot"):
            v = obj.get(k)
            if _is_hex(v):
                return v.lower()
    if _is_hex(obj):
        return str(obj).lower()
    return None


def _post_bytes(url: str, data: bytes, *, headers: Optional[Dict[str, str]] = None) -> Tuple[int, bytes, Dict[str, str]]:
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/octet-stream", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_http_timeout()) as resp:
        body = resp.read()
        return resp.status, body, dict(resp.headers)


def _post_json(url: str, obj: Dict[str, Any]) -> Tuple[int, bytes, Dict[str, str]]:
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_http_timeout()) as resp:
        body = resp.read()
        return resp.status, body, dict(resp.headers)


def _get(url: str) -> Tuple[int, bytes, Dict[str, str]]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=_http_timeout()) as resp:
        body = resp.read()
        return resp.status, body, dict(resp.headers)


def _load_blob_bytes() -> bytes:
    # Priority: explicit file
    p = env("ANIMICA_BLOB_PATH")
    if p:
        path = Path(p)
        if not path.is_file():
            pytest.skip(f"ANIMICA_BLOB_PATH set but file not found: {p}")
        return path.read_bytes()

    # Fallback: use repo fixture if present
    for candidate in (
        Path("da/fixtures/blob_small.bin"),
        Path("da/fixtures/blob_medium.bin"),
    ):
        if candidate.is_file():
            return candidate.read_bytes()

    # Deterministic synthetic 4 KiB blob
    # (pseudo-random but stable: 0..4095 bytes cycling a simple function)
    return bytes((i * 31 + 7) % 256 for i in range(4096))


# -----------------------------------------------------------------------------
# Test
# -----------------------------------------------------------------------------

@pytest.mark.skipif(da_commit is None, reason="da.blob.commitment.commit not importable (in-repo DA lib missing)")
@pytest.mark.timeout(180)
def test_da_post_commitment_and_light_verify_roundtrip():
    base = _derive_base_url()
    ns = int(env("ANIMICA_DA_NS", "24") or "24")
    blob = _load_blob_bytes()

    # 1) POST /da/blob — try raw-octet with ?ns= and then JSON body fallback.
    post_url = f"{base}/da/blob?{urllib.parse.urlencode({'ns': ns})}"
    try:
        status, body, hdrs = _post_bytes(post_url, blob)
        if status >= 400:
            raise AssertionError(f"POST bytes returned HTTP {status}")
        # Expect JSON result
        try:
            res = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise AssertionError(f"POST /da/blob returned non-JSON body: {body[:100]!r}") from exc
    except Exception:
        # Fallback: JSON payload {"ns": int, "data": "0x.."}
        json_url = f"{base}/da/blob"
        status, body, hdrs = _post_json(json_url, {"ns": ns, "data": _hex(blob)})
        if status >= 400:
            raise AssertionError(f"POST json returned HTTP {status}: {body[:200]!r}")
        res = json.loads(body.decode("utf-8"))

    commitment_hex = _extract_commitment(res)
    assert commitment_hex, f"Could not extract commitment/root from response: {res!r}"

    # 2) Light verify by recomputing locally
    root_bytes, size, ns_out = da_commit(blob, ns)  # type: ignore[misc]
    assert size == len(blob), "Local commitment size mismatch"
    assert ns_out == ns, "Local commitment namespace mismatch"

    local_hex = _hex(root_bytes).lower()
    assert local_hex == commitment_hex, (
        f"Server commitment mismatch.\n server: {commitment_hex}\n  local: {local_hex}"
    )

    # 3) GET the blob back by commitment — try canonical path and a query fallback.
    get_url = f"{base}/da/blob/{commitment_hex}"
    try:
        status, got_body, _ = _get(get_url)
        if status >= 400 or not got_body:
            raise AssertionError(f"GET {get_url} failed with HTTP {status}")
    except Exception:
        # Fallback: /da/blob?commitment=0x...
        alt = f"{base}/da/blob?{urllib.parse.urlencode({'commitment': commitment_hex})}"
        status, got_body, _ = _get(alt)
        if status >= 400 or not got_body:
            raise AssertionError(f"GET {alt} failed with HTTP {status}")

    assert got_body == blob, "Fetched blob does not match posted bytes"

    # 4) Best-effort: GET a proof and sanity-check its root matches commitment.
    # We don't depend on exact schema; we only assert the root field equals the commitment.
    proof_ok = False
    for url in (
        f"{base}/da/blob/{commitment_hex}/proof",
        f"{base}/da/proof?{urllib.parse.urlencode({'commitment': commitment_hex})}",
    ):
        try:
            status, pbody, _ = _get(url)
            if status >= 400 or not pbody:
                continue
            # try JSON
            pobj = json.loads(pbody.decode("utf-8"))
            root = _extract_commitment(pobj)
            if root and root.lower() == commitment_hex:
                proof_ok = True
                break
        except Exception:
            continue

    # Proof endpoint might be disabled — don't fail the round-trip for that.
    if not proof_ok:
        pytest.skip("Proof endpoint not available or returned unexpected shape; blob post/get & commitment verified")

