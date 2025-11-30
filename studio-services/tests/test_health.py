from __future__ import annotations

import re

import pytest

# These tests exercise the health endpoints mounted by the app:
#   - GET /healthz
#   - GET /readyz
#   - GET /version
#
# They are intentionally tolerant about exact payload shapes (different
# deployments may include extra diagnostic fields) but still assert the
# key invariants: HTTP 200, JSON content, and expected top-level keys/types.


@pytest.mark.asyncio
async def test_healthz_ok(aclient):
    resp = await aclient.get("/healthz")
    assert resp.status_code == 200
    # Content-Type is JSON (allow charset suffix)
    assert "application/json" in resp.headers.get("content-type", "").lower()

    data = resp.json()
    assert isinstance(data, dict)
    # Accept either {"ok": true} or {"status": "ok"} (or both)
    assert ("ok" in data and bool(data["ok"]) is True) or (
        data.get("status", "").lower() == "ok"
    )


@pytest.mark.asyncio
async def test_readyz_ok(aclient):
    resp = await aclient.get("/readyz")
    assert resp.status_code == 200
    assert "application/json" in resp.headers.get("content-type", "").lower()

    data = resp.json()
    assert isinstance(data, dict)
    # Accept either {"ready": true} or {"status": "ready"} plus optional checks
    assert (
        ("ready" in data and bool(data["ready"]) is True)
        or (data.get("status", "").lower() == "ready")
        or (data.get("status", "").lower() == "ok")
    )
    # If a dependencies section is present, it should be a mapping
    deps = data.get("dependencies") or data.get("checks") or {}
    assert isinstance(deps, dict)


@pytest.mark.asyncio
async def test_version_endpoint(aclient):
    resp = await aclient.get("/version")
    assert resp.status_code == 200
    assert "application/json" in resp.headers.get("content-type", "").lower()

    data = resp.json()
    assert isinstance(data, dict)

    # version should be a non-empty string, ideally semver-ish
    version = data.get("version") or data.get("appVersion") or ""
    assert isinstance(version, str) and version.strip()
    # Best-effort semver prefix check (tolerate suffixes like +meta or -dirty)
    assert re.match(r"^\d+\.\d+\.\d+", version) or version.lower() in {
        "dev",
        "test",
        "unknown",
    }

    # Optional git describe/hash fields are strings if present
    if "git" in data:
        assert isinstance(data["git"], str)
    if "gitDescribe" in data:
        assert isinstance(data["gitDescribe"], str)
    if "name" in data:
        assert isinstance(data["name"], str) and data["name"].strip()
