from __future__ import annotations

import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[3]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))
REPO_ROOT = PYTHON_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def set_test_chain_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Auto-use fixture to set chain ID to 1337 for all tests.
    
    This prevents conflicts when tests mock a node with chain ID 1337
    but the default network config has chain ID 1 (mainnet).
    
    Tests can override this by setting ANIMICA_CHAIN_ID explicitly.
    """
    # Only set if not already set (to allow test-specific overrides)
    import os
    if "ANIMICA_CHAIN_ID" not in os.environ:
        monkeypatch.setenv("ANIMICA_CHAIN_ID", "1337")
