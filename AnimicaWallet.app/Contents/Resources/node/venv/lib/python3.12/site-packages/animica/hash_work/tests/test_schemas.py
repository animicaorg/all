"""Tests for hash work schemas and encoding."""

import pytest

from python.animica.hash_work.algorithms import HashAlgorithm
from python.animica.hash_work.schemas import (
    DeviceType,
    HashJob,
    HashJobDescriptor,
    HashResult,
    decode_hash_job,
    decode_hash_result,
    encode_hash_job,
    encode_hash_result,
    hash_job_to_json,
    hash_result_to_json,
)


def test_device_type_enum():
    """Test DeviceType enum values."""
    assert DeviceType.CPU.value == "CPU"
    assert DeviceType.GPU.value == "GPU"
    assert DeviceType.ASIC.value == "ASIC"
    assert DeviceType.QUANTUM.value == "QUANTUM"
    assert DeviceType.FPGA.value == "FPGA"
    assert DeviceType.OTHER.value == "OTHER"


def test_hash_job_descriptor():
    """Test HashJobDescriptor dataclass."""
    desc = HashJobDescriptor(
        algorithm="SHA256",
        input_commitment=b"\x01" * 32,
        target_bits=16,
        max_iterations=1000000,
    )
    assert desc.algorithm == "SHA256"
    assert len(desc.input_commitment) == 32
    assert desc.target_bits == 16
    assert desc.max_iterations == 1000000
    assert desc.scrypt_n is None

    # With scrypt params
    desc_scrypt = HashJobDescriptor(
        algorithm="SCRYPT",
        input_commitment=b"\x02" * 32,
        target_bits=16,
        max_iterations=100000,
        scrypt_n=16384,
        scrypt_r=8,
        scrypt_p=1,
    )
    assert desc_scrypt.scrypt_n == 16384
    assert desc_scrypt.scrypt_r == 8
    assert desc_scrypt.scrypt_p == 1


def test_hash_job_cbor_roundtrip():
    """Test CBOR encoding/decoding of HashJob."""
    job = HashJob(
        job_id=b"\x01" * 32,
        algorithm=HashAlgorithm.SHA256,
        input_commitment=b"\x02" * 32,
        target_bits=20,
        max_iterations=5000000,
        max_cost=1000000,
        requester="anim1testaddress",
        created_at=12345,
    )

    # Encode and decode
    encoded = encode_hash_job(job)
    assert isinstance(encoded, bytes)
    assert len(encoded) > 0

    decoded = decode_hash_job(encoded)
    assert decoded.job_id == job.job_id
    assert decoded.algorithm == job.algorithm
    assert decoded.input_commitment == job.input_commitment
    assert decoded.target_bits == job.target_bits
    assert decoded.max_iterations == job.max_iterations
    assert decoded.max_cost == job.max_cost
    assert decoded.requester == job.requester
    assert decoded.created_at == job.created_at


def test_hash_job_cbor_with_scrypt():
    """Test CBOR encoding/decoding of HashJob with scrypt params."""
    job = HashJob(
        job_id=b"\x03" * 32,
        algorithm=HashAlgorithm.SCRYPT,
        input_commitment=b"\x04" * 32,
        target_bits=18,
        max_iterations=100000,
        max_cost=500000,
        scrypt_n=16384,
        scrypt_r=8,
        scrypt_p=1,
    )

    encoded = encode_hash_job(job)
    decoded = decode_hash_job(encoded)

    assert decoded.algorithm == HashAlgorithm.SCRYPT
    assert decoded.scrypt_n == 16384
    assert decoded.scrypt_r == 8
    assert decoded.scrypt_p == 1


