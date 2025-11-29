from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from animica.cli import wallet

runner = CliRunner()


def run_cli(args: list[str], wallet_file: Path) -> str:
    result = runner.invoke(wallet.app, ["--wallet-file", str(wallet_file)] + args)
    assert result.exit_code == 0, result.output
    return result.output


def test_wallet_new_and_list(tmp_path: Path) -> None:
    wallet_file = tmp_path / "wallets.json"
    output = run_cli(["new", "--label", "dev1"], wallet_file)
    assert "dev1" in output

    list_output = run_cli(["list"], wallet_file)
    assert "dev1" in list_output


def test_wallet_default_and_env(tmp_path: Path) -> None:
    wallet_file = tmp_path / "wallets.json"
    run_cli(["new", "--label", "dev1"], wallet_file)
    store = json.loads(wallet_file.read_text())
    address = store["wallets"][0]["address"]

    default_output = run_cli(["default", "--address", address], wallet_file)
    assert address in default_output

    env_output = run_cli(["env"], wallet_file)
    assert f"ANIMICA_DEFAULT_ADDRESS={address}" in env_output


def test_wallet_show_and_export(tmp_path: Path) -> None:
    wallet_file = tmp_path / "wallets.json"
    run_cli(["new", "--label", "dev1"], wallet_file)
    store = json.loads(wallet_file.read_text())
    address = store["wallets"][0]["address"]

    show_output = run_cli(["show", "--address", address], wallet_file)
    data = json.loads(show_output)
    assert data["address"] == address

    export_path = tmp_path / "export.json"
    export_output = run_cli(["export", "--address", address, "--out", str(export_path)], wallet_file)
    assert export_path.exists()
    exported = json.loads(export_path.read_text())
    assert exported["address"] == address
    assert "Exported" in export_output


def test_wallet_import(tmp_path: Path) -> None:
    wallet_file = tmp_path / "wallets.json"
    run_cli(["new", "--label", "dev1"], wallet_file)
    store = json.loads(wallet_file.read_text())
    entry = store["wallets"][0]
    imported_path = tmp_path / "import.json"
    imported_path.write_text(json.dumps(entry))

    import_output = run_cli(["import", "--file", str(imported_path), "--label", "dev2", "--force"], wallet_file)
    assert "dev2" in import_output
    store = json.loads(wallet_file.read_text())
    assert store["wallets"][0]["label"] == "dev2"
