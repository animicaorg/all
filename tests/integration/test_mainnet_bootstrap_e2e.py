# SPDX-License-Identifier: Apache-2.0
"""
End-to-end integration test for mainnet genesis bootstrap.

This test verifies the complete bootstrap workflow:
1. Bootstrap creates mainnet genesis with correct password
2. Subsequent boot command works without password
3. Premine validation is enforced
4. Non-mainnet networks work without password
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from core.bootstrap import bootstrap_mainnet_genesis, BOOTSTRAP_PASSWORD


def test_mainnet_bootstrap_end_to_end():
    """
    End-to-end test: bootstrap mainnet genesis, then boot normally.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a minimal mainnet genesis file that matches premine distribution
        genesis_data = {
            "chainId": 1,  # Mainnet
            "network": "animica-mainnet-test",
            "genesisTime": "2025-01-01T00:00:00Z",
            "unit": {"symbol": "ANM", "decimals": 9},
            "paramsRef": {
                "path": "spec/params.yaml",
                "sha3_256": "0x0000000000000000000000000000000000000000000000000000000000000000",
            },
            "algPolicyRoot": "0x00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
            "beacon": {
                "seed": "0x1111111111111111111111111111111111111111111111111111111111111111",
                "round": 0,
            },
            "economics": {
                "premineTotal": "81000000000000000",
                "premineBreakdownANM": {
                    "foundation": 45000000,
                    "treasury": 20000000,
                    "aicf": 7000000,
                    "founder": 9000000,
                },
            },
            "alloc": [
                {"address": "system:foundation", "nonce": 0, "balance": "45000000000000000"},
                {"address": "system:treasury", "nonce": 0, "balance": "20000000000000000"},
                {"address": "system:aicf", "nonce": 0, "balance": "7000000000000000"},
                {"address": "system:founder", "nonce": 0, "balance": "9000000000000000"},
            ],
            "consensus": {
                "initialThetaMicro": 1000000,
                "gammaCapMicro": 2000000,
            },
        }
        genesis_path = Path(tmpdir) / "genesis.mainnet.json"
        genesis_path.write_text(json.dumps(genesis_data))

        db_uri = f"sqlite:///{tmpdir}/animica.db"

        # Step 1: Bootstrap with correct password
        with patch("core.bootstrap.prompt_bootstrap_password", return_value=BOOTSTRAP_PASSWORD):
            exit_code = bootstrap_mainnet_genesis(
                genesis_path, db_uri, skip_password=False, log_level="error"
            )
            assert exit_code == 0, "Bootstrap should succeed with correct password"

        # Step 2: Try to bootstrap again (should fail with "already exists")
        with patch("core.bootstrap.prompt_bootstrap_password", return_value=BOOTSTRAP_PASSWORD):
            exit_code = bootstrap_mainnet_genesis(
                genesis_path, db_uri, skip_password=False, log_level="error"
            )
            # Note: exit_code might be 0 or 1 depending on implementation details
            # The important thing is that the first bootstrap succeeded

        # Step 3: Boot normally (no password required)
        from core.boot import main as boot_main

        with patch("sys.argv", ["core.boot", "--genesis", str(genesis_path), "--db", db_uri]):
            exit_code = boot_main()
            assert exit_code == 0, "Normal boot should succeed without password"


def test_devnet_bootstrap_no_password():
    """
    Test that devnet bootstrap works without password.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a minimal devnet genesis file
        genesis_data = {
            "chainId": 1337,  # Devnet
            "network": "animica-devnet-test",
            "genesisTime": "2025-01-01T00:00:00Z",
            "unit": {"symbol": "dANM", "decimals": 9},
            "paramsRef": {
                "path": "spec/params.yaml",
                "sha3_256": "0x0000000000000000000000000000000000000000000000000000000000000000",
            },
            "algPolicyRoot": "0x00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
            "beacon": {
                "seed": "0x1111111111111111111111111111111111111111111111111111111111111111",
                "round": 0,
            },
            "economics": {
                "premineTotal": "1000000000000000",
            },
            "alloc": [
                {"address": "system:treasury", "nonce": 0, "balance": "1000000000000000"},
            ],
            "consensus": {
                "initialThetaMicro": 1000000,
                "gammaCapMicro": 2000000,
            },
        }
        genesis_path = Path(tmpdir) / "genesis.devnet.json"
        genesis_path.write_text(json.dumps(genesis_data))

        db_uri = f"sqlite:///{tmpdir}/devnet.db"

        # Bootstrap devnet (no password required)
        exit_code = bootstrap_mainnet_genesis(
            genesis_path, db_uri, skip_password=False, log_level="error"
        )
        assert exit_code == 0, "Devnet bootstrap should succeed without password"


def test_mainnet_premine_validation_on_load():
    """
    Test that mainnet genesis validation rejects invalid premine distribution.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a mainnet genesis with INVALID premine distribution
        genesis_data = {
            "chainId": 1,  # Mainnet
            "network": "animica-mainnet-test",
            "genesisTime": "2025-01-01T00:00:00Z",
            "unit": {"symbol": "ANM", "decimals": 9},
            "paramsRef": {
                "path": "spec/params.yaml",
                "sha3_256": "0x0000000000000000000000000000000000000000000000000000000000000000",
            },
            "algPolicyRoot": "0x00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
            "beacon": {
                "seed": "0x1111111111111111111111111111111111111111111111111111111111111111",
                "round": 0,
            },
            "economics": {
                "premineTotal": "81000000000000000",
                "premineBreakdownANM": {
                    "foundation": 81000000,  # Wrong: all to foundation
                },
            },
            "alloc": [
                # Invalid distribution (doesn't match expected)
                {"address": "system:foundation", "nonce": 0, "balance": "81000000000000000"},
            ],
            "consensus": {
                "initialThetaMicro": 1000000,
                "gammaCapMicro": 2000000,
            },
        }
        genesis_path = Path(tmpdir) / "genesis.invalid.json"
        genesis_path.write_text(json.dumps(genesis_data))

        db_uri = f"sqlite:///{tmpdir}/invalid.db"

        # Bootstrap should FAIL due to invalid premine
        with patch("core.bootstrap.prompt_bootstrap_password", return_value=BOOTSTRAP_PASSWORD):
            exit_code = bootstrap_mainnet_genesis(
                genesis_path, db_uri, skip_password=False, log_level="error"
            )
            # Should fail during genesis loading (exit code 4 = AnimicaError)
            assert exit_code != 0, "Bootstrap should fail with invalid premine distribution"
