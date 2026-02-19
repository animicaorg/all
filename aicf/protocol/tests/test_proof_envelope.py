"""
Tests for ProofEnvelope CBOR encoding/decoding.
"""

import hashlib
import pytest

from aicf.protocol.proof_envelope import (
    ProofEnvelope,
    create_stub_quantum_envelope,
    create_training_envelope,
)


def test_proof_envelope_to_dict():
    """Test conversion to dictionary."""
    envelope = ProofEnvelope(
        version=1,
        job_id="test_job_001",
        worker_id="worker_001",
        kind="cpu_train",
        inputs_commitment="a" * 64,
        outputs_commitment="b" * 64,
        metrics={"loss": 0.5, "accuracy": 0.9},
        attestation="c" * 64,
        signature="d" * 128,
        timestamp=1234567890,
    )
    
    data = envelope.to_dict()
    assert data["version"] == 1
    assert data["job_id"] == "test_job_001"
    assert data["kind"] == "cpu_train"
    assert data["metrics"]["loss"] == 0.5


def test_proof_envelope_cbor_roundtrip():
    """Test CBOR encoding and decoding roundtrip."""
    envelope = ProofEnvelope(
        version=1,
        job_id="test_job_002",
        worker_id="worker_002",
        kind="quantum",
        inputs_commitment="1" * 64,
        outputs_commitment="2" * 64,
        metrics={"steps": 1000, "runtime_sec": 10.5},
        attestation="3" * 64,
        signature="4" * 128,
    )
    
    # Encode to CBOR hex
    cbor_hex = envelope.to_cbor_hex()
    assert isinstance(cbor_hex, str)
    assert len(cbor_hex) > 0
    
    # Decode from CBOR hex
    decoded = ProofEnvelope.from_cbor_hex(cbor_hex)
    assert decoded.version == envelope.version
    assert decoded.job_id == envelope.job_id
    assert decoded.worker_id == envelope.worker_id
    assert decoded.kind == envelope.kind
    assert decoded.metrics == envelope.metrics


def test_proof_envelope_validate_schema_valid():
    """Test schema validation with valid envelope."""
    envelope = ProofEnvelope(
        version=1,
        job_id="test_job_003",
        worker_id="worker_003",
        kind="gpu_train",
        inputs_commitment="a" * 64,
        outputs_commitment="b" * 64,
        metrics={"epochs": 3, "loss": 0.3},
        attestation="c" * 64,
        signature="d" * 128,
    )
    
    is_valid, error = envelope.validate_schema()
    assert is_valid
    assert error is None


def test_proof_envelope_validate_schema_invalid_version():
    """Test schema validation with invalid version."""
    envelope = ProofEnvelope(
        version=999,  # Invalid version
        job_id="test_job_004",
        worker_id="worker_004",
        kind="cpu_train",
        inputs_commitment="a" * 64,
        outputs_commitment="b" * 64,
        metrics={},
        attestation="c" * 64,
        signature="d" * 128,
    )
    
    is_valid, error = envelope.validate_schema()
    assert not is_valid
    assert "version" in error.lower()


def test_proof_envelope_validate_schema_invalid_kind():
    """Test schema validation with invalid kind."""
    envelope = ProofEnvelope(
        version=1,
        job_id="test_job_005",
        worker_id="worker_005",
        kind="invalid_kind",  # Invalid
        inputs_commitment="a" * 64,
        outputs_commitment="b" * 64,
        metrics={},
        attestation="c" * 64,
        signature="d" * 128,
    )
    
    is_valid, error = envelope.validate_schema()
    assert not is_valid
    assert "kind" in error.lower()


def test_proof_envelope_validate_schema_invalid_commitment_length():
    """Test schema validation with wrong commitment length."""
    envelope = ProofEnvelope(
        version=1,
        job_id="test_job_006",
        worker_id="worker_006",
        kind="cpu_train",
        inputs_commitment="a" * 32,  # Too short (should be 64)
        outputs_commitment="b" * 64,
        metrics={},
        attestation="c" * 64,
        signature="d" * 128,
    )
    
    is_valid, error = envelope.validate_schema()
    assert not is_valid
    assert "inputs_commitment" in error.lower()


def test_create_stub_quantum_envelope():
    """Test creating a stub quantum envelope."""
    envelope = create_stub_quantum_envelope(
        job_id="quantum_job_001",
        worker_id="qworker_001",
        steps=5000,
        runtime_sec=30.5,
        signature="a" * 128,
    )
    
    assert envelope.version == 1
    assert envelope.job_id == "quantum_job_001"
    assert envelope.worker_id == "qworker_001"
    assert envelope.kind == "stub_quantum_v1"
    assert len(envelope.inputs_commitment) == 64
    assert len(envelope.outputs_commitment) == 64
    assert len(envelope.attestation) == 64
    assert envelope.metrics["steps"] == 5000
    assert envelope.metrics["runtime_sec"] == 30.5
    
    # Should be valid
    is_valid, error = envelope.validate_schema()
    assert is_valid


def test_create_training_envelope():
    """Test creating a training envelope."""
    inputs_hash = hashlib.sha3_256(b"inputs").hexdigest()
    outputs_hash = hashlib.sha3_256(b"outputs").hexdigest()
    attestation_hash = hashlib.sha3_256(b"manifest").hexdigest()
    
    envelope = create_training_envelope(
        job_id="train_job_001",
        worker_id="tworker_001",
        kind="gpu_train",
        inputs_hash=inputs_hash,
        outputs_hash=outputs_hash,
        metrics={
            "epochs": 3,
            "loss": 0.25,
            "accuracy": 0.92,
        },
        attestation_hash=attestation_hash,
        signature="b" * 128,
    )
    
    assert envelope.version == 1
    assert envelope.job_id == "train_job_001"
    assert envelope.kind == "gpu_train"
    assert envelope.inputs_commitment == inputs_hash
    assert envelope.outputs_commitment == outputs_hash
    assert envelope.attestation == attestation_hash
    assert envelope.metrics["epochs"] == 3
    
    # Should be valid
    is_valid, error = envelope.validate_schema()
    assert is_valid


def test_proof_envelope_deterministic_cbor():
    """Test that CBOR encoding is deterministic."""
    envelope1 = ProofEnvelope(
        version=1,
        job_id="test_job_007",
        worker_id="worker_007",
        kind="eval",
        inputs_commitment="x" * 64,
        outputs_commitment="y" * 64,
        metrics={"samples": 100, "accuracy": 0.85},
        attestation="z" * 64,
        signature="w" * 128,
        timestamp=1000000,
    )
    
    envelope2 = ProofEnvelope(
        version=1,
        job_id="test_job_007",
        worker_id="worker_007",
        kind="eval",
        inputs_commitment="x" * 64,
        outputs_commitment="y" * 64,
        metrics={"samples": 100, "accuracy": 0.85},
        attestation="z" * 64,
        signature="w" * 128,
        timestamp=1000000,
    )
    
    # Same envelope should produce same CBOR encoding
    cbor1 = envelope1.to_cbor_hex()
    cbor2 = envelope2.to_cbor_hex()
    assert cbor1 == cbor2
