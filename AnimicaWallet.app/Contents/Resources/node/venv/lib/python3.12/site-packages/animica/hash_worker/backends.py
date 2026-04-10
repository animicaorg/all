"""
Hash work computation backends.

Provides pluggable backends for different hardware types:
- CPU: Working implementation using Python hashlib
- GPU/ASIC/QUANTUM: Mock implementations that delegate to CPU but set appropriate device_type
"""

from __future__ import annotations

import hashlib
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from python.animica.hash_work.algorithms import HashAlgorithm
from python.animica.hash_work.schemas import DeviceType, HashResult


@dataclass
class HashWorkResult:
    """Internal result from backend execution."""

    success: bool
    output_hash: Optional[bytes] = None
    nonce: Optional[bytes] = None
    iterations: int = 0
    error: Optional[str] = None


class HashBackend(ABC):
    """Abstract base class for hash work backends."""

    @abstractmethod
    def get_device_type(self) -> DeviceType:
        """Return the device type for this backend."""
        pass

    @abstractmethod
    def get_backend_id(self) -> str:
        """Return a unique identifier for this backend."""
        pass

    @abstractmethod
    def execute_hash_work(
        self,
        algorithm: HashAlgorithm,
        input_commitment: bytes,
        target_bits: int,
        max_iterations: int,
        scrypt_n: Optional[int] = None,
        scrypt_r: Optional[int] = None,
        scrypt_p: Optional[int] = None,
    ) -> HashWorkResult:
        """
        Execute hash work to find a solution meeting the target.

        Args:
            algorithm: Hash algorithm to use
            input_commitment: 32-byte input commitment
            target_bits: Target difficulty (leading zero bits)
            max_iterations: Maximum iterations to try
            scrypt_n: Scrypt N parameter (if applicable)
            scrypt_r: Scrypt r parameter (if applicable)
            scrypt_p: Scrypt p parameter (if applicable)

        Returns:
            HashWorkResult with success status and solution if found
        """
        pass


class CPUBackend(HashBackend):
    """CPU-based hash work backend using Python hashlib."""

    def get_device_type(self) -> DeviceType:
        return DeviceType.CPU

    def get_backend_id(self) -> str:
        return "cpu-python-hashlib"

    def execute_hash_work(
        self,
        algorithm: HashAlgorithm,
        input_commitment: bytes,
        target_bits: int,
        max_iterations: int,
        scrypt_n: Optional[int] = None,
        scrypt_r: Optional[int] = None,
        scrypt_p: Optional[int] = None,
    ) -> HashWorkResult:
        """Execute hash work on CPU."""
        try:
            if algorithm == HashAlgorithm.SHA256:
                return self._compute_sha256(
                    input_commitment, target_bits, max_iterations
                )
            elif algorithm == HashAlgorithm.SHA256D:
                return self._compute_sha256d(
                    input_commitment, target_bits, max_iterations
                )
            elif algorithm == HashAlgorithm.SCRYPT:
                if not all([scrypt_n, scrypt_r, scrypt_p]):
                    return HashWorkResult(
                        success=False, error="Missing scrypt parameters"
                    )
                return self._compute_scrypt(
                    input_commitment,
                    target_bits,
                    max_iterations,
                    scrypt_n,
                    scrypt_r,
                    scrypt_p,
                )
            elif algorithm == HashAlgorithm.BLAKE2B:
                return self._compute_blake2b(
                    input_commitment, target_bits, max_iterations
                )
            else:
                return HashWorkResult(
                    success=False, error=f"Unsupported algorithm: {algorithm}"
                )
        except Exception as e:
            return HashWorkResult(success=False, error=str(e))

    def _compute_sha256(
        self, input_commitment: bytes, target_bits: int, max_iterations: int
    ) -> HashWorkResult:
        """Compute SHA-256 hash work."""
        target = 2 ** (256 - target_bits)

        for i in range(max_iterations):
            nonce = i.to_bytes(8, "big")
            data = input_commitment + nonce
            output_hash = hashlib.sha256(data).digest()

            # Check if hash meets target
            hash_int = int.from_bytes(output_hash, "big")
            if hash_int < target:
                return HashWorkResult(
                    success=True,
                    output_hash=output_hash,
                    nonce=nonce,
                    iterations=i + 1,
                )

        # Didn't find solution within max_iterations
        return HashWorkResult(
            success=False, error="Max iterations reached without solution"
        )

    def _compute_sha256d(
        self, input_commitment: bytes, target_bits: int, max_iterations: int
    ) -> HashWorkResult:
        """Compute double SHA-256 hash work."""
        target = 2 ** (256 - target_bits)

        for i in range(max_iterations):
            nonce = i.to_bytes(8, "big")
            data = input_commitment + nonce
            output_hash = hashlib.sha256(hashlib.sha256(data).digest()).digest()

            hash_int = int.from_bytes(output_hash, "big")
            if hash_int < target:
                return HashWorkResult(
                    success=True,
                    output_hash=output_hash,
                    nonce=nonce,
                    iterations=i + 1,
                )

        return HashWorkResult(
            success=False, error="Max iterations reached without solution"
        )

    def _compute_scrypt(
        self,
        input_commitment: bytes,
        target_bits: int,
        max_iterations: int,
        N: int,
        r: int,
        p: int,
    ) -> HashWorkResult:
        """Compute Scrypt hash work."""
        target = 2 ** (256 - target_bits)

        for i in range(max_iterations):
            nonce = i.to_bytes(8, "big")
            data = input_commitment + nonce

            # Use hashlib scrypt (requires Python 3.6+)
            try:
                output_hash = hashlib.scrypt(
                    data, salt=b"animica", n=N, r=r, p=p, dklen=32
                )
            except Exception:
                return HashWorkResult(success=False, error="Scrypt computation failed")

            hash_int = int.from_bytes(output_hash, "big")
            if hash_int < target:
                return HashWorkResult(
                    success=True,
                    output_hash=output_hash,
                    nonce=nonce,
                    iterations=i + 1,
                )

        return HashWorkResult(
            success=False, error="Max iterations reached without solution"
        )

    def _compute_blake2b(
        self, input_commitment: bytes, target_bits: int, max_iterations: int
    ) -> HashWorkResult:
        """Compute BLAKE2b hash work."""
        target = 2 ** (256 - target_bits)

        for i in range(max_iterations):
            nonce = i.to_bytes(8, "big")
            data = input_commitment + nonce
            output_hash = hashlib.blake2b(data, digest_size=32).digest()

            hash_int = int.from_bytes(output_hash, "big")
            if hash_int < target:
                return HashWorkResult(
                    success=True,
                    output_hash=output_hash,
                    nonce=nonce,
                    iterations=i + 1,
                )

        return HashWorkResult(
            success=False, error="Max iterations reached without solution"
        )


