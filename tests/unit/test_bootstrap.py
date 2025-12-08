# SPDX-License-Identifier: Apache-2.0
"""
Tests for core.bootstrap — password-gated mainnet genesis creation.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from core.bootstrap import (
    BOOTSTRAP_PASSWORD,
    bootstrap_mainnet_genesis,
    genesis_exists,
    validate_bootstrap_password,
)

def test_validate_bootstrap_password_correct():
    """Correct password passes validation."""
    assert validate_bootstrap_password(BOOTSTRAP_PASSWORD) is True

def test_validate_bootstrap_password_incorrect():
    """Incorrect password fails validation."""
    assert validate_bootstrap_password("wrong_password") is False

def test_validate_bootstrap_password_empty():
    """Empty password fails validation."""
    assert validate_bootstrap_password("") is False

def test_genesis_exists_false_for_nonexistent_db():
    """Genesis does not exist for a non-existent database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_uri = f"sqlite:///{tmpdir}/nonexistent.db"
        assert genesis_exists(db_uri) is False

def test_genesis_exists_false_for_fresh_db():
    """Genesis does not exist for a freshly created database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_uri = f"sqlite:///{tmpdir}/fresh.db"
        # Open the DB to create it but don't write genesis
        from core.db import open_kv
        kv = open_kv(db_uri)
        kv.close()
        assert genesis_exists(db_uri) is False

def test_bootstrap_mainnet_genesis_missing_file():
    """Bootstrap fails if genesis file does not exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        genesis_path = Path(tmpdir) / "missing.json"
        db_uri = f"sqlite:///{tmpdir}/test.db"

        # Should fail with exit code 2 (file not found)
        exit_code = bootstrap_mainnet_genesis(
            genesis_path, db_uri, skip_password=True, log_level="error"
        )
        assert exit_code == 2

def test_bootstrap_mainnet_genesis_incorrect_password():
    """Bootstrap fails with incorrect password for mainnet."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a minimal mainnet genesis file
        genesis_data = {
            "chainId": 1,  # Mainnet
            "genesisTime": "2025-01-01T00:00:00Z",
            "alloc": [],
            "economics": {"premineTotal": "0"},
            "consensus": {"initialThetaMicro": 1000000},
        }
        genesis_path = Path(tmpdir) / "genesis.json"
        genesis_path.write_text(json.dumps(genesis_data))

        db_uri = f"sqlite:///{tmpdir}/test.db"

        # Mock the password prompt to return incorrect password
        with patch("core.bootstrap.prompt_bootstrap_password", return_value="wrong"):
            exit_code = bootstrap_mainnet_genesis(
                genesis_path, db_uri, skip_password=False, log_level="error"
            )
            # Should fail with exit code 3 (password mismatch)
            assert exit_code == 3

def test_bootstrap_mainnet_genesis_correct_password():
    """Bootstrap succeeds with correct password for mainnet."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a minimal mainnet genesis file
        genesis_data = {
            "chainId": 1,  # Mainnet
            "genesisTime": "2025-01-01T00:00:00Z",
            "alloc": [],
            "economics": {"premineTotal": "0"},
            "consensus": {"initialThetaMicro": 1000000},
        }
        genesis_path = Path(tmpdir) / "genesis.json"
        genesis_path.write_text(json.dumps(genesis_data))

        db_uri = f"sqlite:///{tmpdir}/test.db"

        # Mock the password prompt to return correct password
        with patch("core.bootstrap.prompt_bootstrap_password", return_value=BOOTSTRAP_PASSWORD):
            with patch("core.bootstrap.load_genesis") as mock_load:
                # Mock load_genesis to return dummy data
                from core.types.header import Header
                from core.types.params import ChainParams, BlockLimits, RetargetParams, RetargetBounds

                params = ChainParams(
                    chain_id=1,
                    chain_name="Mainnet",
                    genesis_time="2025-01-01T00:00:00Z",
                    genesis_hash=b"\x00" * 32,
                    alg_policy_root=b"\x00" * 32,
                    poies_policy_root=b"\x00" * 32,
                    theta_initial=1000000,
                    gamma_total_cap=1000000,
                    retarget=RetargetParams(
                        window=100,
                        ema_alpha=0.1,
                        bounds=RetargetBounds(min=0.5, max=2.0),
                    ),
                    block=BlockLimits(
                        target_seconds=2.0,
                        max_bytes=1500000,
                        max_gas=20000000,
                        tx_max_bytes=131072,
                        min_gas_price=1000,
                    ),
                )
                header = Header.genesis(
                    chain_id=1,
                    timestamp=0,
                    state_root=b"\x00" * 32,
                    txs_root=b"\x00" * 32,
                    receipts_root=b"\x00" * 32,
                    proofs_root=b"\x00" * 32,
                    da_root=b"\x00" * 32,
                    mix_seed=b"\x00" * 32,
                    poies_policy_root=b"\x00" * 32,
                    pq_alg_policy_root=b"\x00" * 32,
                    theta_micro=1000000,
                    extra=b"",
                )
                mock_load.return_value = (params, header)

                with patch("core.bootstrap.finalize_genesis") as mock_finalize:
                    mock_finalize.return_value = (0, b"\x00" * 32)

                    exit_code = bootstrap_mainnet_genesis(
                        genesis_path, db_uri, skip_password=False, log_level="error"
                    )
                    # Should succeed (exit code 0)
                    assert exit_code == 0

