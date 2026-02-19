# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for vm_py.stdlib.ena — ENA deterministic Python contract API.

Tests cover:
- Import / availability of the ena stdlib module
- Request creation via the simulator
- Status polling
- Result hash retrieval
- verify_receipt structural validation
- Gas constant visibility
- Error cases (bad types, size limits, empty inputs)
"""

from __future__ import annotations

import hashlib
import pytest

import vm_py.stdlib.ena as ena_mod
from vm_py.stdlib.ena import (
    _EnaSimulator,
    _derive_request_id,
    _sha3_hex,
    GAS_ENA_REQUEST,
    GAS_ENA_STATUS,
    GAS_ENA_RESULT,
    GAS_ENA_READ,
    GAS_ENA_VERIFY,
    MAX_MODEL_LEN,
    MAX_TASK_TYPE_LEN,
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_CALLBACK_LEN,
)


# ---------------------------------------------------------------------------
# Simulator self-tests
# ---------------------------------------------------------------------------


class TestEnaSimulator:
    """Tests using the _EnaSimulator directly."""

    def _sim(self):
        return _EnaSimulator()

    def test_request_returns_request_id(self):
        sim = self._sim()
        result = sim.request(
            creator=bytes(32),
            model_version="ena-v0.9.0-h10000",
            task_type="classify",
            input_payload=b"hello world",
            fee_limit=10000,
            current_height=100,
        )
        assert isinstance(result, dict)
        assert "request_id" in result
        assert result["request_id"].startswith("ena-")

    def test_request_deterministic(self):
        """Same inputs → same request_id."""
        sim = self._sim()
        params = dict(
            creator=bytes(32),
            model_version="ena-v0.9.0-h10000",
            task_type="classify",
            input_payload=b"hello world",
            fee_limit=10000,
            current_height=100,
            nonce=0,
        )
        r1 = sim.request(**params)
        r2 = sim.request(**params)
        assert r1["request_id"] == r2["request_id"]

    def test_status_queued_after_request(self):
        sim = self._sim()
        r = sim.request(
            creator=bytes(32),
            model_version="ena-v0.9.0-h10000",
            task_type="embed",
            input_payload=b"embed this",
            fee_limit=5000,
            current_height=200,
        )
        req_id = r["request_id"]
        assert sim.get_status(req_id) == "queued"

    def test_status_unknown_for_missing_id(self):
        sim = self._sim()
        assert sim.get_status("ena-" + "x" * 32) == ""

    def test_result_hash_empty_before_completion(self):
        sim = self._sim()
        r = sim.request(
            creator=bytes(32),
            model_version="ena-v0.9.0-h10000",
            task_type="classify",
            input_payload=b"test",
            fee_limit=1000,
            current_height=300,
        )
        req_id = r["request_id"]
        assert sim.get_result_hash(req_id) == ""

    def test_inject_result_and_read(self):
        sim = self._sim()
        r = sim.request(
            creator=bytes(32),
            model_version="ena-v0.9.0-h10000",
            task_type="classify",
            input_payload=b"classify me",
            fee_limit=1000,
            current_height=400,
        )
        req_id = r["request_id"]
        result_bytes = b"positive"
        result_hash = _sha3_hex(result_bytes)
        sim._inject_result(req_id, result_hash, output_inline=result_bytes)

        assert sim.get_status(req_id) == "completed"
        assert sim.get_result_hash(req_id) == result_hash
        assert sim.read_result(req_id) == result_bytes

    def test_read_result_none_before_completion(self):
        sim = self._sim()
        r = sim.request(
            creator=bytes(32),
            model_version="ena-v0.9.0-h10000",
            task_type="embed",
            input_payload=b"not ready",
            fee_limit=1000,
            current_height=500,
        )
        req_id = r["request_id"]
        assert sim.read_result(req_id) is None

    def test_da_ptr_after_inject(self):
        sim = self._sim()
        r = sim.request(
            creator=bytes(32),
            model_version="ena-v0.9.0-h10000",
            task_type="summarize",
            input_payload=b"large text",
            fee_limit=1000,
            current_height=600,
        )
        req_id = r["request_id"]
        sim._inject_result(req_id, "a" * 64, da_ptr="da:commit123")
        assert sim.get_da_ptr(req_id) == "da:commit123"

    def test_verify_receipt_valid(self):
        sim = self._sim()
        ok, reason = sim.verify_receipt(
            "ena-" + "a" * 32,
            "b" * 64,
            "receipt_hash_" + "x" * 20,
            "worker-1",
        )
        assert ok is True

    def test_verify_receipt_invalid_request_id(self):
        sim = self._sim()
        ok, reason = sim.verify_receipt("bad-id", "b" * 64, "receipt", "worker-1")
        assert ok is False

    def test_verify_receipt_invalid_result_hash(self):
        sim = self._sim()
        ok, reason = sim.verify_receipt("ena-abc", "short_hash", "receipt_xxx", "worker-1")
        assert ok is False


# ---------------------------------------------------------------------------
# Gas constants sanity
# ---------------------------------------------------------------------------


class TestGasConstants:

    def test_request_gas_positive(self):
        assert GAS_ENA_REQUEST > 0

    def test_status_gas_positive(self):
        assert GAS_ENA_STATUS > 0

    def test_result_gas_positive(self):
        assert GAS_ENA_RESULT > 0

    def test_read_gas_positive(self):
        assert GAS_ENA_READ > 0

    def test_verify_gas_positive(self):
        assert GAS_ENA_VERIFY > 0

    def test_request_gas_higher_than_status(self):
        """Request creation should be more expensive than status reads."""
        assert GAS_ENA_REQUEST > GAS_ENA_STATUS

    def test_request_gas_higher_than_result_read(self):
        assert GAS_ENA_REQUEST > GAS_ENA_RESULT


# ---------------------------------------------------------------------------
# Size limits sanity
# ---------------------------------------------------------------------------


class TestSizeLimits:

    def test_max_model_len_positive(self):
        assert MAX_MODEL_LEN > 0

    def test_max_task_type_len_positive(self):
        assert MAX_TASK_TYPE_LEN > 0

    def test_max_input_bytes_positive(self):
        assert MAX_INPUT_BYTES > 0

    def test_max_output_bytes_positive(self):
        assert MAX_OUTPUT_BYTES > 0

    def test_max_callback_len_positive(self):
        assert MAX_CALLBACK_LEN > 0

    def test_max_input_bytes_reasonable(self):
        """Should be at least 1KB and at most 1MB."""
        assert 1024 <= MAX_INPUT_BYTES <= 1_048_576

    def test_max_output_bytes_at_least_max_input(self):
        """Output limit should be at least as large as input limit."""
        assert MAX_OUTPUT_BYTES >= MAX_INPUT_BYTES


# ---------------------------------------------------------------------------
# Public API validation helpers
# ---------------------------------------------------------------------------


class TestPublicApiHelpers:

    def test_derive_request_id_format(self):
        rid = _derive_request_id(bytes(32), "model", "classify", "hash", 100, 0)
        assert rid.startswith("ena-")
        assert len(rid) > 4

    def test_sha3_hex_returns_64_char_hex(self):
        h = _sha3_hex(b"test")
        assert len(h) == 64
        int(h, 16)  # must be valid hex

    def test_to_bytes_str(self):
        from vm_py.stdlib.ena import _to_bytes
        result = _to_bytes("x", "hello")
        assert result == b"hello"

    def test_to_bytes_bytes(self):
        from vm_py.stdlib.ena import _to_bytes
        result = _to_bytes("x", b"raw")
        assert result == b"raw"

    def test_to_bytes_bytearray(self):
        from vm_py.stdlib.ena import _to_bytes
        result = _to_bytes("x", bytearray(b"array"))
        assert result == b"array"

    def test_to_bytes_wrong_type(self):
        from vm_py.stdlib.ena import _to_bytes
        with pytest.raises(TypeError):
            _to_bytes("x", 12345)

    def test_check_size_within(self):
        from vm_py.stdlib.ena import _check_size
        _check_size("data", b"x" * 10, 100)  # no error

    def test_check_size_exceeds(self):
        from vm_py.stdlib.ena import _check_size
        with pytest.raises(ValueError, match="exceeds limit"):
            _check_size("data", b"x" * 100, 10)

    def test_check_nonempty_empty_bytes(self):
        from vm_py.stdlib.ena import _check_nonempty
        with pytest.raises(ValueError, match="must not be empty"):
            _check_nonempty("data", b"")

    def test_check_nonempty_empty_string(self):
        from vm_py.stdlib.ena import _check_nonempty
        with pytest.raises(ValueError, match="must not be empty"):
            _check_nonempty("data", "")


# ---------------------------------------------------------------------------
# Module-level API surface
# ---------------------------------------------------------------------------


class TestModuleExports:

    def test_request_callable(self):
        assert callable(ena_mod.request)

    def test_get_status_callable(self):
        assert callable(ena_mod.get_status)

    def test_get_result_hash_callable(self):
        assert callable(ena_mod.get_result_hash)

    def test_read_result_callable(self):
        assert callable(ena_mod.read_result)

    def test_get_da_ptr_callable(self):
        assert callable(ena_mod.get_da_ptr)

    def test_verify_receipt_callable(self):
        assert callable(ena_mod.verify_receipt)


# ---------------------------------------------------------------------------
# Public API input validation (called against simulator runtime)
# ---------------------------------------------------------------------------


class TestPublicApiInputValidation:
    """Test that the public api raises on bad inputs (without a real chain)."""

    def test_get_status_empty_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            ena_mod.get_status("")

    def test_get_result_hash_empty_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            ena_mod.get_result_hash("")

    def test_read_result_empty_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            ena_mod.read_result("")

    def test_get_da_ptr_empty_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            ena_mod.get_da_ptr("")
