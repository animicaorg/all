# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for execution.state.ena_state — ENA on-chain state module.

Tests cover:
- Request ID determinism
- Canonical serialization
- Fee split math
- State transitions (queued → completed, queued → failed, queued → expired)
- Policy rejection cases
- Model registry validation
- DA pointer / result storage limits
"""

from __future__ import annotations

import hashlib
import json
import pytest

from execution.state.ena_state import (
    ENARequest,
    ENAResult,
    ENAFeeSplit,
    ENAModelVersion,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_EXPIRED,
    DEFAULT_MAX_INPUT_BYTES,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_EXPIRY_BLOCKS,
    DEFAULT_ALLOWED_TASKS,
    DEFAULT_PROVIDER_BPS,
    DEFAULT_AICF_BPS,
    DEFAULT_TREASURY_BPS,
    get_ena_enabled,
    set_ena_enabled,
    get_max_input_bytes,
    set_max_input_bytes,
    get_max_output_bytes,
    set_max_output_bytes,
    get_expiry_blocks,
    set_expiry_blocks,
    get_allowed_tasks,
    set_allowed_tasks,
    get_active_model,
    set_active_model,
    register_model_version,
    get_model_version,
    is_model_allowed,
    create_request,
    get_request,
    get_request_status,
    set_request_status,
    submit_result,
    get_result,
    get_result_hash,
    finalize_fee_split,
    get_fee_split,
    fail_request,
    expire_request,
    verify_receipt,
    _derive_request_id,
    _compute_fee_split,
    _sha3_hex,
    safe_add,
    safe_sub,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class MockState:
    """Simple in-memory state for testing."""

    def __init__(self):
        self._d = {}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def put(self, key, value):
        self._d[key] = value


def _make_state():
    return MockState()


def _setup_state(state):
    """Set up a state with a registered model version and permissive policy."""
    set_ena_enabled(state, True)
    register_model_version(state, "ena-v0.9.0-h10000", "da:abc123", 10000, status="active")
    set_active_model(state, "ena-v0.9.0-h10000")


CREATOR = bytes.fromhex("ab" * 32)
CONTRACT = bytes.fromhex("cd" * 32)


# ---------------------------------------------------------------------------
# Request ID determinism
# ---------------------------------------------------------------------------


class TestRequestIdDeterminism:

    def test_same_inputs_same_id(self):
        """Identical inputs must produce identical request IDs."""
        creator = bytes(32)
        rid1 = _derive_request_id(creator, "model-v1", "classify", "abc", 100, 0)
        rid2 = _derive_request_id(creator, "model-v1", "classify", "abc", 100, 0)
        assert rid1 == rid2

    def test_different_height_different_id(self):
        """Different block heights must produce different request IDs."""
        creator = bytes(32)
        rid1 = _derive_request_id(creator, "model-v1", "classify", "abc", 100, 0)
        rid2 = _derive_request_id(creator, "model-v1", "classify", "abc", 101, 0)
        assert rid1 != rid2

    def test_different_nonce_different_id(self):
        """Different nonces must produce different request IDs."""
        creator = bytes(32)
        rid1 = _derive_request_id(creator, "model-v1", "classify", "abc", 100, 0)
        rid2 = _derive_request_id(creator, "model-v1", "classify", "abc", 100, 1)
        assert rid1 != rid2

    def test_different_model_different_id(self):
        creator = bytes(32)
        rid1 = _derive_request_id(creator, "model-v1", "classify", "abc", 100, 0)
        rid2 = _derive_request_id(creator, "model-v2", "classify", "abc", 100, 0)
        assert rid1 != rid2

    def test_different_task_different_id(self):
        creator = bytes(32)
        rid1 = _derive_request_id(creator, "model-v1", "classify", "abc", 100, 0)
        rid2 = _derive_request_id(creator, "model-v1", "embed", "abc", 100, 0)
        assert rid1 != rid2

    def test_id_starts_with_prefix(self):
        creator = bytes(32)
        rid = _derive_request_id(creator, "model-v1", "classify", "abc", 100, 0)
        assert rid.startswith("ena-")

    def test_id_is_hex(self):
        creator = bytes(32)
        rid = _derive_request_id(creator, "model-v1", "classify", "abc", 100, 0)
        # Should be "ena-" + 32 hex chars
        hex_part = rid[4:]
        assert len(hex_part) == 32
        int(hex_part, 16)  # Must be valid hex

    def test_id_is_deterministic_across_calls(self):
        """Verify 100 identical calls produce identical IDs."""
        creator = b"\x01" * 32
        expected = _derive_request_id(creator, "model-v1", "embed", "x" * 100, 5000, 3)
        for _ in range(100):
            assert _derive_request_id(creator, "model-v1", "embed", "x" * 100, 5000, 3) == expected


# ---------------------------------------------------------------------------
# Canonical serialization (sha3_hex)
# ---------------------------------------------------------------------------


class TestCanonicalSerialization:

    def test_sha3_hex_known_value(self):
        """sha3_256 of empty bytes is well-known."""
        result = _sha3_hex(b"")
        assert result == hashlib.sha3_256(b"").hexdigest()

    def test_sha3_hex_deterministic(self):
        data = b"hello, ENA"
        h1 = _sha3_hex(data)
        h2 = _sha3_hex(data)
        assert h1 == h2
        assert len(h1) == 64

    def test_sha3_hex_different_data_different_hash(self):
        h1 = _sha3_hex(b"foo")
        h2 = _sha3_hex(b"bar")
        assert h1 != h2


# ---------------------------------------------------------------------------
# Fee split math
# ---------------------------------------------------------------------------


class TestFeeSplitMath:

    def test_default_split(self):
        provider, aicf, treasury, refund = _compute_fee_split(10000)
        assert provider == 6000   # 60%
        assert aicf == 3000       # 30%
        assert treasury == 1000   # 10%
        assert refund == 0

    def test_split_sums_to_fee_locked(self):
        fee = 12345
        p, a, t, r = _compute_fee_split(fee)
        assert p + a + t + r == fee

    def test_zero_fee(self):
        p, a, t, r = _compute_fee_split(0)
        assert p == a == t == r == 0

    def test_custom_split(self):
        fee = 10000
        p, a, t, r = _compute_fee_split(fee, provider_bps=5000, aicf_bps=4000, treasury_bps=1000)
        assert p == 5000
        assert a == 4000
        assert t == 1000
        assert r == 0

    def test_split_over_10000_raises(self):
        with pytest.raises(ValueError, match="exceeds 10000"):
            _compute_fee_split(10000, provider_bps=6000, aicf_bps=4000, treasury_bps=1000)

    def test_refund_with_partial_split(self):
        fee = 10000
        # Only 50% distributed → 50% refund
        p, a, t, r = _compute_fee_split(fee, provider_bps=3000, aicf_bps=2000, treasury_bps=0)
        assert p == 3000
        assert a == 2000
        assert t == 0
        assert r == 5000

    def test_safe_add(self):
        assert safe_add(1, 2) == 3

    def test_safe_add_overflow(self):
        with pytest.raises(OverflowError):
            safe_add((2**256) - 1, 1)

    def test_safe_sub(self):
        assert safe_sub(10, 3) == 7

    def test_safe_sub_underflow(self):
        with pytest.raises(ValueError):
            safe_sub(3, 10)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class TestPolicy:

    def test_default_enabled(self):
        state = _make_state()
        assert get_ena_enabled(state) is True

    def test_disable_enable(self):
        state = _make_state()
        set_ena_enabled(state, False)
        assert get_ena_enabled(state) is False
        set_ena_enabled(state, True)
        assert get_ena_enabled(state) is True

    def test_max_input_bytes_default(self):
        state = _make_state()
        assert get_max_input_bytes(state) == DEFAULT_MAX_INPUT_BYTES

    def test_max_input_bytes_set(self):
        state = _make_state()
        set_max_input_bytes(state, 1024)
        assert get_max_input_bytes(state) == 1024

    def test_max_output_bytes_default(self):
        state = _make_state()
        assert get_max_output_bytes(state) == DEFAULT_MAX_OUTPUT_BYTES

    def test_expiry_blocks_default(self):
        state = _make_state()
        assert get_expiry_blocks(state) == DEFAULT_EXPIRY_BLOCKS

    def test_allowed_tasks_default(self):
        state = _make_state()
        tasks = get_allowed_tasks(state)
        assert "classify" in tasks
        assert "embed" in tasks

    def test_set_allowed_tasks(self):
        state = _make_state()
        set_allowed_tasks(state, ["classify"])
        tasks = get_allowed_tasks(state)
        assert tasks == ["classify"]


# ---------------------------------------------------------------------------
# Model version registry
# ---------------------------------------------------------------------------


class TestModelRegistry:

    def test_register_and_get_version(self):
        state = _make_state()
        register_model_version(state, "ena-v1.0.0-h20000", "da:xyz", 20000)
        mv = get_model_version(state, "ena-v1.0.0-h20000")
        assert mv is not None
        assert mv.version == "ena-v1.0.0-h20000"
        assert mv.da_ptr == "da:xyz"
        assert mv.activation_height == 20000
        assert mv.status == "active"

    def test_get_unknown_version(self):
        state = _make_state()
        mv = get_model_version(state, "nonexistent")
        assert mv is None

    def test_get_version_with_empty_da_ptr(self):
        """Model registered with empty da_ptr should still be found (status is sentinel)."""
        state = _make_state()
        register_model_version(state, "ena-v1.0.0-h20000", "", 20000, status="active")
        mv = get_model_version(state, "ena-v1.0.0-h20000")
        assert mv is not None
        assert mv.version == "ena-v1.0.0-h20000"
        assert mv.da_ptr == ""
        assert mv.status == "active"

    def test_model_allowed_active(self):
        state = _make_state()
        register_model_version(state, "ena-v1.0.0-h20000", "da:xyz", 20000, status="active")
        assert is_model_allowed(state, "ena-v1.0.0-h20000") is True

    def test_model_not_allowed_deprecated(self):
        state = _make_state()
        register_model_version(state, "ena-v0.8.0-h0", "da:old", 0, status="deprecated")
        assert is_model_allowed(state, "ena-v0.8.0-h0") is False

    def test_model_not_allowed_experimental(self):
        state = _make_state()
        register_model_version(state, "ena-v1.1.0-h30000", "da:exp", 30000, status="experimental")
        assert is_model_allowed(state, "ena-v1.1.0-h30000") is False

    def test_model_not_allowed_unknown(self):
        state = _make_state()
        assert is_model_allowed(state, "totally-unknown") is False

    def test_set_get_active_model(self):
        state = _make_state()
        register_model_version(state, "ena-v1.0.0-h20000", "da:xyz", 20000)
        set_active_model(state, "ena-v1.0.0-h20000")
        assert get_active_model(state) == "ena-v1.0.0-h20000"

    def test_active_model_default_empty(self):
        state = _make_state()
        assert get_active_model(state) == ""


# ---------------------------------------------------------------------------
# Request lifecycle
# ---------------------------------------------------------------------------


class TestRequestLifecycle:

    def _base_state(self):
        state = _make_state()
        _setup_state(state)
        return state

    def test_create_request_success(self):
        state = self._base_state()
        req_id, req = create_request(
            state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "classify",
            b"hello world", 10000, height := 100,
        )
        assert req_id.startswith("ena-")
        assert req.status == STATUS_QUEUED
        assert req.model_version == "ena-v0.9.0-h10000"
        assert req.task_type == "classify"
        assert req.created_height == height
        assert req.expiry_height > height

    def test_create_request_stores_in_state(self):
        state = self._base_state()
        req_id, _ = create_request(
            state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "classify",
            b"test input", 10000, 200,
        )
        retrieved = get_request(state, req_id)
        assert retrieved is not None
        assert retrieved.request_id == req_id
        assert retrieved.status == STATUS_QUEUED

    def test_get_status_queued(self):
        state = self._base_state()
        req_id, _ = create_request(
            state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "embed",
            b"embed me", 5000, 300,
        )
        assert get_request_status(state, req_id) == STATUS_QUEUED

    def test_set_status_running(self):
        state = self._base_state()
        req_id, _ = create_request(
            state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "classify",
            b"input", 10000, 400,
        )
        set_request_status(state, req_id, STATUS_RUNNING)
        assert get_request_status(state, req_id) == STATUS_RUNNING

    def test_set_status_invalid_raises(self):
        state = self._base_state()
        req_id, _ = create_request(
            state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "classify",
            b"input", 10000, 500,
        )
        with pytest.raises(ValueError, match="Invalid status"):
            set_request_status(state, req_id, "bogus_status")

    def test_submit_result_success(self):
        state = self._base_state()
        req_id, _ = create_request(
            state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "classify",
            b"classify this text", 10000, 600,
        )
        result = submit_result(
            state, req_id, "worker-1", b"positive", "receipt:abc", 601,
        )
        assert result.request_id == req_id
        assert result.worker_id == "worker-1"
        assert result.result_hash == _sha3_hex(b"positive")
        assert get_request_status(state, req_id) == STATUS_COMPLETED

    def test_submit_result_stored(self):
        state = self._base_state()
        req_id, _ = create_request(
            state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "embed",
            b"embed text", 5000, 700,
        )
        submit_result(state, req_id, "worker-2", b"[0.1, 0.2, 0.3]", "receipt:xyz", 701)
        r = get_result(state, req_id)
        assert r is not None
        assert r.result_hash == _sha3_hex(b"[0.1, 0.2, 0.3]")

    def test_get_result_hash_after_completion(self):
        state = self._base_state()
        req_id, _ = create_request(
            state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "summarize",
            b"long text to summarize", 10000, 800,
        )
        submit_result(state, req_id, "worker-3", b"summary", "receipt:def", 801)
        rh = get_result_hash(state, req_id)
        assert rh == _sha3_hex(b"summary")

    def test_submit_result_to_completed_raises(self):
        state = self._base_state()
        req_id, _ = create_request(
            state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "classify",
            b"input", 10000, 900,
        )
        submit_result(state, req_id, "worker-1", b"result1", "receipt:1", 901)
        with pytest.raises(ValueError, match="Cannot submit result"):
            submit_result(state, req_id, "worker-2", b"result2", "receipt:2", 902)

    def test_fail_request(self):
        state = self._base_state()
        req_id, req = create_request(
            state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "classify",
            b"to be failed", 10000, 1000,
        )
        split = fail_request(state, req_id, 1001)
        assert get_request_status(state, req_id) == STATUS_FAILED
        assert split.refund_amount > 0
        assert split.provider_amount == 0

    def test_expire_request(self):
        state = self._base_state()
        req_id, req = create_request(
            state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "classify",
            b"will expire", 10000, 1100,
        )
        split = expire_request(state, req_id, 1101)
        assert get_request_status(state, req_id) == STATUS_EXPIRED
        assert split.refund_amount > 0

    def test_expire_past_expiry_height(self):
        """Submitting result past expiry_height triggers expiry."""
        state = self._base_state()
        set_expiry_blocks(state, 10)
        req_id, req = create_request(
            state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "classify",
            b"expiry test", 10000, 200,
        )
        assert req.expiry_height == 210
        with pytest.raises(ValueError, match="expired"):
            submit_result(state, req_id, "worker-1", b"late result", "receipt:late", 250)

    def test_fee_split_after_completion(self):
        state = self._base_state()
        fee = 10000
        req_id, _ = create_request(
            state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "classify",
            b"input", fee, 1200,
        )
        submit_result(state, req_id, "worker-1", b"ok", "receipt:ok", 1201)
        split = finalize_fee_split(state, req_id)
        assert split.provider_amount + split.aicf_amount + split.treasury_amount + split.refund_amount == fee
        assert split.provider_amount == fee * DEFAULT_PROVIDER_BPS // 10000
        assert split.aicf_amount == fee * DEFAULT_AICF_BPS // 10000
        assert split.treasury_amount == fee * DEFAULT_TREASURY_BPS // 10000

    def test_get_fee_split(self):
        state = self._base_state()
        req_id, _ = create_request(
            state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "classify",
            b"input", 20000, 1300,
        )
        submit_result(state, req_id, "worker-1", b"ok", "receipt:ok", 1301)
        finalize_fee_split(state, req_id)
        split = get_fee_split(state, req_id)
        assert split is not None
        assert split.request_id == req_id


# ---------------------------------------------------------------------------
# Policy rejection cases
# ---------------------------------------------------------------------------


class TestPolicyRejections:

    def _base_state(self):
        state = _make_state()
        _setup_state(state)
        return state

    def test_reject_when_disabled(self):
        state = self._base_state()
        set_ena_enabled(state, False)
        with pytest.raises(ValueError, match="disabled by policy"):
            create_request(
                state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "classify",
                b"input", 10000, 100,
            )

    def test_reject_unknown_model(self):
        state = self._base_state()
        with pytest.raises(ValueError, match="not allowed"):
            create_request(
                state, CREATOR, CONTRACT, "ena-unknown-model", "classify",
                b"input", 10000, 100,
            )

    def test_reject_disallowed_task(self):
        state = self._base_state()
        with pytest.raises(ValueError, match="not allowed"):
            create_request(
                state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "scrape_internet",
                b"input", 10000, 100,
            )

    def test_reject_input_too_large(self):
        state = self._base_state()
        set_max_input_bytes(state, 10)
        with pytest.raises(ValueError, match="too large"):
            create_request(
                state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "classify",
                b"x" * 100, 10000, 100,
            )

    def test_reject_zero_fee(self):
        state = self._base_state()
        with pytest.raises(ValueError, match="fee_locked must be positive"):
            create_request(
                state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "classify",
                b"input", 0, 100,
            )

    def test_reject_output_too_large(self):
        state = self._base_state()
        set_max_output_bytes(state, 10)
        req_id, _ = create_request(
            state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "classify",
            b"input", 10000, 100,
        )
        with pytest.raises(ValueError, match="too large for inline"):
            submit_result(state, req_id, "worker", b"x" * 100, "receipt", 101)

    def test_accept_large_output_with_da_ptr(self):
        """Large outputs accepted if da_ptr is provided."""
        state = self._base_state()
        set_max_output_bytes(state, 10)
        req_id, _ = create_request(
            state, CREATOR, CONTRACT, "ena-v0.9.0-h10000", "classify",
            b"input", 10000, 100,
        )
        # da_ptr bypasses inline size limit
        result = submit_result(
            state, req_id, "worker", b"large" * 100, "receipt", 101,
            da_ptr="da:bigresult"
        )
        assert result.da_ptr == "da:bigresult"


# ---------------------------------------------------------------------------
# verify_receipt
# ---------------------------------------------------------------------------


class TestVerifyReceipt:

    def test_valid_receipt(self):
        ok, reason = verify_receipt(
            "ena-" + "a" * 32,
            "b" * 64,
            "receipt" + "x" * 20,
            "worker-123",
        )
        assert ok is True
        assert reason == "ok"

    def test_invalid_request_id_prefix(self):
        ok, reason = verify_receipt("bad-id", "b" * 64, "receipt" + "x" * 20, "worker")
        assert ok is False
        assert "request_id" in reason

    def test_invalid_result_hash_too_short(self):
        ok, reason = verify_receipt("ena-abc", "short", "receipt" + "x" * 20, "worker")
        assert ok is False
        assert "result_hash" in reason

    def test_invalid_receipt_too_short(self):
        ok, reason = verify_receipt("ena-abc", "b" * 64, "short", "worker")
        assert ok is False
        assert "receipt_hash" in reason

    def test_empty_worker(self):
        ok, reason = verify_receipt("ena-abc", "b" * 64, "receipt" + "x" * 20, "")
        assert ok is False
        assert "worker_id" in reason