def test_hash_result_cbor_roundtrip():
    """Test CBOR encoding/decoding of HashResult."""
    result = HashResult(
        job_id=b"\x05" * 32,
        output_hash=b"\x06" * 32,
        nonce=b"\x07" * 8,
        iterations=123456,
        device_type=DeviceType.GPU,
        backend_id="cuda-11.8",
        worker_address="anim1workeraddress",
        timestamp=67890,
        proof_data=b"proof_metadata",
    )

    # Encode and decode
    encoded = encode_hash_result(result)
    assert isinstance(encoded, bytes)
    assert len(encoded) > 0

    decoded = decode_hash_result(encoded)
    assert decoded.job_id == result.job_id
    assert decoded.output_hash == result.output_hash
    assert decoded.nonce == result.nonce
    assert decoded.iterations == result.iterations
    assert decoded.device_type == result.device_type
    assert decoded.backend_id == result.backend_id
    assert decoded.worker_address == result.worker_address
    assert decoded.timestamp == result.timestamp
    assert decoded.proof_data == result.proof_data


def test_hash_result_cbor_minimal():
    """Test CBOR encoding/decoding with minimal optional fields."""
    result = HashResult(
        job_id=b"\x08" * 32,
        output_hash=b"\x09" * 32,
        nonce=b"\x0a" * 8,
        iterations=999,
        device_type=DeviceType.CPU,
        backend_id="cpu-python",
    )

    encoded = encode_hash_result(result)
    decoded = decode_hash_result(encoded)

    assert decoded.job_id == result.job_id
    assert decoded.worker_address is None
    assert decoded.timestamp is None
    assert decoded.proof_data is None


def test_hash_job_json():
    """Test JSON encoding of HashJob."""
    job = HashJob(
        job_id=b"\x0b" * 32,
        algorithm=HashAlgorithm.SHA256D,
        input_commitment=b"\x0c" * 32,
        target_bits=22,
        max_iterations=10000000,
        max_cost=2000000,
        requester="anim1jsontest",
    )

    json_str = hash_job_to_json(job)
    assert isinstance(json_str, str)
    assert '"algorithm": "SHA256D"' in json_str
    assert '"target_bits": 22' in json_str
    assert '"max_iterations": 10000000' in json_str
    # Check hex encoding
    assert job.job_id.hex() in json_str
    assert job.input_commitment.hex() in json_str


def test_hash_result_json():
    """Test JSON encoding of HashResult."""
    result = HashResult(
        job_id=b"\x0d" * 32,
        output_hash=b"\x0e" * 32,
        nonce=b"\x0f" * 8,
        iterations=555555,
        device_type=DeviceType.ASIC,
        backend_id="asic-antminer",
        worker_address="anim1asicworker",
    )

    json_str = hash_result_to_json(result)
    assert isinstance(json_str, str)
    assert '"device_type": "ASIC"' in json_str
    assert '"backend_id": "asic-antminer"' in json_str
    assert '"iterations": 555555' in json_str
    # Check hex encoding
    assert result.job_id.hex() in json_str
    assert result.output_hash.hex() in json_str
    assert result.nonce.hex() in json_str


def test_cbor_determinism():
    """Test that CBOR encoding is deterministic (canonical)."""
    job = HashJob(
        job_id=b"\x10" * 32,
        algorithm=HashAlgorithm.SHA256,
        input_commitment=b"\x11" * 32,
        target_bits=16,
        max_iterations=1000000,
        max_cost=500000,
    )

    # Encode multiple times
    encoded1 = encode_hash_job(job)
    encoded2 = encode_hash_job(job)
    encoded3 = encode_hash_job(job)

    # All encodings should be identical (byte-for-byte)
    assert encoded1 == encoded2
    assert encoded2 == encoded3


def test_hash_result_different_devices():
    """Test HashResult with different device types."""
    devices = [
        DeviceType.CPU,
        DeviceType.GPU,
        DeviceType.ASIC,
        DeviceType.QUANTUM,
        DeviceType.FPGA,
        DeviceType.OTHER,
    ]

    for device in devices:
        result = HashResult(
            job_id=b"\x12" * 32,
            output_hash=b"\x13" * 32,
            nonce=b"\x14" * 8,
            iterations=1000,
            device_type=device,
            backend_id=f"test-{device.value.lower()}",
        )

        # Ensure encoding/decoding works for all device types
        encoded = encode_hash_result(result)
        decoded = decode_hash_result(encoded)
        assert decoded.device_type == device
        assert decoded.backend_id == f"test-{device.value.lower()}"
