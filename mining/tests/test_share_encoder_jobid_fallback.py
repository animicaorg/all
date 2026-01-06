"""Test share encoder generates fallback jobId when missing."""
import pytest

from mining.share_submitter import _default_share_encoder


def test_share_encoder_preserves_existing_jobid():
    """Share encoder preserves existing jobId."""
    share = {
        "jobId": "explicit-job-123",
        "header": {"height": 100},
        "nonce": "0x1234567890abcdef",
        "proof": {"type": "hashshare", "work": 1000},
    }
    
    payload = _default_share_encoder(share)
    
    assert payload["jobId"] == "explicit-job-123"
    assert "header" in payload
    assert "nonce" in payload
    assert "proof" in payload


def test_share_encoder_generates_fallback_jobid_from_nonce_hex():
    """Share encoder generates fallback jobId when missing (hex nonce)."""
    share = {
        # No jobId
        "header": {"chainId": 1},
        "nonce": "0x1234567890abcdef",
        "proof": {"type": "hashshare", "work": 1000},
        "height": 42,
    }
    
    payload = _default_share_encoder(share)
    
    # Should generate auto-jobId with format: auto-{height}-{nonce_prefix}-{timestamp}
    assert "jobId" in payload
    assert payload["jobId"].startswith("auto-42-12345678-")
    assert "header" in payload
    assert "nonce" in payload
    assert "proof" in payload


def test_share_encoder_generates_fallback_jobid_from_nonce_int():
    """Share encoder generates fallback jobId when missing (int nonce)."""
    share = {
        # No jobId
        "header": {"chainId": 1},
        "nonce": 0x1234567890abcdef,  # int nonce
        "proof": {"type": "hashshare", "work": 1000},
        "height": 42,
    }
    
    payload = _default_share_encoder(share)
    
    # Should generate auto-jobId
    assert "jobId" in payload
    assert payload["jobId"].startswith("auto-42-12345678-")


def test_share_encoder_generates_fallback_jobid_from_nonce_bytes():
    """Share encoder generates fallback jobId when missing (bytes nonce)."""
    share = {
        # No jobId
        "header": {"chainId": 1},
        "nonce": bytes.fromhex("1234567890abcdef"),  # bytes nonce
        "proof": {"type": "hashshare", "work": 1000},
        "height": 42,
    }
    
    payload = _default_share_encoder(share)
    
    # Should generate auto-jobId
    assert "jobId" in payload
    assert payload["jobId"].startswith("auto-42-12345678-")


def test_share_encoder_generates_fallback_jobid_no_height():
    """Share encoder generates fallback jobId when height is missing."""
    share = {
        # No jobId, no height at root level
        "header": {"chainId": 1},
        "nonce": "0xabcdef1234567890",
        "proof": {"type": "hashshare", "work": 1000},
    }
    
    payload = _default_share_encoder(share)
    
    # Should generate auto-jobId with height=0
    assert "jobId" in payload
    assert payload["jobId"].startswith("auto-0-abcdef12-")


def test_share_encoder_uses_job_id_alias():
    """Share encoder recognizes job_id and job aliases."""
    share1 = {
        "job_id": "snake-case-job",
        "header": {"height": 100},
        "nonce": "0x123",
        "proof": {"type": "hashshare"},
    }
    
    payload1 = _default_share_encoder(share1)
    assert payload1["jobId"] == "snake-case-job"
    
    share2 = {
        "job": "short-job",
        "header": {"height": 100},
        "nonce": "0x123",
        "proof": {"type": "hashshare"},
    }
    
    payload2 = _default_share_encoder(share2)
    assert payload2["jobId"] == "short-job"


def test_share_encoder_fallback_jobid_uniqueness():
    """Fallback jobIds should be unique for different shares."""
    share1 = {
        "header": {"height": 100},
        "nonce": "0x1111111111111111",
        "proof": {"type": "hashshare"},
        "height": 10,
    }
    
    share2 = {
        "header": {"height": 100},
        "nonce": "0x2222222222222222",
        "proof": {"type": "hashshare"},
        "height": 10,
    }
    
    payload1 = _default_share_encoder(share1)
    payload2 = _default_share_encoder(share2)
    
    # Different nonces should produce different jobIds
    assert payload1["jobId"] != payload2["jobId"]
    assert "auto-10-11111111-" in payload1["jobId"]
    assert "auto-10-22222222-" in payload2["jobId"]


def test_share_encoder_raises_on_missing_header():
    """Share encoder raises ValueError when header is missing."""
    share = {
        # No header
        "nonce": "0x123",
        "proof": {"type": "hashshare"},
    }
    
    with pytest.raises(ValueError, match="missing 'header'"):
        _default_share_encoder(share)


def test_share_encoder_raises_on_missing_nonce():
    """Share encoder raises ValueError when nonce is missing."""
    share = {
        "header": {"height": 100},
        # No nonce
        "proof": {"type": "hashshare"},
    }
    
    with pytest.raises(ValueError, match="missing 'nonce'"):
        _default_share_encoder(share)


def test_share_encoder_raises_on_missing_proof():
    """Share encoder raises ValueError when proof is missing."""
    share = {
        "header": {"height": 100},
        "nonce": "0x123",
        # No proof
    }
    
    with pytest.raises(ValueError, match="missing 'proof'"):
        _default_share_encoder(share)
