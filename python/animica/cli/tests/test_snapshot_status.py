"""Tests for snapshot status CLI output."""

from __future__ import annotations

from unittest.mock import AsyncMock

from typer.testing import CliRunner

from animica.cli.main import app
from animica.cli import snapshot as snapshot_cli

runner = CliRunner()


def test_snapshot_status_no_snapshots_message(monkeypatch) -> None:
    """Status command should not raise and should explain no snapshots yet."""
    mock_result = {
        "success": True,
        "orchestrator_running": True,
        "config": {
            "interval": 2000,
            "auto_create": True,
            "max_snapshots": 10,
            "sync_enabled": True,
        },
        "status": {
            "healthy": True,
            "total_snapshots": 0,
            "last_snapshot_height": 0,
            "last_health_check": 0,
            "head_height": 459,
            "next_snapshot_height": 2000,
        },
        "statistics": {
            "snapshots_created": 0,
            "snapshots_deleted": 0,
            "snapshots_failed": 0,
            "sync_attempts": 0,
            "sync_successes": 0,
        },
        "errors": [],
        "warnings": [],
        "snapshots": [],
    }
    monkeypatch.setattr(snapshot_cli, "rpc_call", AsyncMock(return_value=mock_result))

    result = runner.invoke(app, ["snapshot", "status"])

    assert result.exit_code == 0
    assert "No snapshots yet" in result.stdout
    assert "Next snapshot" in result.stdout
