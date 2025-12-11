import json
from pathlib import Path
from typing import Optional

import pytest
import respx
from animica.cli import wallet
from typer.testing import CliRunner

runner = CliRunner()


def run_cli(
    args: list[str],
    wallet_file: Optional[Path] = None,
    expect_success: bool = True,
) -> str:
    """
    Run CLI with optional wallet file override.
    If wallet_file is None, tests the default path behavior.
    """
    cli_args = args if wallet_file is None else ["--wallet-file", str(wallet_file)] + args
    result = runner.invoke(wallet.app, cli_args)
    if expect_success:
        assert result.exit_code == 0, f"Command failed: {result.output}"
    return result.output


@pytest.fixture(autouse=True)
def allow_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")


@pytest.fixture
def premine_wallet_store(tmp_path: Path) -> Path:
    """Create a wallet store with a premine wallet entry."""
    wallet_file = tmp_path / "wallets.json"
    store = {
        "version": 1,
        "wallets": [
            {
                "label": "premine",
                "address": "anim1zqp2nx50902d7jgrzk0ep798r2vhpgt3rhtmn89gadzdgyhf9hmln7g9e4xt9",
                "alg_id": 4098,
                "alg_name": "sphincs_shake_128s",
                "public_key_hex": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
                "secret_key_hex": "0011223344556677889900112233445566778899001122334455667788990011",
                "created_at": "2025-01-01T00:00:00Z"
            },
            {
                "label": "alice",
                "address": "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
                "alg_id": 4098,
                "alg_name": "sphincs_shake_128s",
                "public_key_hex": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "secret_key_hex": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "created_at": "2025-01-02T00:00:00Z"
            }
        ]
    }
    wallet_file.write_text(json.dumps(store, indent=2))
    return wallet_file


def test_wallet_create_and_list(tmp_path: Path) -> None:
    wallet_file = tmp_path / "wallets.json"
    output = run_cli(
        ["create", "--label", "dev1", "--allow-insecure-fallback"], wallet_file
    )
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

    show_output = run_cli(
        ["show", "--address", address, "--rpc-url", rpc_url], wallet_file
    )
    data = json.loads(show_output)
    assert data["address"] == address
    assert data["balance"] == 5


def test_wallet_export_and_import(tmp_path: Path) -> None:
    wallet_file = tmp_path / "wallets.json"
    run_cli(["create", "--label", "dev1", "--allow-insecure-fallback"], wallet_file)
    store = json.loads(wallet_file.read_text())
    address = store["wallets"][0]["address"]

    export_path = tmp_path / "export.json"
    export_output = run_cli(
        ["export", "--address", address, "--out", str(export_path)], wallet_file
    )
    assert "Exported" in export_output

    import_output = run_cli(
        [
            "import",
            "--file",
            str(export_path),
            "--label",
            "dev2",
            "--force",
        ],
        wallet_file,
    )
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


# New tests for positional arguments and enhanced lookup
@respx.mock
def test_wallet_show_by_address_positional(premine_wallet_store: Path) -> None:
    """Test showing wallet by address using positional argument."""
    rpc_url = "http://localhost:9999/rpc"
    respx.post(rpc_url).respond(json={"jsonrpc": "2.0", "id": 1, "result": "0x0a"})
    
    address = "anim1zqp2nx50902d7jgrzk0ep798r2vhpgt3rhtmn89gadzdgyhf9hmln7g9e4xt9"
    output = run_cli(["show", address, "--rpc-url", rpc_url], premine_wallet_store)
    data = json.loads(output)
    assert data["address"] == address
    assert data["label"] == "premine"
    assert data["balance"] == 10


@respx.mock
def test_wallet_show_by_label(premine_wallet_store: Path) -> None:
    """Test showing wallet by label."""
    rpc_url = "http://localhost:9999/rpc"
    respx.post(rpc_url).respond(json={"jsonrpc": "2.0", "id": 1, "result": "0x0a"})
    
    output = run_cli(["show", "premine", "--rpc-url", rpc_url], premine_wallet_store)
    data = json.loads(output)
    assert data["label"] == "premine"
    assert data["address"] == "anim1zqp2nx50902d7jgrzk0ep798r2vhpgt3rhtmn89gadzdgyhf9hmln7g9e4xt9"


@respx.mock
def test_wallet_show_by_public_key_hex(premine_wallet_store: Path) -> None:
    """Test showing wallet by public key hex."""
    rpc_url = "http://localhost:9999/rpc"
    respx.post(rpc_url).respond(json={"jsonrpc": "2.0", "id": 1, "result": "0x14"})
    
    pubkey = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    output = run_cli(["show", pubkey, "--rpc-url", rpc_url], premine_wallet_store)
    data = json.loads(output)
    assert data["public_key_hex"] == pubkey
    assert data["label"] == "premine"


def test_wallet_show_not_found(premine_wallet_store: Path) -> None:
    """Test that non-existent wallet lookup fails gracefully."""
    output = run_cli(
        ["show", "nonexistent"],
        premine_wallet_store,
        expect_success=False
    )
    assert "Wallet not found" in output


