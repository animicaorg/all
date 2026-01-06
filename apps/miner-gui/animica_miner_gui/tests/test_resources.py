"""Tests for resource management (logo path)."""

from pathlib import Path

import pytest

from animica_miner_gui.resources import get_logo_path


def test_get_logo_path():
    """Test that get_logo_path returns a valid path."""
    logo_path = get_logo_path()
    
    # Logo path should be returned
    assert logo_path is not None, "Logo path should not be None"
    
    # Path should be a Path object
    assert isinstance(logo_path, Path), "Logo path should be a Path object"
    
    # Logo file should exist
    assert logo_path.exists(), f"Logo file should exist at {logo_path}"
    
    # Logo should be a file (not directory)
    assert logo_path.is_file(), "Logo path should point to a file"
    
    # Logo should have .png extension
    assert logo_path.suffix == ".png", "Logo should be a PNG file"
    
    # Logo name should be logo.png
    assert logo_path.name == "logo.png", "Logo filename should be logo.png"


def test_logo_path_is_readable():
    """Test that the logo file is readable."""
    logo_path = get_logo_path()
    assert logo_path is not None
    
    # Try to read the file
    with open(logo_path, "rb") as f:
        data = f.read()
        # PNG files start with specific magic bytes
        assert data[:8] == b'\x89PNG\r\n\x1a\n', "File should be a valid PNG"
