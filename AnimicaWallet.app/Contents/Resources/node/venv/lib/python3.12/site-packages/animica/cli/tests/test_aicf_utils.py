"""
Unit tests for AICF CLI utilities.
"""

from __future__ import annotations

import pytest

from animica.cli import aicf_utils


class TestNormalizeRpcUrl:
    """Tests for URL normalization."""
    
    def test_adds_rpc_to_base_url(self):
        """Should append /rpc to base URLs."""
        assert aicf_utils.normalize_rpc_url("http://127.0.0.1:8545") == "http://127.0.0.1:8545/rpc"
    
    def test_preserves_existing_rpc(self):
        """Should not double-append /rpc."""
        assert aicf_utils.normalize_rpc_url("http://127.0.0.1:8545/rpc") == "http://127.0.0.1:8545/rpc"
    
    def test_removes_trailing_slash(self):
        """Should handle trailing slashes correctly."""
        assert aicf_utils.normalize_rpc_url("http://127.0.0.1:8545/") == "http://127.0.0.1:8545/rpc"
        assert aicf_utils.normalize_rpc_url("http://127.0.0.1:8545/rpc/") == "http://127.0.0.1:8545/rpc"
    
    def test_adds_http_scheme(self):
        """Should add http:// if missing."""
        assert aicf_utils.normalize_rpc_url("127.0.0.1:8545") == "http://127.0.0.1:8545/rpc"
    
    def test_handles_https(self):
        """Should work with HTTPS."""
        assert aicf_utils.normalize_rpc_url("https://mainnet.animica.org") == "https://mainnet.animica.org/rpc"
    
    def test_replaces_wrong_path(self):
        """Should replace incorrect paths with /rpc."""
        assert aicf_utils.normalize_rpc_url("http://127.0.0.1:8545/api") == "http://127.0.0.1:8545/rpc"
    
    def test_empty_url_returns_default(self):
        """Should return default URL for empty input."""
        assert aicf_utils.normalize_rpc_url("") == "http://127.0.0.1:8545/rpc"
        assert aicf_utils.normalize_rpc_url(None) == "http://127.0.0.1:8545/rpc"


class TestGetRpcUrl:
    """Tests for RPC URL resolution."""
    
    def test_uses_override(self, monkeypatch: pytest.MonkeyPatch):
        """Override parameter should take precedence."""
        monkeypatch.setenv("ANIMICA_RPC_URL", "http://example.com:8545")
        url = aicf_utils.get_rpc_url(override="http://override.com:9999")
        assert url == "http://override.com:9999/rpc"
    
    def test_uses_environment(self, monkeypatch: pytest.MonkeyPatch):
        """Should use ANIMICA_RPC_URL env var."""
        monkeypatch.setenv("ANIMICA_RPC_URL", "http://testnet.animica.org:8545")
        url = aicf_utils.get_rpc_url()
        assert url == "http://testnet.animica.org:8545/rpc"
    
    def test_uses_default(self, monkeypatch: pytest.MonkeyPatch):
        """Should use default if no override or env var."""
        monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
        url = aicf_utils.get_rpc_url()
        assert url == "http://127.0.0.1:8545/rpc"


class TestSafeJsonEncode:
    """Tests for safe JSON encoding."""
    
    def test_handles_normal_values(self):
        """Should encode normal values."""
        obj = {"key": "value", "num": 42, "nested": {"arr": [1, 2, 3]}}
        result = aicf_utils.safe_json_encode(obj)
        assert '"key": "value"' in result
        assert '"num": 42' in result
    
    def test_converts_large_int_to_string(self):
        """Should convert large integers to strings."""
        obj = {"balance": 9007199254740992}  # > 2^53
        result = aicf_utils.safe_json_encode(obj)
        assert '"9007199254740992"' in result
    
    def test_handles_bytes(self):
        """Should convert bytes to hex."""
        obj = {"hash": b'\x01\x02\x03\x04'}
        result = aicf_utils.safe_json_encode(obj)
        assert '"0x01020304"' in result


class TestCreateRpcSession:
    """Tests for RPC session creation."""
    
    def test_creates_session_with_retries(self):
        """Should create a session with retry adapter."""
        session = aicf_utils.create_rpc_session(timeout=30, retries=3)
        assert session is not None
        # Verify adapter is attached
        assert "http://" in session.adapters
        assert "https://" in session.adapters


# Integration tests for rpc_call and rpc_doctor would require a running RPC server
# or mocking requests, which is beyond unit testing scope. These should be in
# integration tests instead.
