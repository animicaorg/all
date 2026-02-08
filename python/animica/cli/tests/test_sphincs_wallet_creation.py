"""
Test that SPHINCS+ wallet creation works correctly.
This test validates the fix for equal-sized keys in SPHINCS+ (pk=64, sk=64).
"""
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from animica.cli import wallet

runner = CliRunner()


@pytest.fixture(autouse=True)
def allow_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable PQ pure fallback for testing."""
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")


def test_sphincs_wallet_creation_with_equal_key_sizes(tmp_path: Path) -> None:
    """
    Test that SPHINCS+ wallet creation succeeds despite having equal-sized keys.
    
    SPHINCS+ SHAKE-128s has pk=64 and sk=64 bytes, which is valid for this algorithm.
    The validation logic should allow this.
    """
    wallet_file = tmp_path / "test_sphincs.json"
    
    # Create a wallet with SPHINCS+ algorithm explicitly
    result = runner.invoke(
        wallet.app,
        [
            "--wallet-file", str(wallet_file),
            "create",
            "--label", "sphincs_test",
            "--alg", "sphincs_shake_128s",
            "--allow-insecure-fallback",
        ],
    )
    
    # Should succeed
    assert result.exit_code == 0, f"Command failed: {result.output}"
    assert "Wallet created" in result.output
    assert "sphincs_shake_128s" in result.output
    
    # Verify the wallet was created correctly
    assert wallet_file.exists()
    store = json.loads(wallet_file.read_text())
    assert store["version"] == 1
    assert len(store["wallets"]) == 1
    
    entry = store["wallets"][0]
    assert entry["label"] == "sphincs_test"
    assert entry["alg_name"] == "sphincs_shake_128s"
    assert entry["address"].startswith("anim1")
    
    # Verify key sizes are as expected for SPHINCS+
    public_key_hex = entry["public_key_hex"]
    secret_key_hex = entry["secret_key_hex"]
    
    # Each byte is 2 hex characters
    assert len(public_key_hex) == 64 * 2  # 64 bytes = 128 hex chars
    assert len(secret_key_hex) == 64 * 2  # 64 bytes = 128 hex chars
    
    # Keys should not be identical (different check)
    assert public_key_hex != secret_key_hex


def test_dilithium3_wallet_creation_with_different_key_sizes(tmp_path: Path) -> None:
    """
    Test that Dilithium3 wallet creation still works with different key sizes.
    
    Dilithium3 has pk=1952 and sk=4000 bytes (sk > pk), which should also be valid.
    """
    wallet_file = tmp_path / "test_dilithium3.json"
    
    # Create a wallet with Dilithium3 algorithm explicitly
    result = runner.invoke(
        wallet.app,
        [
            "--wallet-file", str(wallet_file),
            "create",
            "--label", "dilithium3_test",
            "--alg", "dilithium3",
            "--allow-insecure-fallback",
        ],
    )
    
    # Should succeed
    assert result.exit_code == 0, f"Command failed: {result.output}"
    assert "Wallet created" in result.output
    
    # Verify the wallet was created correctly
    assert wallet_file.exists()
    store = json.loads(wallet_file.read_text())
    assert len(store["wallets"]) == 1
    
    entry = store["wallets"][0]
    assert entry["label"] == "dilithium3_test"
    assert entry["alg_name"] == "dilithium3"
    
    # Verify key sizes are as expected for Dilithium3
    public_key_hex = entry["public_key_hex"]
    secret_key_hex = entry["secret_key_hex"]
    
    # Dilithium3 should have different key sizes (sk > pk)
    # Note: actual sizes depend on normalization, but secret should be larger
    assert len(secret_key_hex) > len(public_key_hex)
