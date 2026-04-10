"""
Tests for DA provider service.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

try:
    from fastapi.testclient import TestClient

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

if FASTAPI_AVAILABLE:
    from da.provider.service import ProviderService


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI not available")
def test_provider_service_health():
    """Test health check endpoint."""
    with tempfile.TemporaryDirectory() as tmpdir:
        service = ProviderService(storage_path=Path(tmpdir))
        client = TestClient(service.app)

        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI not available")
def test_provider_service_store_and_retrieve():
    """Test storing and retrieving blobs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        service = ProviderService(storage_path=Path(tmpdir))

        # Store a blob
        commitment = b"test_commitment_32_bytes_long!!"[:32]
        data = b"test blob data content"
        blob_path = service.store_blob(commitment, data)

        assert blob_path.exists()
        assert blob_path.read_bytes() == data

        # Retrieve via service method
        retrieved = service.get_blob(commitment)
        assert retrieved == data

        # Check existence
        assert service.has_blob(commitment)
        assert not service.has_blob(b"nonexistent_commitment_32bytes!!"[:32])


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI not available")
def test_provider_service_get_blob_http():
    """Test GET /blob/{commitment} endpoint."""
    with tempfile.TemporaryDirectory() as tmpdir:
        service = ProviderService(storage_path=Path(tmpdir))
        client = TestClient(service.app)

        # Store a blob
        commitment = b"test_commitment_32_bytes_long!!"[:32]
        data = b"test blob data content"
        service.store_blob(commitment, data)

        # Retrieve via HTTP
        commit_hex = commitment.hex()
        response = client.get(f"/blob/{commit_hex}")
        assert response.status_code == 200
        assert response.content == data

        # With 0x prefix
        response = client.get(f"/blob/0x{commit_hex}")
        assert response.status_code == 200
        assert response.content == data


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI not available")
def test_provider_service_head_blob():
    """Test HEAD /blob/{commitment} endpoint."""
    with tempfile.TemporaryDirectory() as tmpdir:
        service = ProviderService(storage_path=Path(tmpdir))
        client = TestClient(service.app)

        # Store a blob
        commitment = b"test_commitment_32_bytes_long!!"[:32]
        data = b"test blob data content"
        service.store_blob(commitment, data)

        # HEAD request
        commit_hex = commitment.hex()
        response = client.head(f"/blob/{commit_hex}")
        assert response.status_code == 200
        assert int(response.headers["Content-Length"]) == len(data)
        assert response.content == b""  # HEAD should not return body


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI not available")
def test_provider_service_not_found():
    """Test 404 for nonexistent blob."""
    with tempfile.TemporaryDirectory() as tmpdir:
        service = ProviderService(storage_path=Path(tmpdir))
        client = TestClient(service.app)

        # Request nonexistent blob
        commitment = b"nonexistent_commitment_32bytes!!"[:32]
        commit_hex = commitment.hex()
        response = client.get(f"/blob/{commit_hex}")
        assert response.status_code == 404


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI not available")
def test_provider_service_range_request():
    """Test partial content retrieval with Range header."""
    with tempfile.TemporaryDirectory() as tmpdir:
        service = ProviderService(storage_path=Path(tmpdir))
        client = TestClient(service.app)

        # Store a blob
        commitment = b"test_commitment_32_bytes_long!!"[:32]
        data = b"0123456789" * 10  # 100 bytes
        service.store_blob(commitment, data)

        # Request range
        commit_hex = commitment.hex()
        response = client.get(
            f"/blob/{commit_hex}", headers={"Range": "bytes=10-19"}
        )
        assert response.status_code == 206
        assert response.content == data[10:20]
        assert "Content-Range" in response.headers
        assert response.headers["Content-Range"] == f"bytes 10-19/{len(data)}"


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI not available")
def test_provider_service_authentication():
    """Test bearer token authentication."""
    with tempfile.TemporaryDirectory() as tmpdir:
        auth_token = "test_secret_token"
        service = ProviderService(storage_path=Path(tmpdir), auth_token=auth_token)
        client = TestClient(service.app)

        # Store a blob
        commitment = b"test_commitment_32_bytes_long!!"[:32]
        data = b"test blob data"
        service.store_blob(commitment, data)

        commit_hex = commitment.hex()

        # Request without auth should fail
        response = client.get(f"/blob/{commit_hex}")
        assert response.status_code == 401

        # Request with wrong token should fail
        response = client.get(
            f"/blob/{commit_hex}", headers={"Authorization": "Bearer wrong_token"}
        )
        assert response.status_code == 401

        # Request with correct token should succeed
        response = client.get(
            f"/blob/{commit_hex}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        assert response.content == data


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI not available")
def test_provider_service_rate_limiting():
    """Test rate limiting."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Very low rate limit for testing
        service = ProviderService(storage_path=Path(tmpdir), rate_limit_rps=2)
        client = TestClient(service.app)

        # Store a blob
        commitment = b"test_commitment_32_bytes_long!!"[:32]
        data = b"test blob data"
        service.store_blob(commitment, data)

        commit_hex = commitment.hex()

        # First 2 requests should succeed
        for _ in range(2):
            response = client.get(f"/blob/{commit_hex}")
            assert response.status_code == 200

        # Third request should be rate limited
        response = client.get(f"/blob/{commit_hex}")
        assert response.status_code == 429


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI not available")
def test_provider_service_invalid_commitment():
    """Test invalid commitment format."""
    with tempfile.TemporaryDirectory() as tmpdir:
        service = ProviderService(storage_path=Path(tmpdir))
        client = TestClient(service.app)

        # Too short
        response = client.get("/blob/abcd")
        assert response.status_code == 400

        # Too long
        response = client.get("/blob/" + "a" * 100)
        assert response.status_code == 400