def test_bootstrap_devnet_genesis_no_password_required():
    """Bootstrap for devnet (non-mainnet) does not require password."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a devnet genesis file (chain_id != 1)
        genesis_data = {
            "chainId": 1337,  # Devnet
            "genesisTime": "2025-01-01T00:00:00Z",
            "alloc": [],
            "economics": {"premineTotal": "0"},
            "consensus": {"initialThetaMicro": 1000000},
        }
        genesis_path = Path(tmpdir) / "genesis.json"
        genesis_path.write_text(json.dumps(genesis_data))

        db_uri = f"sqlite:///{tmpdir}/test.db"

        # Should succeed without password (skip_password=False but chain_id != 1)
        with patch("core.bootstrap.load_genesis") as mock_load:
            from core.types.header import Header
            from core.types.params import ChainParams, BlockLimits, RetargetParams, RetargetBounds

            params = ChainParams(
                chain_id=1337,
                chain_name="Devnet",
                genesis_time="2025-01-01T00:00:00Z",
                genesis_hash=b"\x00" * 32,
                alg_policy_root=b"\x00" * 32,
                poies_policy_root=b"\x00" * 32,
                theta_initial=1000000,
                gamma_total_cap=1000000,
                retarget=RetargetParams(
                    window=100,
                    ema_alpha=0.1,
                    bounds=RetargetBounds(min=0.5, max=2.0),
                ),
                block=BlockLimits(
                    target_seconds=2.0,
                    max_bytes=1500000,
                    max_gas=20000000,
                    tx_max_bytes=131072,
                    min_gas_price=1000,
                ),
            )
            header = Header.genesis(
                chain_id=1337,
                timestamp=0,
                state_root=b"\x00" * 32,
                txs_root=b"\x00" * 32,
                receipts_root=b"\x00" * 32,
                proofs_root=b"\x00" * 32,
                da_root=b"\x00" * 32,
                mix_seed=b"\x00" * 32,
                poies_policy_root=b"\x00" * 32,
                pq_alg_policy_root=b"\x00" * 32,
                theta_micro=1000000,
                extra=b"",
            )
            mock_load.return_value = (params, header)

            with patch("core.bootstrap.finalize_genesis") as mock_finalize:
                mock_finalize.return_value = (0, b"\x00" * 32)

                # No password prompt should occur (devnet)
                exit_code = bootstrap_mainnet_genesis(
                    genesis_path, db_uri, skip_password=False, log_level="error"
                )
                assert exit_code == 0
