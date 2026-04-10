"""
Tests for data directory configuration.
"""

import os
import tempfile
from pathlib import Path

import pytest

from python.animica.cli.data_dir import (
    get_data_dir,
    ensure_data_dir,
    get_aicf_data_dir,
    get_quantum_data_dir,
    check_data_dir_writable,
)


def test_get_data_dir_default():
    """Test getting default data directory."""
    # Clear env var
    old_val = os.environ.pop("ANIMICA_DATA_DIR", None)
    try:
        data_dir = get_data_dir()
        assert data_dir == Path.home() / ".animica"
    finally:
        if old_val:
            os.environ["ANIMICA_DATA_DIR"] = old_val


def test_get_data_dir_from_env():
    """Test getting data directory from environment variable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["ANIMICA_DATA_DIR"] = tmpdir
        try:
            data_dir = get_data_dir()
            assert data_dir == Path(tmpdir)
        finally:
            os.environ.pop("ANIMICA_DATA_DIR", None)


def test_ensure_data_dir_creates_directory():
    """Test that ensure_data_dir creates directory if it doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["ANIMICA_DATA_DIR"] = tmpdir
        try:
            test_subdir = "test_subdir"
            result = ensure_data_dir(test_subdir)
            
            assert result.exists()
            assert result.is_dir()
            assert result == Path(tmpdir) / test_subdir
        finally:
            os.environ.pop("ANIMICA_DATA_DIR", None)


def test_ensure_data_dir_base():
    """Test ensuring base data directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["ANIMICA_DATA_DIR"] = tmpdir
        try:
            result = ensure_data_dir()
            
            assert result.exists()
            assert result.is_dir()
            assert result == Path(tmpdir)
        finally:
            os.environ.pop("ANIMICA_DATA_DIR", None)


def test_get_aicf_data_dir():
    """Test getting AICF-specific data directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["ANIMICA_DATA_DIR"] = tmpdir
        try:
            aicf_dir = get_aicf_data_dir()
            
            assert aicf_dir.exists()
            assert aicf_dir.is_dir()
            assert aicf_dir == Path(tmpdir) / "aicf"
        finally:
            os.environ.pop("ANIMICA_DATA_DIR", None)


def test_get_quantum_data_dir():
    """Test getting quantum worker data directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["ANIMICA_DATA_DIR"] = tmpdir
        try:
            quantum_dir = get_quantum_data_dir()
            
            assert quantum_dir.exists()
            assert quantum_dir.is_dir()
            assert quantum_dir == Path(tmpdir) / "quantum"
        finally:
            os.environ.pop("ANIMICA_DATA_DIR", None)


def test_check_data_dir_writable_success():
    """Test checking writable data directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["ANIMICA_DATA_DIR"] = tmpdir
        try:
            is_writable, error = check_data_dir_writable()
            
            assert is_writable
            assert error is None
        finally:
            os.environ.pop("ANIMICA_DATA_DIR", None)


def test_ensure_data_dir_permission_error():
    """Test that ensure_data_dir raises appropriate error for non-writable directory."""
    # Create a read-only directory
    with tempfile.TemporaryDirectory() as tmpdir:
        readonly_dir = Path(tmpdir) / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)  # Read-only
        
        os.environ["ANIMICA_DATA_DIR"] = str(readonly_dir)
        try:
            # Should raise PermissionError when trying to ensure subdirectory
            with pytest.raises(PermissionError):
                ensure_data_dir("test")
        finally:
            # Clean up
            readonly_dir.chmod(0o755)
            os.environ.pop("ANIMICA_DATA_DIR", None)
