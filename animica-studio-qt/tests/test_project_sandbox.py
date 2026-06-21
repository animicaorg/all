"""ProjectService sandbox / path-traversal tests (no network, no display)."""
from __future__ import annotations

from pathlib import Path

import pytest

from animica_studio.services.project_service import ProjectService, SandboxError


@pytest.fixture()
def service(project_dir: Path) -> ProjectService:
    svc = ProjectService()
    svc.open_project(str(project_dir))
    return svc


def test_open_sets_root(service: ProjectService, project_dir: Path) -> None:
    assert service.is_open() is True
    assert service.root == project_dir.resolve()


def test_resolve_normal_path(service: ProjectService, project_dir: Path) -> None:
    resolved = service.resolve("src/app.py")
    assert resolved == (project_dir.resolve() / "src" / "app.py")


@pytest.mark.parametrize(
    "bad",
    [
        "../secret.txt",
        "../../etc/passwd",
        "src/../../escape.py",
        "a/b/../../../outside",
    ],
)
def test_resolve_rejects_traversal(service: ProjectService, bad: str) -> None:
    with pytest.raises(SandboxError):
        service.resolve(bad)


def test_resolve_rejects_absolute_outside(service: ProjectService) -> None:
    with pytest.raises(SandboxError):
        service.resolve("/etc/passwd")


def test_read_write_within_sandbox(service: ProjectService) -> None:
    assert "hello" in service.read_file("src/app.py")
    service.write_file("notes/todo.md", "# notes\n")
    assert service.read_file("notes/todo.md") == "# notes\n"


def test_write_outside_sandbox_rejected(service: ProjectService) -> None:
    with pytest.raises(SandboxError):
        service.write_file("../escape.md", "nope")


def test_file_tree_ignores_globs(service: ProjectService) -> None:
    tree = service.file_tree("")
    names = {node["name"] for node in tree}
    # __pycache__ is an ignore glob and must be excluded.
    assert "__pycache__" not in names
    assert "src" in names
    assert "README.md" in names


def test_detect_project_type(service: ProjectService) -> None:
    # pyproject.toml present → python.
    assert service.detect_project_type() == "python"


def test_resolve_requires_open_project() -> None:
    svc = ProjectService()
    with pytest.raises(Exception):
        svc.resolve("anything.py")
