"""
Data Directory Configuration
=============================

Manages the configurable data directory for AICF/quantum worker data.
Ensures all writes go to a writable location and never fail due to
read-only filesystem surprises.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def get_data_dir() -> Path:
    """
    Get the configured data directory for Animica.
    
    Resolves in this order:
    1. ANIMICA_DATA_DIR environment variable
    2. ~/.animica (default)
    
    Returns:
        Path to the data directory
    """
    data_dir_str = os.environ.get("ANIMICA_DATA_DIR")
    if data_dir_str:
        return Path(data_dir_str).expanduser().resolve()
    
    return Path.home() / ".animica"


def ensure_data_dir(subdir: Optional[str] = None) -> Path:
    """
    Ensure the data directory exists and is writable.
    
    Args:
        subdir: Optional subdirectory within the data directory
    
    Returns:
        Path to the (sub)directory
    
    Raises:
        PermissionError: If directory is not writable
        OSError: If directory cannot be created
    """
    base_dir = get_data_dir()
    
    if subdir:
        target_dir = base_dir / subdir
    else:
        target_dir = base_dir
    
    # Create directory if it doesn't exist
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise OSError(
            f"Failed to create data directory {target_dir}: {e}. "
            f"Set ANIMICA_DATA_DIR to a writable location."
        ) from e
    
    # Check if writable
    if not os.access(target_dir, os.W_OK):
        raise PermissionError(
            f"Data directory {target_dir} is not writable. "
            f"Set ANIMICA_DATA_DIR to a writable location."
        )
    
    return target_dir


def get_aicf_data_dir() -> Path:
    """Get the AICF-specific data directory."""
    return ensure_data_dir("aicf")


def get_quantum_data_dir() -> Path:
    """Get the quantum worker data directory."""
    return ensure_data_dir("quantum")


def check_data_dir_writable() -> tuple[bool, Optional[str]]:
    """
    Check if the data directory is writable.
    
    Returns:
        (is_writable, error_message)
    """
    try:
        data_dir = get_data_dir()
        ensure_data_dir()
        
        # Try to write a test file
        test_file = data_dir / ".write_test"
        try:
            test_file.write_text("test")
            test_file.unlink()
            return True, None
        except Exception as e:
            return False, f"Cannot write to {data_dir}: {e}"
    
    except Exception as e:
        return False, str(e)


__all__ = [
    "get_data_dir",
    "ensure_data_dir",
    "get_aicf_data_dir",
    "get_quantum_data_dir",
    "check_data_dir_writable",
]
