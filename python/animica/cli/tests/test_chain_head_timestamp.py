from __future__ import annotations

from typer.testing import CliRunner

from animica.cli import chain

runner = CliRunner()


def test_chain_head_keeps_zero_timestamp(monkeypatch) -> None:
    monkeypatch.setattr(
        chain,
        "_try_rpc",
        lambda *args, **kwargs: {
            "height": 3,
            "hash": "0xabc",
            "timestamp": 0,
        },
    )

    result = runner.invoke(chain.app, ["head"])

    assert result.exit_code == 0
    assert "Timestamp: 0" in result.stdout
