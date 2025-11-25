# -*- coding: utf-8 -*-
"""
Integration: studio-services — verify contract source path → matched on-chain code.

This test exercises the verification flow exposed by studio-services:
  1) Reads a Python-contract source + manifest (defaults to studio-services fixtures).
  2) POSTs to /verify with {source, manifest, address|txHash}.
  3) Polls verification status until success, then asserts sane fields (codeHash hex, status).

It is intentionally tolerant to minor API shape differences across deployments.
If no compatible endpoint or required environment is present, the test SKIPS.

Environment
-----------
• RUN_INTEGRATION_TESTS=1                    — enable integration tests
• STUDIO_SERVICES_URL or ANIMICA_SERVICES_URL
                                             — base URL for studio-services (REQUIRED)
• SERVICES_API_KEY or ANIMICA_SERVICES_API_KEY
                                             — optional API key header (X-API-Key)
• VERIFY_ADDRESS or VERIFY_TXHASH            — target deployed contract or deployment tx (RECOMMENDED)
• VERIFY_SOURCE                              — path to contract source (default fixtures/counter)
• VERIFY_MANIFEST                            — path to manifest JSON (default fixtures/counter)
• ANIMICA_HTTP_TIMEOUT                       — per-call timeout seconds (default: 8)
• ANIMICA_RESULT_WAIT_SECS                   — poll window seconds (default: 240)
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

import pytest

from tests.integration import env  # gating helper


# --------------------------------- helpers -----------------------------------

def _services_base() -> Optional[str]:
    base = env("STUDIO_SERVICES_URL") or env("ANIMICA_SERVICES_URL")
    if base:
        return base.rstrip("/")
    return None


def _timeout() -> float:
    try:
        return float(env("ANIMICA_HTTP_TIMEOUT", "8"))
    except Exception:
        return 8.0


def _wait_secs() -> float:
    try:
        return float(env("ANIMICA_RESULT_WAIT_SECS", "240"))
    except Exception:
        return 240.0


def _api_key() -> Optional[str]:
    return env("SERVICES_API_KEY") or env("ANIMICA_SERVICES_API_KEY")


def _headers() -> Dict[str, str]:
    h = {"Accept": "application/json"}
    key = _api_key()
    if key:
        h["X-API-Key"] = key
        # Some deployments also accept Bearer
        h["Authorization"] = f"Bearer {key}"
    return h


def _read_fixture(path_env: str, default_rel: str, *, binary: bool = False) -> Tuple[str, bytes]:
    # Resolve env or default path in repo
    p = env(path_env) or default_rel
    pp = pathlib.Path(p)
    if not pp.is_file():
        pytest.skip(f"Fixture not found: {pp} (override via ${path_env})")
    data = pp.read_bytes()
    return pp.name, data


def _post_json(url: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **_headers()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            raw = resp.read()
        doc = json.loads(raw.decode("utf-8"))
        if isinstance(doc, dict):
            return doc
    except Exception:
        return None
    return None


def _get_json(url: str) -> Optional[Dict[str, Any]]:
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            raw = resp.read()
        doc = json.loads(raw.decode("utf-8"))
        if isinstance(doc, dict):
            return doc
    except Exception:
        return None
    return None


def _is_hex(s: Any) -> bool:
    if not isinstance(s, str) or not s.startswith("0x") or len(s) < 3:
        return False
    try:
        int(s[2:], 16)
        return True
    except Exception:
        return False


def _status_ok(doc: Dict[str, Any]) -> bool:
    # Accept a variety of success markers
    status = (doc.get("status") or doc.get("state") or "").lower()
    verified = doc.get("verified") or doc.get("match") or doc.get("ok")
    reason = (doc.get("reason") or doc.get("error") or "")
    return bool(verified) or status in ("matched", "success", "ok", "done") and not reason


def _extract_job_id(doc: Dict[str, Any]) -> Optional[str]:
    for k in ("jobId", "job_id", "id"):
        v = doc.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _join(base: str, path: str) -> str:
    return urllib.parse.urljoin(base + "/", path.lstrip("/"))


# ----------------------------------- test ------------------------------------

@pytest.mark.timeout(900)
def test_services_verify_cycle_contract_source_path_matches_onchain():
    base = _services_base()
    if not base:
        pytest.skip("Set STUDIO_SERVICES_URL (or ANIMICA_SERVICES_URL) to run studio-services verification test.")

    # Target to verify: prefer explicit address/txHash from environment.
    address = env("VERIFY_ADDRESS")
    tx_hash = env("VERIFY_TXHASH")
    if not address and not tx_hash:
        pytest.skip("Provide VERIFY_ADDRESS or VERIFY_TXHASH for a deployed contract to verify.")

    # Read fixtures (defaults to studio-services bundled counter).
    src_name, src_bytes = _read_fixture(
        "VERIFY_SOURCE",
        "studio-services/fixtures/counter/contract.py",
    )
    _, manifest_bytes = _read_fixture(
        "VERIFY_MANIFEST",
        "studio-services/fixtures/counter/manifest.json",
    )
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except Exception as exc:
        pytest.skip(f"Manifest could not be parsed as JSON: {exc}")

    # Compose flexible payloads accepted by different deployments.
    src_text = src_bytes.decode("utf-8", errors="strict")
    src_b64 = base64.b64encode(src_bytes).decode("ascii")

    candidate_payloads = []

    # Canonical (recommended by our routers/models):
    payload1 = {
        "source": {"filename": src_name, "content": src_text},
        "manifest": manifest,
    }
    if address:
        payload1["address"] = address
    if tx_hash:
        payload1["txHash"] = tx_hash
    candidate_payloads.append(payload1)

    # Minimal variant (string source):
    payload2 = {
        "source": src_text,
        "manifest": manifest,
    }
    if address:
        payload2["address"] = address
    if tx_hash:
        payload2["txHash"] = tx_hash
    candidate_payloads.append(payload2)

    # Base64 variant:
    payload3 = {
        "source_b64": src_b64,
        "filename": src_name,
        "manifest": manifest,
    }
    if address:
        payload3["address"] = address
    if tx_hash:
        payload3["txHash"] = tx_hash
    candidate_payloads.append(payload3)

    # Try POST /verify at common mounts.
    verify_paths = ["/verify", "/api/verify", "/v1/verify"]
    submit_doc: Optional[Dict[str, Any]] = None
    used_path: Optional[str] = None

    for vp in verify_paths:
        url = _join(base, vp)
        for payload in candidate_payloads:
            submit_doc = _post_json(url, payload)
            if isinstance(submit_doc, dict):
                used_path = vp
                break
        if submit_doc:
            break

    if not submit_doc:
        pytest.skip("studio-services /verify endpoint not reachable at common paths or payload shapes.")

    # Either immediate result or queued job.
    job_id = _extract_job_id(submit_doc)
    result = submit_doc

    # Poll status if needed.
    deadline = time.time() + _wait_secs()

    def _fetch_status() -> Optional[Dict[str, Any]]:
        # Prefer address-specific GET; fall back to txHash; finally by jobId if supported.
        if address:
            for p in ("/verify/", "/api/verify/", "/v1/verify/"):
                doc = _get_json(_join(base, f"{p}{address}"))
                if doc:
                    return doc
        if tx_hash:
            for p in ("/verify/", "/api/verify/", "/v1/verify/"):
                doc = _get_json(_join(base, f"{p}{tx_hash}"))
                if doc:
                    return doc
        if job_id:
            for p in ("/verify/status/", "/api/verify/status/", "/v1/verify/status/"):
                doc = _get_json(_join(base, f"{p}{job_id}"))
                if doc:
                    return doc
        return None

    # If first response already has status/verified flag, keep it; else poll.
    if not _status_ok(result):
        while time.time() < deadline:
            doc = _fetch_status()
            if doc:
                result = doc
                if _status_ok(result):
                    break
            time.sleep(1.0)

    assert _status_ok(result), f"Verification did not succeed: {json.dumps(result, indent=2)}"

    # Sane fields
    code_hash = result.get("codeHash") or result.get("code_hash") or result.get("artifactHash") or result.get("artifact_hash")
    if code_hash is not None:
        assert _is_hex(code_hash), f"codeHash is not hex-like: {code_hash!r}"

    # Echoed/normalized source filename or path (best-effort)
    src_path = result.get("sourcePath") or result.get("source") or result.get("filename")
    if isinstance(src_path, str):
        assert src_name in src_path or src_path.endswith(".py"), f"Unexpected source path in result: {src_path!r}"

    # If the server reports an address/txHash back, it should match our inputs (when provided).
    if address and isinstance(result.get("address"), str):
        assert result["address"].lower() == address.lower(), "Verification result address mismatch."
    if tx_hash and isinstance(result.get("txHash"), str):
        assert result["txHash"].lower() == tx_hash.lower(), "Verification result txHash mismatch."

