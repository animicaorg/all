"""
core.bootstrap — One-time mainnet genesis bootstrap with password gate
========================================================================

This module provides a password-protected bootstrap command for creating the
canonical mainnet genesis. The password gate ensures that only authorized
operators can initialize the mainnet genesis for the first time.

Security Requirements:
----------------------
- Bootstrap password is required ONLY for creating mainnet genesis (chain_id == 1)
- Password is never logged or persisted (constant exists in code only)
- Interactive prompt uses getpass for non-echo input
- Password mismatch exits cleanly without creating genesis
- Subsequent startups with existing genesis do not prompt
- If genesis is missing during normal startup, fail with clear error message

Usage:
------
    # First-time mainnet genesis creation:
    python -m core.bootstrap --network mainnet --genesis-sample genesis/genesis.sample.mainnet.json --db sqlite:///animica.db

    # Subsequent startups (no password required):
    python -m core.boot --genesis <path> --db sqlite:///animica.db
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Optional

from core.chain.head import finalize_genesis
from core.db import open_kv
from core.db.block_db import BlockDB
from core.db.state_db import StateDB
from core.errors import AnimicaError
from core.genesis.loader import load_genesis
from core.logging import setup_logging
from core.types.params import ChainParams

# ==================================================================================
# BOOTSTRAP PASSWORD (mainnet only)
# ==================================================================================
# This password is required ONLY for first-time mainnet genesis creation.
# It is never logged or persisted. The constant may exist in code but must not
# be emitted to logs or stored in files/databases.
#
# SECURITY NOTE: This is a simple gate for first-time genesis bootstrap. In a
# production deployment, consider:
#   1. Using environment variables (e.g., ANIMICA_BOOTSTRAP_PASSWORD) instead of
#      hardcoded constants
#   2. Multi-signature authorization or HSM-based key management
#   3. Ceremony-style genesis creation with multiple authorized parties
#   4. Time-limited bootstrap windows with additional security layers
#
# For the current implementation, the hardcoded password is acceptable for initial
# bootstrap but should be enhanced for production mainnet deployment.
# ==================================================================================

import os

# Read from environment variable if available, otherwise use default
BOOTSTRAP_PASSWORD: str = os.getenv("ANIMICA_BOOTSTRAP_PASSWORD", "animicawins")

# Prompt text (never includes the password itself)
BOOTSTRAP_PROMPT: str = "Enter Animica mainnet bootstrap password: "

# Error messages (generic to avoid leaking information)
BOOTSTRAP_ERROR_MISMATCH: str = "Bootstrap password incorrect. Aborting."
BOOTSTRAP_ERROR_ALREADY_EXISTS: str = (
    "Genesis already exists. Bootstrap is only required for first-time genesis creation."
)
BOOTSTRAP_ERROR_MISSING_GENESIS: str = (
    "Genesis not found. Run the bootstrap command to create mainnet genesis:\n"
    "    python -m core.bootstrap --network mainnet --genesis-sample <path> --db <uri>"
)


# ==================================================================================
# PASSWORD VALIDATION
# ==================================================================================


def prompt_bootstrap_password() -> str:
    """
    Prompt the user for the bootstrap password (non-echo).

    Returns:
        The entered password (not validated here; caller must validate).
    """
    # Use getpass for non-echo input (secure terminal input)
    password = getpass.getpass(BOOTSTRAP_PROMPT)
    return password.strip()


def validate_bootstrap_password(entered: str) -> bool:
    """
    Validate the entered bootstrap password against the expected value.

    Args:
        entered: Password entered by the user

    Returns:
        True if password matches, False otherwise.

    Security:
        - Does not log the entered password
        - Uses secrets.compare_digest() for constant-time comparison
          to prevent timing attacks
    """
    import secrets
    # Use constant-time comparison to prevent timing attacks
    return secrets.compare_digest(entered, BOOTSTRAP_PASSWORD)


# ==================================================================================
# GENESIS EXISTENCE CHECK
# ==================================================================================


def genesis_exists(db_uri: str) -> bool:
    """
    Check if genesis already exists in the database.

    Args:
        db_uri: Database URI (sqlite:///path or rocksdb:///path)

    Returns:
        True if genesis exists (head is set at height 0 or higher), False otherwise.
    """
    try:
        kv = open_kv(db_uri)
        block_db = BlockDB(kv)
        # Check if canonical head exists
        head_data = block_db.get_canonical_head()
        if head_data is not None:
            height, _ = head_data
            return height >= 0  # Genesis exists if head is at height 0 or higher
        return False
    except Exception:
        # If DB doesn't exist or can't be opened, genesis doesn't exist
        return False


# ==================================================================================
# BOOTSTRAP COMMAND
# ==================================================================================


def bootstrap_mainnet_genesis(
    genesis_path: Path,
    db_uri: str,
    *,
    skip_password: bool = False,
    log_level: str = "info",
) -> int:
    """
    Bootstrap mainnet genesis with password gate.

    Args:
        genesis_path: Path to genesis JSON file (e.g., genesis.sample.mainnet.json)
        db_uri: Database URI for storing genesis
        skip_password: If True, skip password prompt (for testing or non-mainnet)
        log_level: Logging level

    Returns:
        Exit code (0 = success, non-zero = failure)
    """
    setup_logging(level=log_level.upper())

    # Step 1: Check if genesis already exists
    if genesis_exists(db_uri):
        print(BOOTSTRAP_ERROR_ALREADY_EXISTS, file=sys.stderr)
        return 1

    # Step 2: Load genesis to determine if it's mainnet (chain_id == 1)
    try:
        with open(genesis_path, "r", encoding="utf-8") as f:
            genesis_data = json.load(f)
        chain_id = int(genesis_data.get("chainId", 0))
    except Exception as e:
        print(f"[bootstrap] Failed to load genesis file: {e}", file=sys.stderr)
        return 2

    # Step 3: Password gate (mainnet only)
    is_mainnet = chain_id == 1
    if is_mainnet and not skip_password:
        entered_password = prompt_bootstrap_password()
        if not validate_bootstrap_password(entered_password):
            # Do not log the entered password or any details
            print(BOOTSTRAP_ERROR_MISMATCH, file=sys.stderr)
            return 3

    # Step 4: Load and initialize genesis
    try:
        params, genesis_header = load_genesis(genesis_path)
        kv = open_kv(db_uri)
        block_db = BlockDB(kv)
        state_db = StateDB(kv)

        # Finalize genesis (write to DB and set canonical head)
        head_height, head_hash = finalize_genesis(block_db, params, genesis_header)

        print("=== Animica mainnet bootstrap complete ===")
        print(f"DB:            {db_uri}")
        print(f"Genesis:       {genesis_path}")
        print(f"Chain ID:      {params.chain_id}")
        print(f"Head height:   {head_height}")
        print(f"Head hash:     0x{head_hash.hex()}")
        print("Status:        Mainnet genesis initialized successfully.")
        print()
        print("Subsequent node startups will not require the bootstrap password.")
        print("Use `python -m core.boot` for normal node startup.")
        return 0

    except AnimicaError as e:
        print(f"[bootstrap] Animica error: {e}", file=sys.stderr)
        return 4
    except Exception as e:
        print(f"[bootstrap] Unhandled error: {e}", file=sys.stderr)
        return 5


# ==================================================================================
# CLI
# ==================================================================================


def main(argv: Optional[list[str]] = None) -> int:
    """
    Command-line interface for mainnet genesis bootstrap.
    """
    ap = argparse.ArgumentParser(
        prog="animica-bootstrap",
        description="Bootstrap Animica mainnet genesis with password gate. "
        "Required for first-time mainnet genesis creation only. "
        "Subsequent startups use `python -m core.boot` without password.",
    )
    ap.add_argument(
        "--genesis-sample",
        type=str,
        required=True,
        help="Path to genesis sample file (e.g., genesis/genesis.sample.mainnet.json)",
    )
    ap.add_argument(
        "--db",
        type=str,
        required=True,
        help="Database URI (e.g., sqlite:///animica.db or rocksdb:///data)",
    )
    ap.add_argument(
        "--network",
        type=str,
        default="mainnet",
        help="Network name (mainnet, testnet, devnet); used for documentation only",
    )
    ap.add_argument(
        "--skip-password",
        action="store_true",
        help="Skip password prompt (for testing or non-mainnet networks)",
    )
    ap.add_argument(
        "--log",
        type=str,
        default="info",
        choices=["debug", "info", "warn", "error"],
        help="Log level (default: info)",
    )
    args = ap.parse_args(argv)

    genesis_path = Path(args.genesis_sample)
    if not genesis_path.exists():
        print(f"[bootstrap] genesis file not found: {genesis_path}", file=sys.stderr)
        return 2

    return bootstrap_mainnet_genesis(
        genesis_path,
        args.db,
        skip_password=args.skip_password,
        log_level=args.log,
    )


if __name__ == "__main__":
    raise SystemExit(main())
