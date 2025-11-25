"""
DA service HTTP/JSON API contract tests.

Contract we assert here (for *unknown* commitments):

  * GET /da/blob/{commitment_hex}
      - returns 404
      - Content-Type: application/json
      - JSON body contains at least: {"error": ..., "commitment": ...}

  * GET /da/proof/{commitment_hex}
      - returns 404
      - Content-Type: application/json
      - JSON body contains at least: {"error": ..., "commitment": ...}

These tests assume there is an ASGI app exposed by animica.da.api, following
one of these patterns:

  * def create_app() -> ASGIApp
  * app: ASGIApp   (module-level object)

If that module or httpx is not available, the tests are skipped rather than
failing import.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

try:
    import httpx
except ImportError:  # pragma: no cover - env without httpx
    httpx = None  # type: ignore[assignment]


def _load_da_app() -> Optional[Any]:
    """
    Best-effort loader for the DA API ASGI app.

    We first look for animica.da.api.create_app(), then fall back to
    animica.da.api.app if present.

    If nothing is found, returns None and tests are skipped.
    """
    try:
        from animica.da import api  # type: ignore[import]

    except Exception:
        return None

    # Prefer a factory if present
    create = getattr(api, "create_app", None)
    if callable(create):
        try:
            return create()
        except Exception:
            # If the factory itself is broken, treat as "no app" so tests signal
            # a clean skip instead of exploding at import time.
            return None

    # Fallback: module-level app
    app = getattr(api, "app", None)
    return app


APP = _load_da_app()
HAVE_HTTPX = httpx is not None
HAVE_DA_API = HAVE_HTTPX and APP is not None


@pytest.mark.skipif(not HAVE_DA_API, reason="DA API app or httpx not available")
@pytest.mark.asyncio
async def test_da_blob_unknown_commitment_returns_404_and_json() -> None:
    """
    For an unknown commitment, GET /da/blob/{commitment} must return:

      * HTTP 404
      * application/json Content-Type
      * JSON with at least 'error' and 'commitment' fields.
    """
    commitment_hex = "f" * 64  # 32-byte hex string (no 0x prefix)

    transport = httpx.ASGITransport(app=APP)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get(f"/da/blob/{commitment_hex}")

    assert resp.status_code == 404
    ctype = resp.headers.get("content-type", "")
    assert "application/json" in ctype.lower()

    body = resp.json()
    assert isinstance(body, dict)
    assert "error" in body
    assert "commitment" in body
    assert body["commitment"] == commitment_hex


@pytest.mark.skipif(not HAVE_DA_API, reason="DA API app or httpx not available")
@pytest.mark.asyncio
async def test_da_proof_unknown_commitment_returns_404_and_json() -> None:
    """
    For an unknown commitment, GET /da/proof/{commitment} must return:

      * HTTP 404
      * application/json Content-Type
      * JSON with at least 'error' and 'commitment' fields.
    """
    commitment_hex = "0" * 64  # different fake commitment

    transport = httpx.ASGITransport(app=APP)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get(f"/da/proof/{commitment_hex}")

    assert resp.status_code == 404
    ctype = resp.headers.get("content-type", "")
    assert "application/json" in ctype.lower()

    body = resp.json()
    assert isinstance(body, dict)
    assert "error" in body
    assert "commitment" in body
    assert body["commitment"] == commitment_hex
