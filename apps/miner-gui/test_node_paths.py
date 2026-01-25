from __future__ import annotations

import os
from pathlib import Path

from animica_miner_gui.backend import node_paths


def _make_executable(path: Path) -> None:
    path.write_text("stub", encoding="utf-8")
    os.chmod(path, 0o755)


def test_resolve_uses_env_override(tmp_path, monkeypatch) -> None:
    exe = tmp_path / "animica-node"
    _make_executable(exe)
    monkeypatch.setenv("ANIMICA_NODE_PATH", str(exe))
    monkeypatch.setattr(node_paths, "is_frozen", lambda: False)

    resolved = node_paths.resolve_node_executable()

    assert resolved.exe_path == exe
    assert resolved.mode == "dev"
    assert "ANIMICA_NODE_PATH" in resolved.reason


def test_resolve_repo_payload(tmp_path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    payload = (
        repo_root
        / "apps"
        / "miner-gui"
        / "animica_miner_gui"
        / "node"
        / "animica-node"
        / "animica-node"
    )
    payload.parent.mkdir(parents=True)
    _make_executable(payload)
    monkeypatch.delenv("ANIMICA_NODE_PATH", raising=False)
    monkeypatch.setattr(node_paths, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(node_paths, "is_frozen", lambda: False)

    resolved = node_paths.resolve_node_executable()

    assert resolved.exe_path == payload
    assert resolved.mode == "dev"
    assert resolved.base_dir == payload.parent


def test_resolve_dist_payload(tmp_path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    dist_payload = repo_root / "dist" / "animica-node" / "animica-node"
    dist_payload.parent.mkdir(parents=True)
    _make_executable(dist_payload)
    monkeypatch.delenv("ANIMICA_NODE_PATH", raising=False)
    monkeypatch.setattr(node_paths, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(node_paths, "is_frozen", lambda: False)

    resolved = node_paths.resolve_node_executable()

    assert resolved.exe_path == dist_payload
    assert resolved.mode == "dev"


def test_resolve_frozen_uses_bundle(tmp_path, monkeypatch) -> None:
    resources = tmp_path / "Resources"
    bundle_payload = resources / "node" / "animica-node" / "animica-node"
    bundle_payload.parent.mkdir(parents=True)
    _make_executable(bundle_payload)
    monkeypatch.delenv("ANIMICA_NODE_PATH", raising=False)
    monkeypatch.setattr(node_paths, "is_frozen", lambda: True)
    monkeypatch.setattr(node_paths, "get_resources_dir", lambda: resources)

    resolved = node_paths.resolve_node_executable()

    assert resolved.exe_path == bundle_payload
    assert resolved.mode == "frozen"
