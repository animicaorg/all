import json
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

from animica.cli import wallet

runner = CliRunner()


def run_cli(args: list[str], wallet_file: Path) -> str:
    result = runner.invoke(wallet.app, ["--wallet-file", str(wallet_file)] + args)
    assert result.exit_code == 0, result.output
    return result.output


@pytest.fixture(autouse=True)
def allow_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")


def test_wallet_create_and_list(tmp_path: Path) -> None:
    wallet_file = tmp_path / "wallets.json"
    output = run_cli(["create", "--label", "dev1", "--allow-insecure-fallback"], wallet_file)
    assert "Wallet created" in output

    store = json.loads(wallet_file.read_text())
    address = store["wallets"][0]["address"]
    assert address.startswith("anim1")

    list_output = run_cli(["list"], wallet_file)
    assert "dev1" in list_output
    assert address in list_output


@respx.mock
def test_wallet_show_with_balance(tmp_path: Path) -> None:
    wallet_file = tmp_path / "wallets.json"
    run_cli(["create", "--label", "dev1", "--allow-insecure-fallback"], wallet_file)
    store = json.loads(wallet_file.read_text())
    address = store["wallets"][0]["address"]

    rpc_url = "http://localhost:9999/rpc"
    respx.post(rpc_url).respond(json={"jsonrpc": "2.0", "id": 1, "result": "0x05"})

    show_output = run_cli(["show", "--address", address, "--rpc-url", rpc_url], wallet_file)
    data = json.loads(show_output)
    assert data["address"] == address
    assert data["balance"] == 5


def test_wallet_export_and_import(tmp_path: Path) -> None:
    wallet_file = tmp_path / "wallets.json"
    run_cli(["create", "--label", "dev1", "--allow-insecure-fallback"], wallet_file)
    store = json.loads(wallet_file.read_text())
    address = store["wallets"][0]["address"]

    export_path = tmp_path / "export.json"
    export_output = run_cli(["export", "--address", address, "--out", str(export_path)], wallet_file)
    assert "Exported" in export_output

    import_output = run_cli([
        "import",
        "--file",
        str(export_path),
        "--label",
        "dev2",
        "--force",
    ], wallet_file)
    assert "dev2" in import_output

    store = json.loads(wallet_file.read_text())
    assert store["wallets"][0]["label"] == "dev2"


def test_wallet_default_and_env(tmp_path: Path) -> None:
    wallet_file = tmp_path / "wallets.json"
    run_cli(["create", "--label", "dev1", "--allow-insecure-fallback"], wallet_file)
    store = json.loads(wallet_file.read_text())
    address = store["wallets"][0]["address"]

    default_output = run_cli(["set-default", "--address", address], wallet_file)
    assert address in default_output

    env_output = run_cli(["env"], wallet_file)
    assert f"ANIMICA_DEFAULT_ADDRESS={address}" in env_output
