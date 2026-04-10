"""
Schema definitions for hash work jobs and results.

Provides HashJob, HashResult dataclasses and deterministic CBOR/JSON codecs
aligned with existing external-service patterns (mirroring AICF/DA style).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

import cbor2

from .algorithms import HashAlgorithm


class DeviceType(str, Enum):
    """Device type for hash work execution."""

    CPU = "CPU"
    GPU = "GPU"
    ASIC = "ASIC"
    QUANTUM = "QUANTUM"
    FPGA = "FPGA"
    OTHER = "OTHER"


@dataclass(frozen=True)
class HashJobDescriptor:
    """
    ABI-friendly descriptor for hash job (used in VM-Py contracts).

    This is the on-chain representation that contracts emit/consume.
    """

    algorithm: str  # HashAlgorithm.value
    input_commitment: bytes  # 32-byte commitment to input data
    target_bits: int  # Difficulty target (log2 scale)
    max_iterations: int  # Maximum iterations allowed
    # Scrypt-specific params (optional)
    scrypt_n: Optional[int] = None
    scrypt_r: Optional[int] = None
    scrypt_p: Optional[int] = None


@dataclass(frozen=True)
class HashJob:
    """
    Complete hash job specification.

    Fields align with AICF/DA job patterns for consistency.
    """

    job_id: bytes  # 32-byte unique identifier
    algorithm: HashAlgorithm
    input_commitment: bytes  # 32-byte hash commitment
    target_bits: int  # Difficulty target
    max_iterations: int  # Iteration limit
    max_cost: int  # Maximum computational cost (gas-like)
    # Scrypt-specific
    scrypt_n: Optional[int] = None
    scrypt_r: Optional[int] = None
    scrypt_p: Optional[int] = None
    # Job metadata
    requester: Optional[str] = None  # Address
    created_at: Optional[int] = None  # Block height or timestamp


@dataclass(frozen=True)
class HashResult:
    """
    Hash work result with proof metadata.

    Fields align with existing external service result patterns.
    """

    job_id: bytes  # 32-byte job identifier
    output_hash: bytes  # 32-byte result hash
    nonce: bytes  # Nonce/solution found (variable length)
    iterations: int  # Actual iterations performed
    # Proof metadata
    device_type: DeviceType
    backend_id: str  # Backend implementation identifier
    worker_address: Optional[str] = None  # Worker address
    timestamp: Optional[int] = None  # Result timestamp
    # Optional verification data
    proof_data: Optional[bytes] = None


# --- CBOR encoding/decoding (deterministic, canonical) ---


def encode_hash_job(job: HashJob) -> bytes:
    """Encode HashJob to canonical CBOR."""
    data: Dict[str, Any] = {
        "job_id": job.job_id,
        "algorithm": job.algorithm.value,
        "input_commitment": job.input_commitment,
        "target_bits": job.target_bits,
        "max_iterations": job.max_iterations,
        "max_cost": job.max_cost,
    }
    if job.scrypt_n is not None:
        data["scrypt_n"] = job.scrypt_n
    if job.scrypt_r is not None:
        data["scrypt_r"] = job.scrypt_r
    if job.scrypt_p is not None:
        data["scrypt_p"] = job.scrypt_p
    if job.requester is not None:
        data["requester"] = job.requester
    if job.created_at is not None:
        data["created_at"] = job.created_at

    return cbor2.dumps(data, canonical=True)


def decode_hash_job(data: bytes) -> HashJob:
    """Decode HashJob from CBOR."""
    obj = cbor2.loads(data)
    return HashJob(
        job_id=bytes(obj["job_id"]),
        algorithm=HashAlgorithm(obj["algorithm"]),
        input_commitment=bytes(obj["input_commitment"]),
        target_bits=int(obj["target_bits"]),
        max_iterations=int(obj["max_iterations"]),
        max_cost=int(obj["max_cost"]),
        scrypt_n=obj.get("scrypt_n"),
        scrypt_r=obj.get("scrypt_r"),
        scrypt_p=obj.get("scrypt_p"),
        requester=obj.get("requester"),
        created_at=obj.get("created_at"),
    )


def encode_hash_result(result: HashResult) -> bytes:
    """Encode HashResult to canonical CBOR."""
    data: Dict[str, Any] = {
        "job_id": result.job_id,
        "output_hash": result.output_hash,
        "nonce": result.nonce,
        "iterations": result.iterations,
        "device_type": result.device_type.value,
        "backend_id": result.backend_id,
    }
    if result.worker_address is not None:
        data["worker_address"] = result.worker_address
    if result.timestamp is not None:
        data["timestamp"] = result.timestamp
    if result.proof_data is not None:
        data["proof_data"] = result.proof_data

    return cbor2.dumps(data, canonical=True)


def decode_hash_result(data: bytes) -> HashResult:
    """Decode HashResult from CBOR."""
    obj = cbor2.loads(data)
    return HashResult(
        job_id=bytes(obj["job_id"]),
        output_hash=bytes(obj["output_hash"]),
        nonce=bytes(obj["nonce"]),
        iterations=int(obj["iterations"]),
        device_type=DeviceType(obj["device_type"]),
        backend_id=str(obj["backend_id"]),
        worker_address=obj.get("worker_address"),
        timestamp=obj.get("timestamp"),
        proof_data=bytes(obj["proof_data"]) if "proof_data" in obj else None,
    )


# --- JSON encoding (for human-readable configs/logs) ---


def hash_job_to_json(job: HashJob) -> str:
    """Encode HashJob to JSON (for configs/logs, not consensus-critical)."""
    data = {
        "job_id": job.job_id.hex(),
        "algorithm": job.algorithm.value,
        "input_commitment": job.input_commitment.hex(),
        "target_bits": job.target_bits,
        "max_iterations": job.max_iterations,
        "max_cost": job.max_cost,
    }
    if job.scrypt_n is not None:
        data["scrypt_n"] = job.scrypt_n
    if job.scrypt_r is not None:
        data["scrypt_r"] = job.scrypt_r
    if job.scrypt_p is not None:
        data["scrypt_p"] = job.scrypt_p
    if job.requester is not None:
        data["requester"] = job.requester
    if job.created_at is not None:
        data["created_at"] = job.created_at

    return json.dumps(data, sort_keys=True)


def hash_result_to_json(result: HashResult) -> str:
    """Encode HashResult to JSON (for configs/logs, not consensus-critical)."""
    data = {
        "job_id": result.job_id.hex(),
        "output_hash": result.output_hash.hex(),
        "nonce": result.nonce.hex(),
        "iterations": result.iterations,
        "device_type": result.device_type.value,
        "backend_id": result.backend_id,
    }
    if result.worker_address is not None:
        data["worker_address"] = result.worker_address
    if result.timestamp is not None:
        data["timestamp"] = result.timestamp
    if result.proof_data is not None:
        data["proof_data"] = result.proof_data.hex()

    return json.dumps(data, sort_keys=True)


__all__ = [
    "DeviceType",
    "HashJobDescriptor",
    "HashJob",
    "HashResult",
    "encode_hash_job",
    "decode_hash_job",
    "encode_hash_result",
    "decode_hash_result",
    "hash_job_to_json",
    "hash_result_to_json",
]
