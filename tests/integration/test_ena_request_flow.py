# SPDX-License-Identifier: Apache-2.0
"""
Integration test: ENA on-chain request → worker result → contract reads hash.

Simulates the full ENA lifecycle:
  1. Contract creates ENA request (creates on-chain state)
  2. Worker submits inference result with receipt
  3. Fee split is finalized
  4. Contract reads result hash + DA pointer

This test does NOT run actual AI inference; it uses deterministic mocks.
Skip unless RUN_INTEGRATION_TESTS=1 or allow local execution via direct import.
"""

from __future__ import annotations

import hashlib
import pytest

from tests.integration import env  # skip gate


# ---------------------------------------------------------------------------
# State mock (shared with unit tests)
# ---------------------------------------------------------------------------


class _MockState:
    def __init__(self):
        self._d = {}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def put(self, key, value):
        self._d[key] = value


def _sha3_hex(data: bytes) -> str:
    return hashlib.sha3_256(data).hexdigest()


# ---------------------------------------------------------------------------
# ENA request lifecycle integration test
# ---------------------------------------------------------------------------


class TestENARequestLifecycle:
    """
    End-to-end lifecycle test for ENA on-chain request flow.
    Uses only pure Python state (no running node required).
    """

    @pytest.fixture(autouse=True)
    def _imports(self):
        """Skip if execution.state.ena_state is not importable."""
        try:
            from execution.state.ena_state import (
                create_request,
                submit_result,
                finalize_fee_split,
                fail_request,
                expire_request,
                get_result,
                get_result_hash,
                get_fee_split,
                get_request_status,
                register_model_version,
                set_active_model,
                set_ena_enabled,
                set_expiry_blocks,
                STATUS_QUEUED,
                STATUS_COMPLETED,
                STATUS_FAILED,
                STATUS_EXPIRED,
            )
            self._create_request = create_request
            self._submit_result = submit_result
            self._finalize_fee_split = finalize_fee_split
            self._fail_request = fail_request
            self._expire_request = expire_request
            self._get_result = get_result
            self._get_result_hash = get_result_hash
            self._get_fee_split = get_fee_split
            self._get_request_status = get_request_status
            self._register_model_version = register_model_version
            self._set_active_model = set_active_model
            self._set_ena_enabled = set_ena_enabled
            self._set_expiry_blocks = set_expiry_blocks
            self.STATUS_QUEUED = STATUS_QUEUED
            self.STATUS_COMPLETED = STATUS_COMPLETED
            self.STATUS_FAILED = STATUS_FAILED
            self.STATUS_EXPIRED = STATUS_EXPIRED
        except ImportError as e:
            pytest.skip(f"execution.state.ena_state not available: {e}")

    def _setup_state(self, model_version="ena-v0.9.0-h10000"):
        state = _MockState()
        self._set_ena_enabled(state, True)
        self._register_model_version(state, model_version, "da:checkpoint", 10000, status="active")
        self._set_active_model(state, model_version)
        return state

    CREATOR = bytes.fromhex("ab" * 32)
    CONTRACT = bytes.fromhex("cd" * 32)
    MODEL = "ena-v0.9.0-h10000"

    def test_happy_path_classify(self):
        """Full classify: create → submit result → finalize fees → read hash."""
        state = self._setup_state()
        input_payload = b"Is this cat or dog?"
        fee = 20000

        # Step 1: Contract creates ENA request
        req_id, req = self._create_request(
            state=state,
            creator=self.CREATOR,
            contract_address=self.CONTRACT,
            model_version=self.MODEL,
            task_type="classify",
            input_payload=input_payload,
            fee_locked=fee,
            current_height=1000,
        )
        assert req.status == self.STATUS_QUEUED
        assert req_id.startswith("ena-")

        # Step 2: Off-chain worker submits result
        result_payload = b'{"label":"cat","confidence":0.97}'
        receipt_hash = "worker_receipt_" + "a" * 32
        result = self._submit_result(
            state=state,
            request_id=req_id,
            worker_id="provider-0x1234",
            result_payload=result_payload,
            receipt_hash=receipt_hash,
            current_height=1001,
        )
        assert result.result_hash == _sha3_hex(result_payload)
        assert self._get_request_status(state, req_id) == self.STATUS_COMPLETED

        # Step 3: Fee split finalized
        split = self._finalize_fee_split(state, req_id)
        assert split.provider_amount + split.aicf_amount + split.treasury_amount + split.refund_amount == fee
        assert split.provider_amount > 0
        assert split.aicf_amount > 0

        # Step 4: Contract reads result hash deterministically
        rh = self._get_result_hash(state, req_id)
        assert rh == _sha3_hex(result_payload)

        # Step 5: Verify stored fee split
        stored_split = self._get_fee_split(state, req_id)
        assert stored_split is not None
        assert stored_split.provider_amount == split.provider_amount

    def test_happy_path_embed(self):
        """Full embed: create → submit result with DA pointer."""
        state = self._setup_state()

        req_id, _ = self._create_request(
            state=state,
            creator=self.CREATOR,
            contract_address=self.CONTRACT,
            model_version=self.MODEL,
            task_type="embed",
            input_payload=b"embed this sentence",
            fee_locked=5000,
            current_height=2000,
        )

        large_result = b"[" + b",".join(b"0.1" for _ in range(512)) + b"]"
        result = self._submit_result(
            state=state,
            request_id=req_id,
            worker_id="worker-embed-1",
            result_payload=large_result,
            receipt_hash="receipt_" + "b" * 32,
            current_height=2001,
            da_ptr="da:embedding_result_abc",
        )
        assert result.da_ptr == "da:embedding_result_abc"
        assert result.result_hash == _sha3_hex(large_result)

        rh = self._get_result_hash(state, req_id)
        assert rh == _sha3_hex(large_result)

    def test_request_expiry_and_refund(self):
        """Request expires → creator gets refund minus slash."""
        state = self._setup_state()
        self._set_expiry_blocks(state, 10)

        fee = 15000
        req_id, req = self._create_request(
            state=state,
            creator=self.CREATOR,
            contract_address=self.CONTRACT,
            model_version=self.MODEL,
            task_type="classify",
            input_payload=b"will expire",
            fee_locked=fee,
            current_height=5000,
        )
        assert req.expiry_height == 5010

        split = self._expire_request(state, req_id, 5020)
        assert self._get_request_status(state, req_id) == self.STATUS_EXPIRED
        assert split.refund_amount > 0
        assert split.aicf_amount > 0  # slash fee goes to AICF
        assert split.provider_amount == 0
        total = split.provider_amount + split.aicf_amount + split.treasury_amount + split.refund_amount
        assert total == fee

    def test_request_failure_and_refund(self):
        """Request fails → creator gets refund minus slash."""
        state = self._setup_state()
        fee = 10000

        req_id, _ = self._create_request(
            state=state,
            creator=self.CREATOR,
            contract_address=self.CONTRACT,
            model_version=self.MODEL,
            task_type="summarize",
            input_payload=b"will fail",
            fee_locked=fee,
            current_height=6000,
        )

        split = self._fail_request(state, req_id, 6001)
        assert self._get_request_status(state, req_id) == self.STATUS_FAILED
        assert split.refund_amount > 0
        total = split.provider_amount + split.aicf_amount + split.treasury_amount + split.refund_amount
        assert total == fee

    def test_multiple_requests_independent(self):
        """Multiple concurrent requests should have independent state."""
        state = self._setup_state()

        ids = []
        for i in range(5):
            req_id, _ = self._create_request(
                state=state,
                creator=self.CREATOR,
                contract_address=self.CONTRACT,
                model_version=self.MODEL,
                task_type="classify",
                input_payload=f"input {i}".encode(),
                fee_locked=10000,
                current_height=7000,
                nonce=i,
            )
            ids.append(req_id)

        # All IDs should be unique
        assert len(set(ids)) == 5

        # Complete some, leave others queued
        for req_id in ids[:3]:
            self._submit_result(
                state, req_id, "worker", b"result", "receipt_" + "r" * 20, 7001
            )

        for req_id in ids[:3]:
            assert self._get_request_status(state, req_id) == self.STATUS_COMPLETED
        for req_id in ids[3:]:
            assert self._get_request_status(state, req_id) == self.STATUS_QUEUED

    def test_determinism_same_inputs(self):
        """Same inputs at same height produce same request_id."""
        state1 = self._setup_state()
        state2 = self._setup_state()

        params = dict(
            creator=self.CREATOR,
            contract_address=self.CONTRACT,
            model_version=self.MODEL,
            task_type="classify",
            input_payload=b"determinism check",
            fee_locked=10000,
            current_height=8000,
            nonce=42,
        )

        req_id1, _ = self._create_request(state=state1, **params)
        req_id2, _ = self._create_request(state=state2, **params)
        assert req_id1 == req_id2
