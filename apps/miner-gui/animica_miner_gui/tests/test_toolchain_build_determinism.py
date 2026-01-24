from __future__ import annotations

import shutil
from pathlib import Path

from animica_miner_gui.ide.toolchain.builder import build_contract


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def test_build_produces_same_hash_twice(tmp_path: Path) -> None:
    template_dir = _repo_root() / "templates" / "contract-python-ide-sample"
    project_dir = tmp_path / "project"
    shutil.copytree(template_dir, project_dir)

    first = build_contract(project_dir)
    assert first.success
    assert first.artifacts is not None

    second = build_contract(project_dir)
    assert second.success
    assert second.artifacts is not None

    assert first.artifacts.code_hash == second.artifacts.code_hash