class GPUBackend(HashBackend):
    """
    GPU backend (mock - delegates to CPU but reports GPU device type).

    In production, this would use CUDA/OpenCL for GPU acceleration.
    """

    def __init__(self):
        self._cpu_backend = CPUBackend()

    def get_device_type(self) -> DeviceType:
        return DeviceType.GPU

    def get_backend_id(self) -> str:
        return "gpu-mock-cuda"

    def execute_hash_work(
        self,
        algorithm: HashAlgorithm,
        input_commitment: bytes,
        target_bits: int,
        max_iterations: int,
        scrypt_n: Optional[int] = None,
        scrypt_r: Optional[int] = None,
        scrypt_p: Optional[int] = None,
    ) -> HashWorkResult:
        """Execute using CPU backend but report as GPU."""
        return self._cpu_backend.execute_hash_work(
            algorithm,
            input_commitment,
            target_bits,
            max_iterations,
            scrypt_n,
            scrypt_r,
            scrypt_p,
        )


class ASICBackend(HashBackend):
    """
    ASIC backend (mock - delegates to CPU but reports ASIC device type).

    In production, this would interface with ASIC hardware controllers.
    """

    def __init__(self):
        self._cpu_backend = CPUBackend()

    def get_device_type(self) -> DeviceType:
        return DeviceType.ASIC

    def get_backend_id(self) -> str:
        return "asic-mock-antminer"

    def execute_hash_work(
        self,
        algorithm: HashAlgorithm,
        input_commitment: bytes,
        target_bits: int,
        max_iterations: int,
        scrypt_n: Optional[int] = None,
        scrypt_r: Optional[int] = None,
        scrypt_p: Optional[int] = None,
    ) -> HashWorkResult:
        """Execute using CPU backend but report as ASIC."""
        return self._cpu_backend.execute_hash_work(
            algorithm,
            input_commitment,
            target_bits,
            max_iterations,
            scrypt_n,
            scrypt_r,
            scrypt_p,
        )


class QuantumBackend(HashBackend):
    """
    Quantum backend (mock - uses probabilistic search but reports QUANTUM device type).

    In production, this would interface with quantum computing hardware.
    """

    def __init__(self):
        self._cpu_backend = CPUBackend()

    def get_device_type(self) -> DeviceType:
        return DeviceType.QUANTUM

    def get_backend_id(self) -> str:
        return "quantum-mock-simulator"

    def execute_hash_work(
        self,
        algorithm: HashAlgorithm,
        input_commitment: bytes,
        target_bits: int,
        max_iterations: int,
        scrypt_n: Optional[int] = None,
        scrypt_r: Optional[int] = None,
        scrypt_p: Optional[int] = None,
    ) -> HashWorkResult:
        """
        Execute using probabilistic sampling (mock quantum advantage).

        Real quantum computers could potentially use Grover's algorithm
        for quadratic speedup in unstructured search.
        """
        # For mock, just use CPU backend
        return self._cpu_backend.execute_hash_work(
            algorithm,
            input_commitment,
            target_bits,
            max_iterations,
            scrypt_n,
            scrypt_r,
            scrypt_p,
        )


def get_backend(backend_type: str) -> HashBackend:
    """
    Factory function to get a hash backend by type.

    Args:
        backend_type: One of "cpu", "gpu", "asic", "quantum"

    Returns:
        Appropriate HashBackend instance

    Raises:
        ValueError: If backend_type is unknown
    """
    backend_type = backend_type.lower()

    if backend_type == "cpu":
        return CPUBackend()
    elif backend_type == "gpu":
        return GPUBackend()
    elif backend_type == "asic":
        return ASICBackend()
    elif backend_type == "quantum":
        return QuantumBackend()
    else:
        raise ValueError(
            f"Unknown backend type: {backend_type}. "
            f"Expected one of: cpu, gpu, asic, quantum"
        )


__all__ = [
    "HashBackend",
    "CPUBackend",
    "GPUBackend",
    "ASICBackend",
    "QuantumBackend",
    "HashWorkResult",
    "get_backend",
]
