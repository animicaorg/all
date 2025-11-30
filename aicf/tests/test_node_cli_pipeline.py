import json
import subprocess
import sys
from pathlib import Path


def _run_cli(cmd: list[str]) -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "aicf.cli.node_pipeline", *cmd],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def test_cli_pipeline_roundtrip(tmp_path: Path) -> None:
    datadir = tmp_path / "state"

    status = json.loads(_run_cli(["status", "--datadir", str(datadir), "--json"]))
    assert status["height"] == 0

    new_height = int(_run_cli(["mine", "--count", "2", "--datadir", str(datadir)]))
    assert new_height >= 2

    toggled_on = _run_cli(["auto", "true", "--datadir", str(datadir)])
    assert toggled_on == "on"
    toggled_off = _run_cli(["auto", "false", "--datadir", str(datadir)])
    assert toggled_off == "off"

    block_one = json.loads(
        _run_cli(["block", "1", "--datadir", str(datadir), "--json"])
    )
    assert block_one["number"] == hex(1)

    summary = json.loads(
        _run_cli(["pipeline", "--datadir", str(datadir), "--mine", "1", "--json"])
    )
    assert summary["endHeight"] >= new_height + 1
    assert summary["headHash"]
