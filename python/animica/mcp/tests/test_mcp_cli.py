"""CLI tests for ``animica mcp`` (typer app).

These never start a transport. ``tools`` and ``info`` must work WITHOUT the
optional ``mcp`` SDK; ``serve`` must fail cleanly (exit 2 + pip hint) when the
SDK is unavailable — which we force here by poisoning the import, so the test
holds whether or not ``mcp`` is installed.
"""

from __future__ import annotations

import json
import sys

from typer.testing import CliRunner

from animica.cli.mcp import app

runner = CliRunner()


def test_tools_json_lists_all(monkeypatch):
    res = runner.invoke(app, ["tools", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    names = {t["name"] for t in data}
    assert {"animica_ai_ask", "animica_quantum_draw", "animica_studio_estimate"} <= names
    assert len(data) == 15


def test_tools_table_renders():
    res = runner.invoke(app, ["tools"])
    assert res.exit_code == 0
    assert "animica_ai_ask" in res.output
    assert "READ + COMPUTE" in res.output


def test_info_shows_install_and_both_ecosystems():
    res = runner.invoke(app, ["info"])
    assert res.exit_code == 0
    assert 'animica[mcp]' in res.output
    assert "stdio" in res.output  # Claude Code
    assert "http" in res.output.lower()  # OpenAI Apps SDK


def test_serve_prints_clean_hint_when_sdk_missing(monkeypatch):
    # Force `from mcp.server.fastmcp import FastMCP` to raise ImportError even if
    # the SDK is installed, so we always exercise the McpNotInstalled hint path.
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", None)
    res = runner.invoke(app, ["serve", "--transport", "stdio"])
    assert res.exit_code == 2
    assert 'pip install "animica[mcp]"' in res.output
