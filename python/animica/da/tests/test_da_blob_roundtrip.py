from __future__ import annotations

import pathlib
import sys
from typing import Any, Optional

import pytest

# Ensure the local "python/animica" package is importable when running tests
# from the repo root.
THIS_FILE = pathlib.Path(__file__).resolve()
PYTHON_ROOT = THIS_FILE.parents[3]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

try:
    import httpx
except ImportError:  # pragma: no cover - env without httpx
    httpx = None  # type: ignore[assignment]

try:
    from da.constants import MAX_BLOB_BYTES
    from da.retrieval.service import RetrievalService
except Exception:  # pragma: no cover
    MAX_BLOB_BYTES = 0
    RetrievalService = None  # type: ignore[assignment]

try:
    from animica.da import api as da_api  # type: ignore[import]
except Exception:  # pragma: no cover
    da_api = None  # type: ignore[assignment]

HAVE_HTTPX = httpx is not None
HAVE_DA_API = HAVE_HTTPX and da_api is not None and RetrievalService is not None


def _build_app(tmpdir) -> Optional[Any]:
    if not HAVE_DA_API:
        return None
    try:
        svc = RetrievalService(store_root=tmpdir)
        return da_api.create_app(service=svc)
    except Exception:
        return None


def _make_blob(length: int) -> bytes:
    return bytes((i % 251) for i in range(length))


@pytest.mark.skipif(not HAVE_DA_API, reason="DA API app or httpx not available")
@pytest.mark.anyio
@pytest.mark.parametrize("blob_size", [17, MAX_BLOB_BYTES])
async def test_da_blob_roundtrip_via_api(blob_size: int, tmp_path) -> None:
    app = _build_app(tmp_path)
    if app is None:
        pytest.skip("DA API app factory unavailable")

    data = _make_blob(blob_size)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        post_resp = await client.post(
            "/da/blob", params={"ns": 24}, content=data
        )
        assert post_resp.status_code == 200
        post_json = post_resp.json()
        commitment = post_json.get("commitment")
        assert isinstance(commitment, str)

        get_resp = await client.get(f"/da/blob/{commitment}")

    assert get_resp.status_code == 200
    assert get_resp.content == data
