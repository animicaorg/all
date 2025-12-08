"""Integration tests for docker-compose configuration."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest


def get_compose_file() -> Path:
    """Get the path to the docker-compose file."""
    # Assuming tests are run from repo root
    compose_file = Path(__file__).resolve().parents[4] / "tests" / "devnet" / "docker-compose.yml"
    return compose_file


def test_compose_file_exists() -> None:
    """Test that the docker-compose file exists."""
    compose_file = get_compose_file()
    assert compose_file.exists(), f"Compose file not found at {compose_file}"


@pytest.mark.skipif(
    subprocess.run(["which", "docker"], capture_output=True).returncode != 0,
    reason="Docker not installed"
)
def test_compose_dev_profile_excludes_studio_services() -> None:
    """Test that 'dev' profile does not include studio services."""
    compose_file = get_compose_file()
    
    # List services with 'dev' profile
    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "--profile", "dev", "config", "--services"],
        capture_output=True,
        text=True,
        cwd=compose_file.parent
    )
    
    # Check that the command succeeded
    assert result.returncode == 0, f"docker compose config failed: {result.stderr}"
    
    services = result.stdout.strip().split("\n")
    
    # Verify node services are included
    assert "node1" in services, "node1 should be in dev profile"
    assert "miner" in services, "miner should be in dev profile"
    
    # Verify studio services are NOT included
    assert "services" not in services, "services (studio) should NOT be in dev profile"
    assert "explorer" not in services, "explorer should NOT be in dev profile"


@pytest.mark.skipif(
    subprocess.run(["which", "docker"], capture_output=True).returncode != 0,
    reason="Docker not installed"
)
def test_compose_studio_profile_includes_studio_services() -> None:
    """Test that 'studio' profile includes studio services when combined with dev."""
    compose_file = get_compose_file()
    
    # List services with both 'dev' and 'studio' profiles
    # Studio services depend on node, so both profiles are needed
    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), 
         "--profile", "dev", "--profile", "studio", "config", "--services"],
        capture_output=True,
        text=True,
        cwd=compose_file.parent
    )
    
    # Check that the command succeeded
    assert result.returncode == 0, f"docker compose config failed: {result.stderr}"
    
    services = result.stdout.strip().split("\n")
    
    # Verify studio services are included
    assert "services" in services, "services (studio) should be in studio profile"
    assert "explorer" in services, "explorer should be in studio profile"


@pytest.mark.skipif(
    subprocess.run(["which", "docker"], capture_output=True).returncode != 0,
    reason="Docker not installed"
)
def test_compose_combined_profiles() -> None:
    """Test that both profiles can be used together."""
    compose_file = get_compose_file()
    
    # List services with both profiles
    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), 
         "--profile", "dev", "--profile", "studio", "config", "--services"],
        capture_output=True,
        text=True,
        cwd=compose_file.parent
    )
    
    # Check that the command succeeded
    assert result.returncode == 0, f"docker compose config failed: {result.stderr}"
    
    services = result.stdout.strip().split("\n")
    
    # Verify all services are included
    assert "node1" in services, "node1 should be included"
    assert "miner" in services, "miner should be included"
    assert "services" in services, "services should be included"
    assert "explorer" in services, "explorer should be included"


def test_compose_studio_services_depends_on_node() -> None:
    """Test that studio services container depends on node1."""
    compose_file = get_compose_file()
    
    # Parse the compose file (simplified check)
    content = compose_file.read_text()
    
    # Find the services section
    assert "services:" in content
    
    # Check that services container has node1 dependency
    services_section_start = content.find("container_name: animica-studio-services")
    assert services_section_start != -1, "studio services container not found"
    
    # Look for depends_on after the services section
    depends_on_pos = content.find("depends_on:", services_section_start)
    node1_dep_pos = content.find("node1:", depends_on_pos)
    
    # Make sure we find the dependency before the next service definition
    next_service = content.find("# ---", services_section_start + 1)
    assert depends_on_pos < next_service, "depends_on not found in services container"
    assert node1_dep_pos < next_service, "node1 dependency not found in services container"
