# SPDX-License-Identifier: Apache-2.0
"""
tests.unit
==========

Small shared helpers for unit-test modules. Import from this package to keep
tests concise and consistent:

    from tests.unit import in_ci, env_flag, read_text_fixture, read_json_fixture

Paths
-----
- Repository root is inferred relative to this file.
- Common fixtures can live under `tests/fixtures/` and be loaded via helpers.

This module intentionally avoids test frameworks as hard dependencies.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Resolve repository root: tests/unit/__init__.py -> tests -> <root>
ROOT: Path = Path(__file__).resolve().parents[2]
FIXTURES: Path = ROOT / "tests" / "fixtures"

__all__ = [
    "ROOT",
    "FIXTURES",
    "in_ci",
    "env_flag",
    "read_text_fixture",
    "read_json_fixture",
]


def in_ci() -> bool:
    """Return True when running under a CI environment."""
    return os.getenv("CI", "").lower() in {"1", "true", "yes", "on"}


def env_flag(name: str, default: bool = False) -> bool:
    """
    Read a boolean-like environment variable.

    Truthy values: 1, true, yes, on (case-insensitive)
    Falsy values:  0, false, no, off (case-insensitive)
    If unset, returns `default`.
    """
    val = os.getenv(name)
    if val is None:
        return default
    v = val.strip().lower()
    if v in {"1", "true", "yes", "on", "y"}:
        return True
    if v in {"0", "false", "no", "off", "n"}:
        return False
    # Fallback: non-empty string considered truthy
    return bool(v)


def read_text_fixture(relpath: str) -> str:
    """
    Load a text fixture from `tests/fixtures/<relpath>` using UTF-8.

    Raises FileNotFoundError if the fixture does not exist.
    """
    path = (FIXTURES / relpath).resolve()
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def read_json_fixture(relpath: str) -> Any:
    """
    Load a JSON fixture from `tests/fixtures/<relpath>` and return the parsed object.
    """
    return json.loads(read_text_fixture(relpath))
