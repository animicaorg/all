from __future__ import annotations

import json
from typing import Any, Dict

import pytest

errors_mod = pytest.importorskip(
    "mempool.errors", reason="mempool.errors module not found"
)

MempoolError = errors_mod.MempoolError
FeeTooLow = errors_mod.FeeTooLow
NonceGap = errors_mod.NonceGap
Oversize = errors_mod.Oversize
ReplacementError = errors_mod.ReplacementError
DoSError = errors_mod.DoSError
MempoolErrorCode = errors_mod.MempoolErrorCode
err_payload = errors_mod.err_payload


def _assert_serializable(err: MempoolError) -> Dict[str, Any]:
    """Helper: to_dict() has the right shape and is JSON-serializable."""
    data = err.to_dict()
    assert isinstance(data, dict)
    assert set(data.keys()) == {"code", "reason", "message", "context"}

    # Basic types
    assert isinstance(data["code"], int)
    assert isinstance(data["reason"], str)
    assert isinstance(data["message"], str)
    assert isinstance(data["context"], dict)

    # Must be JSON-serializable
    json.dumps(data)
    return data


def test_fee_too_low_serialization() -> None:
    err = FeeTooLow(
        offered_gas_price_wei=100,
        min_required_wei=200,
        tx_hash="0xdeadbeef",
        sender="0xsender",
    )

    data = _assert_serializable(err)

    assert data["code"] == MempoolErrorCode.FEE_TOO_LOW
    assert data["reason"] == "fee_too_low"
    assert "gwei" in data["message"]

    ctx = data["context"]
    assert ctx["offered_gas_price_wei"] == 100
    assert ctx["min_required_wei"] == 200
    assert ctx["tx_hash"] == "0xdeadbeef"
    assert ctx["sender"] == "0xsender"


def test_nonce_gap_serialization() -> None:
    err = NonceGap(
        expected_nonce=10,
        got_nonce=7,
        sender="0xabc",
        tx_hash="0x123",
    )

    data = _assert_serializable(err)

    assert data["code"] == MempoolErrorCode.NONCE_GAP
    assert data["reason"] == "nonce_gap"
    assert "nonce gap" in data["message"]

    ctx = data["context"]
    assert ctx["expected_nonce"] == 10
    assert ctx["got_nonce"] == 7
    assert ctx["sender"] == "0xabc"
    assert ctx["tx_hash"] == "0x123"


def test_oversize_serialization() -> None:
    err = Oversize(
        size_bytes=4096,
        max_bytes=2048,
        tx_hash="0xoversize",
        sender="0xsender",
    )

    data = _assert_serializable(err)

    assert data["code"] == MempoolErrorCode.OVERSIZE
    assert data["reason"] == "tx_too_large"
    assert "large" in data["message"] or "too large" in data["message"]

    ctx = data["context"]
    assert ctx["size_bytes"] == 4096
    assert ctx["max_bytes"] == 2048
    assert ctx["tx_hash"] == "0xoversize"
    assert ctx["sender"] == "0xsender"


def test_replacement_error_serialization() -> None:
    err = ReplacementError(
        required_bump=1.20,
        current_effective_gas_price_wei=1_000,
        offered_effective_gas_price_wei=1_100,
        tx_hash_old="0xold",
        tx_hash_new="0xnew",
        sender="0xrbf",
    )

    data = _assert_serializable(err)

    assert data["code"] == MempoolErrorCode.REPLACEMENT
    assert data["reason"] == "replacement_underpriced"
    assert "replacement" in data["message"] or "underpriced" in data["message"]

    ctx = data["context"]
    assert ctx["required_bump"] == pytest.approx(1.20, rel=1e-9)
    assert ctx["current_effective_gas_price_wei"] == 1_000
    assert ctx["offered_effective_gas_price_wei"] == 1_100
    assert ctx["tx_hash_old"] == "0xold"
    assert ctx["tx_hash_new"] == "0xnew"
    assert ctx["sender"] == "0xrbf"


def test_dos_error_serialization_and_custom_reason() -> None:
    # Default reason
    err_default = DoSError(
        "rate limit exceeded",
        peer_id="peer-1",
        remote_addr="192.0.2.1",
    )

    data_default = _assert_serializable(err_default)
    assert data_default["code"] == MempoolErrorCode.DOS
    assert data_default["reason"] == "dos_violation"
    assert "rate limit" in data_default["message"]

    ctx = data_default["context"]
    assert ctx["peer_id"] == "peer-1"
    assert ctx["remote_addr"] == "192.0.2.1"

    # Custom reason + extra data
    err_custom = DoSError(
        "malformed burst",
        peer_id="peer-2",
        remote_addr="198.51.100.7",
        reason="malformed_burst",
        extra={"burst_size": 42},
    )

    data_custom = _assert_serializable(err_custom)
    assert data_custom["code"] == MempoolErrorCode.DOS
    assert data_custom["reason"] == "malformed_burst"
    assert "malformed" in data_custom["message"]

    ctx2 = data_custom["context"]
    assert ctx2["peer_id"] == "peer-2"
    assert ctx2["remote_addr"] == "198.51.100.7"
    assert ctx2["burst_size"] == 42


def test_err_payload_matches_to_dict() -> None:
    """
    err_payload() is the helper used when embedding mempool errors into
    JSON-RPC error.data. It should be a thin wrapper around .to_dict().
    """
    err = Oversize(size_bytes=3000, max_bytes=1024)

    d1 = err.to_dict()
    d2 = err_payload(err)

    assert d1 == d2
    # And still JSON-serializable.
    json.dumps(d2)
