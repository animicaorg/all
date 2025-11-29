from __future__ import annotations

import json
import os
from pathlib import Path

from animica.wallet_cli import DATA_DIR_NAME, WALLET_FILENAME, generate_wallet, main, write_wallet


def test_generate_wallet_has_expected_fields():
    wallet = generate_wallet()
    assert wallet.address.startswith("1") or wallet.address.startswith("3")
    assert len(wallet.public_key) in (66, 130)  # compressed hex length is 66
    assert wallet.wif.startswith("5") or wallet.wif.startswith("K") or wallet.wif.startswith("L")


def test_write_wallet_creates_file(tmp_path: Path):
    wallet = generate_wallet()
    wallet_path = write_wallet(wallet, tmp_path)

    assert wallet_path.exists()
    assert wallet_path.parent == tmp_path / DATA_DIR_NAME

    payload = json.loads(wallet_path.read_text(encoding="utf-8"))
    assert payload["address"] == wallet.address
    assert payload["wif"] == wallet.wif
    assert payload["public_key"] == wallet.public_key

    # POSIX-only check: ensure we wrote restrictive perms when possible.
    if os.name != "nt":
        mode = wallet_path.stat().st_mode & 0o777
        assert mode == 0o600


def test_cli_main_creates_wallet(tmp_path: Path, monkeypatch):
    # Run CLI against a temp directory to avoid touching the repo root.
    rc = main(["--root", str(tmp_path), "--force"])
    assert rc == 0

    wallet_file = tmp_path / DATA_DIR_NAME / WALLET_FILENAME
    assert wallet_file.exists()

    payload = json.loads(wallet_file.read_text(encoding="utf-8"))
    assert "address" in payload and payload["address"]
    assert "wif" in payload and payload["wif"].strip()
