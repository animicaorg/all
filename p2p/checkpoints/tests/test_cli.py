"""Tests for checkpoint CLI tool."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


# Get project root dynamically
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.absolute()


def test_cli_list_all():
    """Test CLI list command for all chains."""
    result = subprocess.run(
        [sys.executable, "-m", "p2p.checkpoints.cli.checkpoints", "list"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT)
    )
    
    assert result.returncode == 0
    assert "mainnet" in result.stdout.lower()
    assert "55795" in result.stdout
    assert "0x0a3205eb3aca078a9c6e8415e5970e198b43c087bff7b71371054bbbc99d8938" in result.stdout


def test_cli_list_mainnet():
    """Test CLI list command for mainnet only."""
    result = subprocess.run(
        [sys.executable, "-m", "p2p.checkpoints.cli.checkpoints", "list", "--chain-id", "1"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT)
    )
    
    assert result.returncode == 0
    assert "mainnet" in result.stdout.lower()
    assert "55795" in result.stdout
    assert "0x0a3205eb3aca078a9c6e8415e5970e198b43c087bff7b71371054bbbc99d8938" in result.stdout


def test_cli_list_testnet():
    """Test CLI list command for testnet (should have no checkpoints)."""
    result = subprocess.run(
        [sys.executable, "-m", "p2p.checkpoints.cli.checkpoints", "list", "--chain-id", "2"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT)
    )
    
    assert result.returncode == 0
    assert "No built-in checkpoints" in result.stdout or "testnet" in result.stdout.lower()


def test_cli_export_mainnet(tmp_path):
    """Test CLI export command for mainnet."""
    output_file = tmp_path / "checkpoints.json"
    
    result = subprocess.run(
        [
            sys.executable, "-m", "p2p.checkpoints.cli.checkpoints",
            "export",
            "--chain-id", "1",
            "--output", str(output_file)
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT)
    )
    
    assert result.returncode == 0
    assert output_file.exists()
    
    # Verify file content
    with output_file.open() as f:
        data = json.load(f)
    
    assert data["chain_id"] == 1
    assert data["network"] == "mainnet"
    assert "checkpoints" in data
    assert len(data["checkpoints"]) >= 1
    
    # Find the specific checkpoint
    cp_55795 = next((cp for cp in data["checkpoints"] if cp["height"] == 55795), None)
    assert cp_55795 is not None
    assert cp_55795["hash"] == "0x0a3205eb3aca078a9c6e8415e5970e198b43c087bff7b71371054bbbc99d8938"


def test_cli_export_all(tmp_path):
    """Test CLI export command for all chains."""
    output_file = tmp_path / "all_checkpoints.json"
    
    result = subprocess.run(
        [
            sys.executable, "-m", "p2p.checkpoints.cli.checkpoints",
            "export",
            "--output", str(output_file)
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT)
    )
    
    assert result.returncode == 0
    assert output_file.exists()
    
    # Verify file content
    with output_file.open() as f:
        data = json.load(f)
    
    assert "networks" in data
    assert "mainnet" in data["networks"]
    
    mainnet_data = data["networks"]["mainnet"]
    assert mainnet_data["chain_id"] == 1
    assert len(mainnet_data["checkpoints"]) >= 1


def test_cli_export_testnet_fails(tmp_path):
    """Test CLI export command fails for testnet (no checkpoints)."""
    output_file = tmp_path / "testnet_checkpoints.json"
    
    result = subprocess.run(
        [
            sys.executable, "-m", "p2p.checkpoints.cli.checkpoints",
            "export",
            "--chain-id", "2",
            "--output", str(output_file)
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT)
    )
    
    # Should fail because testnet has no checkpoints
    assert result.returncode != 0
    assert "No built-in checkpoints" in result.stderr


def test_cli_no_command():
    """Test CLI with no command shows help."""
    result = subprocess.run(
        [sys.executable, "-m", "p2p.checkpoints.cli.checkpoints"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT)
    )
    
    # Should show help
    assert "usage:" in result.stdout.lower() or "usage:" in result.stderr.lower()
