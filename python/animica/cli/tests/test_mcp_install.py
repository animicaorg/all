"""Tests for `animica mcp install` (5.2.0) — wiring the MCP server into clients."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from animica.cli.main import app as root_app

runner = CliRunner()


def test_install_merges_and_preserves(tmp_path):
    cfg = tmp_path / "claude.json"
    cfg.write_text(json.dumps({"mcpServers": {"existing": {"command": "foo"}}, "otherKey": 1}),
                   encoding="utf-8")
    res = runner.invoke(root_app, ["mcp", "install", "claude", "--config-path", str(cfg)])
    assert res.exit_code == 0, res.output
    data = json.loads(cfg.read_text())
    assert set(data["mcpServers"]) == {"existing", "animica"}  # existing preserved
    assert data["otherKey"] == 1                               # unrelated keys preserved
    assert data["mcpServers"]["animica"]["args"][-2:] == ["mcp", "serve"]


def test_install_vscode_uses_servers_key(tmp_path):
    cfg = tmp_path / "mcp.json"
    res = runner.invoke(root_app, ["mcp", "install", "vscode", "--config-path", str(cfg)])
    assert res.exit_code == 0, res.output
    data = json.loads(cfg.read_text())
    assert "servers" in data  # VS Code uses "servers", not "mcpServers"
    assert data["servers"]["animica"]["type"] == "stdio"


def test_install_custom_name(tmp_path):
    cfg = tmp_path / "cursor.json"
    res = runner.invoke(root_app, ["mcp", "install", "cursor", "--name", "anim2",
                                   "--config-path", str(cfg)])
    assert res.exit_code == 0
    assert "anim2" in json.loads(cfg.read_text())["mcpServers"]


def test_install_unknown_client_errors():
    res = runner.invoke(root_app, ["mcp", "install", "emacs"])
    assert res.exit_code != 0
    assert "unknown client" in res.output.lower()


def test_install_rejects_invalid_json(tmp_path):
    cfg = tmp_path / "broken.json"
    cfg.write_text("{not json", encoding="utf-8")
    res = runner.invoke(root_app, ["mcp", "install", "claude", "--config-path", str(cfg)])
    assert res.exit_code == 1
    assert "json" in res.output.lower()


def test_install_print_does_not_write(tmp_path):
    cfg = tmp_path / "p.json"
    res = runner.invoke(root_app, ["mcp", "install", "claude", "--config-path", str(cfg), "--print"])
    assert res.exit_code == 0
    assert not cfg.exists()  # --print must not write
    assert "mcpServers" in res.output