def test_wallet_export_by_label(premine_wallet_store: Path, tmp_path: Path) -> None:
    """Test exporting wallet by label using positional argument."""
    export_path = tmp_path / "export.json"
    output = run_cli(
        ["export", "alice", "--out", str(export_path)],
        premine_wallet_store
    )
    assert "Exported" in output
    
    exported = json.loads(export_path.read_text())
    assert exported["label"] == "alice"
    assert exported["address"] == "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km"


def test_wallet_set_default_by_label(premine_wallet_store: Path) -> None:
    """Test setting default wallet by label using positional argument."""
    output = run_cli(["set-default", "alice"], premine_wallet_store)
    assert "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km" in output
    
    store = json.loads(premine_wallet_store.read_text())
    assert store["default_address"] == "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km"


def test_wallet_default_path_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test that wallet file defaults to ~/.animica/wallets.json."""
    # Set HOME to tmp_path for isolation
    animica_dir = tmp_path / ".animica"
    animica_dir.mkdir()
    default_wallet_file = animica_dir / "wallets.json"
    
    # Temporarily override HOME
    monkeypatch.setenv("HOME", str(tmp_path))
    
    # Create a wallet without --wallet-file (should use default)
    output = run_cli(
        ["create", "--label", "testdefault", "--allow-insecure-fallback"],
        wallet_file=None  # No override, use default
    )
    assert "Wallet created" in output
    assert str(default_wallet_file) in output
    
    # Verify wallet was created in the default location
    assert default_wallet_file.exists()
    store = json.loads(default_wallet_file.read_text())
    assert len(store["wallets"]) == 1
    assert store["wallets"][0]["label"] == "testdefault"


def test_wallet_env_var_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test that ANIMICA_WALLETS_FILE env var overrides default path."""
    custom_wallet_file = tmp_path / "custom_wallets.json"
    monkeypatch.setenv("ANIMICA_WALLETS_FILE", str(custom_wallet_file))
    
    # Create wallet - should use env var path
    output = run_cli(
        ["create", "--label", "envtest", "--allow-insecure-fallback"],
        wallet_file=None  # No CLI override
    )
    assert "Wallet created" in output
    
    # Verify wallet was created in custom location
    assert custom_wallet_file.exists()
    store = json.loads(custom_wallet_file.read_text())
    assert store["wallets"][0]["label"] == "envtest"


def test_wallet_cli_flag_overrides_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test that --wallet-file flag overrides ANIMICA_WALLETS_FILE env var."""
    env_wallet_file = tmp_path / "env_wallets.json"
    cli_wallet_file = tmp_path / "cli_wallets.json"
    
    monkeypatch.setenv("ANIMICA_WALLETS_FILE", str(env_wallet_file))
    
    # Create wallet with explicit --wallet-file flag
    output = run_cli(
        ["create", "--label", "cliflag", "--allow-insecure-fallback"],
        wallet_file=cli_wallet_file
    )
    assert "Wallet created" in output
    
    # Verify wallet was created in CLI-specified location, not env var location
    assert cli_wallet_file.exists()
    assert not env_wallet_file.exists()
    store = json.loads(cli_wallet_file.read_text())
    assert store["wallets"][0]["label"] == "cliflag"


def test_wallet_show_missing_identifier_error() -> None:
    """Test that show command without identifier shows helpful error."""
    output = run_cli(["show"], wallet_file=None, expect_success=False)
    assert "Missing wallet identifier" in output
    assert "Usage:" in output


# ============================================================================
# PQ Dependency Tests
# ============================================================================

def test_wallet_create_missing_pq_deps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that wallet create fails with helpful message when PQ deps are missing."""
    wallet_file = tmp_path / "wallets.json"
    
    # Remove the ANIMICA_UNSAFE_PQ_FAKE env var to simulate missing deps
    # Note: ANIMICA_ALLOW_PQ_PURE_FALLBACK is not checked by check_pq_signing_available()
    monkeypatch.delenv("ANIMICA_UNSAFE_PQ_FAKE", raising=False)
    
    result = runner.invoke(wallet.app, [
        "--wallet-file", str(wallet_file),
        "create",
        "--label", "production-wallet"
        # Note: NOT using --allow-insecure-fallback
    ])
    
    # Should exit with error
    assert result.exit_code == 1
    # Should contain helpful error message
    assert "Post-quantum signing dependencies not available" in result.output
    assert "python-oqs" in result.output
    assert "liboqs" in result.output
    # Should mention the --allow-insecure-fallback option for dev/test
    assert "allow-insecure-fallback" in result.output


def test_wallet_create_with_insecure_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that wallet create works with --allow-insecure-fallback even when PQ deps are missing."""
    wallet_file = tmp_path / "wallets.json"
    
    # Remove ANIMICA_UNSAFE_PQ_FAKE to simulate missing deps, but use --allow-insecure-fallback flag
    monkeypatch.delenv("ANIMICA_UNSAFE_PQ_FAKE", raising=False)
    
    output = run_cli(
        ["create", "--label", "dev-wallet", "--allow-insecure-fallback"],
        wallet_file
    )
    
    # Should succeed
    assert "Wallet created" in output
    assert "dev-wallet" in output
    
    # Verify wallet was created
    store = json.loads(wallet_file.read_text())
    assert len(store["wallets"]) == 1
    assert store["wallets"][0]["label"] == "dev-wallet"
