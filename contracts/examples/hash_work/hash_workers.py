# -*- coding: utf-8 -*-
"""
HashWorkers contract - Registry for hash work computation workers.

Maintains a registry of workers with their capabilities (device types,
supported algorithms) to enable job matching and performance tracking.

Persistent state:
  w:{address} -> worker metadata (CBOR bytes)
  a:{address} -> active status (b"1"=active, b"0"=inactive)
  c:{address}:{algo} -> capability flag (b"1" if supported)

Events:
  WorkerRegistered(address, device_type, algorithms)
  WorkerStatusChanged(address, active)
  WorkerCapabilityUpdated(address, algorithm, supported)
"""

from stdlib import abi, events, hash, storage

# State key prefixes
_W_PREFIX = b"w:"  # worker metadata
_A_PREFIX = b"a:"  # active status
_C_PREFIX = b"c:"  # capabilities

# Caps
_MAX_METADATA_LEN = 1024
_MAX_ALGORITHMS = 32


def _key(prefix: bytes, addr: bytes) -> bytes:
    """Build storage key."""
    return prefix + addr


def _cap_key(addr: bytes, algo: bytes) -> bytes:
    """Build capability key."""
    return _C_PREFIX + addr + b":" + algo


def _sha3_32(b: bytes) -> bytes:
    """Return 32-byte SHA3-256 digest."""
    return hash.sha3_256(b)


# --- Public interface ---


def register_worker(address: bytes, device_type: bytes, metadata: bytes) -> bool:
    """
    Register a worker with device capabilities.

    Args:
        address: Worker address (20-32 bytes)
        device_type: Device type (e.g., b"CPU", b"GPU", b"ASIC", b"QUANTUM")
        metadata: Optional metadata JSON/CBOR bytes

    Returns:
        True if successfully registered
    """
    # Validate inputs
    if len(address) < 20 or len(address) > 32:
        abi.revert(b"address_invalid")
    if len(device_type) == 0 or len(device_type) > 32:
        abi.revert(b"device_type_invalid")
    if len(metadata) > _MAX_METADATA_LEN:
        abi.revert(b"metadata_too_long")

    # Check if already registered
    existing = storage.get(_key(_W_PREFIX, address))
    if existing is not None:
        abi.revert(b"worker_already_registered")

    # Store worker metadata
    # Format: device_type_len (1) + device_type + metadata
    worker_data = len(device_type).to_bytes(1, "big") + device_type + metadata

    storage.set(_key(_W_PREFIX, address), worker_data)
    storage.set(_key(_A_PREFIX, address), b"1")  # active by default

    # Emit registration event
    events.emit(
        b"WorkerRegistered",
        {
            b"address": address,
            b"device_type": device_type,
            b"metadata_hash": _sha3_32(metadata) if len(metadata) > 0 else b"\x00" * 32,
        },
    )

    return True


def set_worker_active(address: bytes, active: bool) -> bool:
    """
    Set worker active status.

    Args:
        address: Worker address
        active: Active status

    Returns:
        True if successfully updated
    """
    # Validate inputs
    if len(address) < 20 or len(address) > 32:
        abi.revert(b"address_invalid")

    # Check worker exists
    worker_data = storage.get(_key(_W_PREFIX, address))
    if worker_data is None:
        abi.revert(b"worker_not_found")

    # Update status
    status_byte = b"1" if active else b"0"
    storage.set(_key(_A_PREFIX, address), status_byte)

    # Emit status change event
    events.emit(
        b"WorkerStatusChanged",
        {
            b"address": address,
            b"active": active,
        },
    )

    return True


