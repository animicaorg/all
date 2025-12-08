"""
Test that /readyz endpoint properly serializes bytes to JSON-safe values.

This test ensures the fix for the "TypeError: Object of type bytes is not JSON
serializable" error that occurred when the readyz endpoint tried to return
block hashes as raw bytes instead of hex strings.
"""

from __future__ import annotations

import pytest

from rpc.tests import new_test_client


def test_readyz_returns_json_serializable_response():
    """Test that /readyz endpoint returns valid JSON with no bytes."""
    client, cfg, tmpdir = new_test_client()
    
    # Make a GET request to /readyz
    response = client.get("/readyz")
    
    # Should return 200 or 503, but never 500 (internal server error)
    assert response.status_code in (200, 503), (
        f"Expected 200 or 503, got {response.status_code}: {response.text}"
    )
    
    # Should be valid JSON
    data = response.json()
    assert isinstance(data, dict), f"Expected dict, got {type(data)}"
    
    # Should have 'ready' and 'details' keys
    assert "ready" in data, f"Missing 'ready' key in response: {data}"
    assert "details" in data, f"Missing 'details' key in response: {data}"
    
    # Verify all values are JSON-serializable (no bytes)
    details = data["details"]
    assert isinstance(details, dict), f"Expected details to be dict, got {type(details)}"
    
    for key, value in details.items():
        # None, str, int, float, list, dict are all JSON-serializable
        # bytes is NOT JSON-serializable
        assert not isinstance(value, bytes), (
            f"Found bytes value for key '{key}': {value!r}. "
            "All values must be JSON-serializable (str, int, None, etc.)"
        )
        
        # If hash is present, it should be a hex string starting with "0x" or None
        if key == "hash" and value is not None:
            assert isinstance(value, str), f"hash must be str or None, got {type(value)}"
            assert value.startswith("0x"), f"hash must start with '0x', got {value}"


def test_readyz_with_genesis_block():
    """Test /readyz when DB has a genesis block with a hash."""
    client, cfg, tmpdir = new_test_client()
    
    # The test client bootstraps genesis automatically via deps.ensure_started(cfg)
    # which calls _maybe_bootstrap_genesis
    
    # Make request to /readyz
    response = client.get("/readyz")
    
    # Should succeed (200) since genesis is bootstrapped
    assert response.status_code == 200, (
        f"Expected 200 after genesis bootstrap, got {response.status_code}: {response.text}"
    )
    
    data = response.json()
    assert data["ready"] is True, f"Expected ready=True with genesis, got {data}"
    
    # Check that details contains valid fields
    details = data["details"]
    
    # Height should be 0 for genesis
    if details.get("height") is not None:
        assert isinstance(details["height"], int), "height must be int or None"
        assert details["height"] >= 0, "height must be non-negative"
    
    # Hash should be hex string if present
    if details.get("hash") is not None:
        assert isinstance(details["hash"], str), "hash must be str or None"
        assert details["hash"].startswith("0x"), f"hash must be hex string, got {details['hash']}"
        # Should be 32 bytes = 64 hex chars + "0x" prefix = 66 chars
        assert len(details["hash"]) == 66, (
            f"hash should be 66 chars (0x + 64 hex), got {len(details['hash'])}"
        )
    
    # DB path should be present
    assert "db" in details, "db path should be in details"
    assert isinstance(details["db"], str), "db must be string"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
