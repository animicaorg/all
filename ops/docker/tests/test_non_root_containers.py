"""
Test that Docker containers run as non-root users.

This test verifies that all Animica Docker containers are configured to run
as non-root users for security best practices.
"""
import subprocess
import pytest


def run_docker_command(image_name: str, command: list[str]) -> str:
    """Run a command in a Docker container and return the output."""
    cmd = ["docker", "run", "--rm", image_name] + command
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.mark.docker
def test_node_runs_as_non_root():
    """Test that the node container runs as a non-root user."""
    # Build the image
    subprocess.run(
        ["docker", "build", "-f", "ops/docker/node.Dockerfile", "-t", "animica-node:test", "."],
        check=True,
        capture_output=True,
        cwd="/home/runner/work/all/all"
    )
    
    # Check the user
    output = run_docker_command("animica-node:test", ["id", "-u"])
    uid = int(output)
    
    # Should not be root (UID 0)
    assert uid != 0, "Node container should not run as root (UID 0)"
    # Should be the expected animica user (UID 10001)
    assert uid == 10001, f"Node container should run as UID 10001, got {uid}"


@pytest.mark.docker
def test_node_can_write_to_volume():
    """Test that the non-root node user can write to mounted volumes."""
    # Build the image
    subprocess.run(
        ["docker", "build", "-f", "ops/docker/node.Dockerfile", "-t", "animica-node:test", "."],
        check=True,
        capture_output=True,
        cwd="/home/runner/work/all/all"
    )
    
    # Create a test volume
    volume_name = "animica-node-test-vol"
    subprocess.run(["docker", "volume", "create", volume_name], check=True, capture_output=True)
    
    try:
        # Test writing to the volume
        cmd = [
            "docker", "run", "--rm",
            "-v", f"{volume_name}:/data",
            "animica-node:test",
            "sh", "-c", "touch /data/test.txt && echo 'success'"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert "success" in result.stdout, "Should be able to write to volume"
        
    finally:
        # Clean up
        subprocess.run(["docker", "volume", "rm", volume_name], capture_output=True)


@pytest.mark.docker
def test_miner_runs_as_non_root():
    """Test that the miner container runs as a non-root user."""
    # Build the image
    subprocess.run(
        ["docker", "build", "-f", "ops/docker/miner.Dockerfile", "-t", "animica-miner:test", "."],
        check=True,
        capture_output=True,
        cwd="/home/runner/work/all/all"
    )
    
    # Check the user
    output = run_docker_command("animica-miner:test", ["id", "-u"])
    uid = int(output)
    
    # Should not be root (UID 0)
    assert uid != 0, "Miner container should not run as root (UID 0)"
    # Should be the expected animica user (UID 10002)
    assert uid == 10002, f"Miner container should run as UID 10002, got {uid}"


@pytest.mark.docker
def test_explorer_runs_as_non_root():
    """Test that the explorer container runs as a non-root user."""
    # Build the image
    subprocess.run(
        ["docker", "build", "-f", "ops/docker/explorer.Dockerfile", "-t", "animica-explorer:test", "."],
        check=True,
        capture_output=True,
        cwd="/home/runner/work/all/all"
    )
    
    # Check the user
    output = run_docker_command("animica-explorer:test", ["id", "-u"])
    uid = int(output)
    
    # Should not be root (UID 0)
    assert uid != 0, "Explorer container should not run as root (UID 0)"
    # Should be the expected animica user (UID 10003)
    assert uid == 10003, f"Explorer container should run as UID 10003, got {uid}"


if __name__ == "__main__":
    # Run tests when executed directly
    pytest.main([__file__, "-v", "-m", "docker"])