def add_algorithm_capability(address: bytes, algorithm: bytes) -> bool:
    """
    Add algorithm capability for a worker.

    Args:
        address: Worker address
        algorithm: Algorithm name (e.g., b"SHA256", b"SCRYPT")

    Returns:
        True if successfully added
    """
    # Validate inputs
    if len(address) < 20 or len(address) > 32:
        abi.revert(b"address_invalid")
    if len(algorithm) == 0 or len(algorithm) > 32:
        abi.revert(b"algorithm_invalid")

    # Check worker exists
    worker_data = storage.get(_key(_W_PREFIX, address))
    if worker_data is None:
        abi.revert(b"worker_not_found")

    # Normalize algorithm to uppercase
    algo_str = algorithm.decode("utf-8", errors="ignore").upper()
    algo_bytes = algo_str.encode("utf-8")

    # Set capability
    storage.set(_cap_key(address, algo_bytes), b"1")

    # Emit capability update event
    events.emit(
        b"WorkerCapabilityUpdated",
        {
            b"address": address,
            b"algorithm": algo_bytes,
            b"supported": True,
        },
    )

    return True


def remove_algorithm_capability(address: bytes, algorithm: bytes) -> bool:
    """
    Remove algorithm capability for a worker.

    Args:
        address: Worker address
        algorithm: Algorithm name

    Returns:
        True if successfully removed
    """
    # Validate inputs
    if len(address) < 20 or len(address) > 32:
        abi.revert(b"address_invalid")
    if len(algorithm) == 0 or len(algorithm) > 32:
        abi.revert(b"algorithm_invalid")

    # Check worker exists
    worker_data = storage.get(_key(_W_PREFIX, address))
    if worker_data is None:
        abi.revert(b"worker_not_found")

    # Normalize algorithm
    algo_str = algorithm.decode("utf-8", errors="ignore").upper()
    algo_bytes = algo_str.encode("utf-8")

    # Remove capability
    storage.set(_cap_key(address, algo_bytes), b"0")

    # Emit capability update event
    events.emit(
        b"WorkerCapabilityUpdated",
        {
            b"address": address,
            b"algorithm": algo_bytes,
            b"supported": False,
        },
    )

    return True


def get_worker(address: bytes) -> tuple:
    """
    Get worker information.

    Args:
        address: Worker address

    Returns:
        (exists: bool, device_type: bytes, active: bool, metadata: bytes)
    """
    if len(address) < 20 or len(address) > 32:
        return (False, b"", False, b"")

    worker_data = storage.get(_key(_W_PREFIX, address))
    if worker_data is None:
        return (False, b"", False, b"")

    # Decode worker data
    # Format: device_type_len (1) + device_type + metadata
    if len(worker_data) < 1:
        return (False, b"", False, b"")

    device_type_len = worker_data[0]
    if len(worker_data) < 1 + device_type_len:
        return (False, b"", False, b"")

    device_type = worker_data[1 : 1 + device_type_len]
    metadata = worker_data[1 + device_type_len :]

    # Get active status
    status = storage.get(_key(_A_PREFIX, address))
    active = status == b"1" if status is not None else False

    return (True, device_type, active, metadata)


def supports_algorithm(address: bytes, algorithm: bytes) -> bool:
    """
    Check if worker supports an algorithm.

    Args:
        address: Worker address
        algorithm: Algorithm name

    Returns:
        True if worker supports the algorithm
    """
    if len(address) < 20 or len(address) > 32:
        return False
    if len(algorithm) == 0:
        return False

    # Normalize algorithm
    algo_str = algorithm.decode("utf-8", errors="ignore").upper()
    algo_bytes = algo_str.encode("utf-8")

    # Check capability
    cap = storage.get(_cap_key(address, algo_bytes))
    return cap == b"1" if cap is not None else False


def is_active(address: bytes) -> bool:
    """
    Check if worker is active.

    Args:
        address: Worker address

    Returns:
        True if worker is active
    """
    if len(address) < 20 or len(address) > 32:
        return False

    # Check worker exists first
    worker_data = storage.get(_key(_W_PREFIX, address))
    if worker_data is None:
        return False

    status = storage.get(_key(_A_PREFIX, address))
    return status == b"1" if status is not None else False
