from __future__ import annotations

import pytest

pytest.importorskip("typer")

from typer.testing import CliRunner

from animica.cli import chain

runner = CliRunner()


def test_chain_reset_dry_run_default() -> None:
    result = runner.invoke(chain.app, ["reset"])
    assert result.exit_code == 0
    assert "dry-run" in result.output.lower()


def test_chain_reset_force_invokes_workflow(monkeypatch) -> None:
    calls = []

    def _fake_run(cmd, check=False, **kwargs):
        calls.append(cmd)
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(chain.subprocess, "run", _fake_run)
    monkeypatch.setattr(chain, "_try_rpc", lambda *args, **kwargs: {"height": 0, "hash": "0x00"})

    result = runner.invoke(chain.app, ["reset", "--force"])
    assert result.exit_code == 0
    assert any(cmd[:3] == ["animica", "node", "down"] for cmd in calls)
    assert any(cmd[:3] == ["animica", "node", "up"] for cmd in calls)
